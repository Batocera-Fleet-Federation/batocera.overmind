"""Asyncio TLS mux server: terminates Drone outbound connections.

Each connected Drone gets one coroutine running :meth:`MuxServer.handle_connection`:

1. Read the first frame; it must be a HELLO. Authenticate ``(device_id, token)``.
2. Register presence and reply HELLO_ACK with a session id and the Drone's
   reflexive address (the source ip:port the Edge observed -- the STUN-like
   signal later phases use for hole punching).
3. Serve the connection: answer PINGs, keep ``last_seen`` fresh, probe liveness,
   and -- for relayed transfers -- pair legs and forward DATA frames between two
   Drones (see :mod:`overmind.edge.relay`).
4. On disconnect, deregister presence and tear down any relay sessions.

Uses stdlib ``asyncio`` (no extra deps) and scales to many idle persistent
connections far better than thread-per-connection. Relay forwarding means one
connection's coroutine writes to *another* connection's writer, so each
connection wraps its writer in a :class:`_Connection` with a write lock.
"""

from __future__ import annotations

import asyncio
import itertools
import time
import uuid
from typing import Callable, Dict, Optional

from overmind.transfer_tokens import verify_transfer_token

from . import protocol
from .auth import Authenticator
from .registry import PresenceEntry, PresenceRegistry
from .relay import RelayHub


def _noop(_message: str) -> None:
    return None


class _Connection:
    """A mux connection's writer plus a lock, so cross-connection relay writes
    never interleave frames on the same socket."""

    _ids = itertools.count(1)

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.id = next(self._ids)
        self._lock = asyncio.Lock()

    async def send(self, data: bytes) -> None:
        async with self._lock:
            self.writer.write(data)
            await self.writer.drain()

    async def send_control(self, message: dict) -> None:
        await self.send(protocol.encode_control(message))

    async def send_error(self, reason: str) -> None:
        try:
            await self.send_control({"type": protocol.MSG_ERROR, "reason": reason})
        except (ConnectionError, OSError):
            pass


class MuxServer:
    def __init__(
        self,
        *,
        authenticator: Authenticator,
        registry: PresenceRegistry,
        relay: Optional[RelayHub] = None,
        transfer_secret: Optional[str] = None,
        host: str = "0.0.0.0",
        port: int = 9443,
        ssl_context=None,
        ping_interval: float = 20.0,
        max_missed_pings: int = 3,
        now: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = _noop,
    ) -> None:
        self._authenticator = authenticator
        self._registry = registry
        self._relay = relay if relay is not None else RelayHub()
        # Shared SECRET_KEY for offline transfer-token validation. When None
        # (local dev / allow-all), tokens are not enforced.
        self._transfer_secret = transfer_secret
        # device_id -> live connection, for pushing TRANSFER_OFFER to a sender.
        self._connections: Dict[str, _Connection] = {}
        self._host = host
        self._port = port
        self._ssl_context = ssl_context
        self._ping_interval = max(1.0, float(ping_interval))
        self._max_missed_pings = max(1, int(max_missed_pings))
        self._now = now
        self._log = log

    async def serve_forever(self) -> None:
        server = await asyncio.start_server(
            self.handle_connection, self._host, self._port, ssl=self._ssl_context
        )
        sockets = ", ".join(str(sock.getsockname()) for sock in (server.sockets or []))
        self._log(f"edge mux server listening on {sockets}")
        async with server:
            await server.serve_forever()

    async def handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = _Connection(writer)
        session_id = uuid.uuid4().hex
        reflexive_addr = self._peername(writer)
        device_id: Optional[str] = None
        try:
            device_id = await self._handshake(reader, conn, session_id, reflexive_addr)
            if device_id is None:
                return
            await self._serve(reader, conn, device_id)
        except (EOFError, ConnectionError, asyncio.IncompleteReadError) as error:
            self._log(f"edge mux connection closed ({device_id}): {error}")
        except protocol.MuxProtocolError as error:
            self._log(f"edge mux protocol error ({device_id}): {error}")
        except Exception as error:  # noqa: BLE001 -- never let one connection crash the server
            self._log(f"edge mux handler error ({device_id}): {error}")
        finally:
            await self._teardown_relay(conn)
            if device_id is not None:
                self._registry.deregister(device_id, session_id)
                if self._connections.get(device_id) is conn:
                    del self._connections[device_id]
            await self._close(writer)

    async def _handshake(
        self,
        reader: asyncio.StreamReader,
        conn: _Connection,
        session_id: str,
        reflexive_addr: Optional[str],
    ) -> Optional[str]:
        kind, payload = await protocol.read_frame_async(reader)
        if kind != protocol.FRAME_CONTROL:
            await conn.send_error("handshake must be a control frame")
            return None
        message = protocol.decode_control(payload)
        if message.get("type") != protocol.MSG_HELLO:
            await conn.send_error("expected hello")
            return None
        device_id = str(message.get("device_id") or "").strip()
        token = str(message.get("token") or "")
        authorized = await self._authenticate(device_id, token)
        if not authorized:
            await conn.send_error("unauthorized")
            self._log(f"edge mux auth rejected for device {device_id!r}")
            return None
        entry = PresenceEntry(
            device_id=device_id,
            session_id=session_id,
            reflexive_addr=reflexive_addr,
            capabilities=list(message.get("capabilities") or []),
            lan_addrs=list(message.get("lan_addrs") or []),
        )
        self._registry.register(entry)
        self._connections[device_id] = conn  # newest connection wins (reconnect)
        await conn.send_control(
            {
                "type": protocol.MSG_HELLO_ACK,
                "session_id": session_id,
                "reflexive_addr": reflexive_addr,
                "ping_interval": self._ping_interval,
            }
        )
        self._log(f"edge mux connected: device={device_id} reflexive={reflexive_addr}")
        return device_id

    async def _serve(self, reader: asyncio.StreamReader, conn: _Connection, device_id: str) -> None:
        missed = 0
        while True:
            try:
                kind, payload = await asyncio.wait_for(
                    protocol.read_frame_async(reader), timeout=self._ping_interval
                )
            except asyncio.TimeoutError:
                missed += 1
                if missed >= self._max_missed_pings:
                    self._log(f"edge mux idle timeout: device={device_id}")
                    return
                await conn.send_control({"type": protocol.MSG_PING, "t": self._now()})
                continue
            missed = 0
            self._registry.touch(device_id)
            if kind == protocol.FRAME_DATA:
                await self._relay_data(conn, payload)
                continue
            message = protocol.decode_control(payload)
            if await self._handle_control(conn, device_id, message):
                return  # BYE

    async def _relay_data(self, conn: _Connection, payload: bytes) -> None:
        try:
            session_id, _ = protocol.parse_relay_data(payload)
        except protocol.MuxProtocolError:
            return  # malformed relay frame; ignore
        await self._relay.forward(session_id, conn.id, payload)

    async def _handle_control(self, conn: _Connection, device_id: str, message: dict) -> bool:
        """Handle one control message. Returns True if the connection should close."""
        message_type = message.get("type")
        if message_type == protocol.MSG_PING:
            await conn.send_control({"type": protocol.MSG_PONG, "t": message.get("t")})
        elif message_type == protocol.MSG_BYE:
            return True
        elif message_type == protocol.MSG_RELAY_OPEN:
            await self._relay_open(conn, device_id, message)
        elif message_type == protocol.MSG_RELAY_CLOSE:
            await self._relay_close(conn, message)
        elif message_type == protocol.MSG_TRANSFER_REQUEST:
            await self._transfer_request(conn, device_id, message)
        # PONG and anything else: liveness already refreshed via touch().
        return False

    async def _transfer_request(self, conn: _Connection, device_id: str, message: dict) -> None:
        """Receiver asks to pull an asset: validate the token (offline, with the
        shared secret) and push a TRANSFER_OFFER to the sender's mux."""
        session_id = str(message.get("session_id") or "")
        token = str(message.get("token") or "")
        from_device = str(message.get("from_device") or "")
        asset = message.get("asset") if isinstance(message.get("asset"), dict) else {}

        if self._transfer_secret:
            payload = verify_transfer_token(self._transfer_secret, token)
            if payload is None:
                await self._transfer_error(conn, session_id, "invalid or expired token")
                return
            # The token binds the session, both peers, and the asset.
            if (
                payload.get("sid") != session_id
                or payload.get("to") != device_id
                or payload.get("from") != from_device
            ):
                await self._transfer_error(conn, session_id, "token does not match request")
                return
            asset = payload.get("asset") if isinstance(payload.get("asset"), dict) else asset

        sender = self._connections.get(from_device)
        if sender is None:
            await self._transfer_error(conn, session_id, "sender is offline")
            return
        try:
            await sender.send_control(
                {
                    "type": protocol.MSG_TRANSFER_OFFER,
                    "session_id": session_id,
                    "token": token,
                    "from_device": from_device,
                    "to_device": device_id,
                    "asset": asset,
                }
            )
        except (ConnectionError, OSError):
            await self._transfer_error(conn, session_id, "could not reach sender")
            return
        self._log(f"transfer offered: session={session_id} from={from_device} to={device_id}")

    async def _transfer_error(self, conn: _Connection, session_id: str, reason: str) -> None:
        try:
            await conn.send_control(
                {"type": protocol.MSG_TRANSFER_ERROR, "session_id": session_id, "reason": reason}
            )
        except (ConnectionError, OSError):
            pass

    async def _relay_open(self, conn: _Connection, device_id: str, message: dict) -> None:
        session_id = str(message.get("session_id") or "")
        role = str(message.get("role") or "")
        if not session_id or role not in ("sender", "receiver"):
            await conn.send_error("invalid relay_open")
            return
        session = self._relay.open_leg(session_id, role, device_id, conn.id, conn.send)
        self._log(f"relay leg open: session={session_id} role={role} device={device_id}")
        if session.is_ready():
            for leg in list(session.legs.values()):
                try:
                    await leg.send(
                        protocol.encode_control(
                            {"type": protocol.MSG_RELAY_READY, "session_id": session_id}
                        )
                    )
                except (ConnectionError, OSError):
                    pass

    async def _relay_close(self, conn: _Connection, message: dict) -> None:
        session_id = str(message.get("session_id") or "")
        for leg in self._relay.close_session(session_id):
            if leg.conn_id == conn.id:
                continue
            try:
                await leg.send(
                    protocol.encode_control(
                        {"type": protocol.MSG_RELAY_CLOSE, "session_id": session_id}
                    )
                )
            except (ConnectionError, OSError):
                pass

    async def _teardown_relay(self, conn: _Connection) -> None:
        for session_id, peer in self._relay.drop_connection(conn.id):
            try:
                await peer.send(
                    protocol.encode_control(
                        {"type": protocol.MSG_RELAY_CLOSE, "session_id": session_id}
                    )
                )
            except (ConnectionError, OSError):
                pass

    async def _authenticate(self, device_id: str, token: str) -> bool:
        # Auth may hit the DB; run it off the event loop so one slow lookup does
        # not stall other connections.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._authenticator.authenticate, device_id, token)

    @staticmethod
    def _peername(writer: asyncio.StreamWriter) -> Optional[str]:
        peer = writer.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= 2:
            return f"{peer[0]}:{peer[1]}"
        return None

    @staticmethod
    async def _close(writer: asyncio.StreamWriter) -> None:
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
