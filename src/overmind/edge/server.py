"""Asyncio TLS mux server: terminates Drone outbound connections.

Each connected Drone gets one coroutine running :meth:`MuxServer.handle_connection`.
The flow is:

1. Read the first frame; it must be a HELLO. Authenticate ``(device_id, token)``.
2. Register presence and reply HELLO_ACK with a session id and the Drone's
   reflexive address (the source ip:port the Edge observed -- this is the
   STUN-like signal later phases use for hole punching).
3. Serve the connection: answer PINGs with PONGs, keep ``last_seen`` fresh, and
   probe liveness with our own PINGs when the link goes quiet.
4. On disconnect, deregister presence.

The server uses stdlib ``asyncio`` (no extra deps) and scales to many idle
persistent connections far better than a thread-per-connection model. Only the
per-connection coroutine writes to its own ``writer``, so no write lock is needed
in Phase 1 (the relay adds one in Phase 2 when multiple coroutines share a writer).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Awaitable, Callable, Optional

from . import protocol
from .auth import Authenticator
from .registry import PresenceEntry, PresenceRegistry


def _noop(_message: str) -> None:
    return None


class MuxServer:
    def __init__(
        self,
        *,
        authenticator: Authenticator,
        registry: PresenceRegistry,
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
        session_id = uuid.uuid4().hex
        reflexive_addr = self._peername(writer)
        device_id: Optional[str] = None
        try:
            device_id = await self._handshake(reader, writer, session_id, reflexive_addr)
            if device_id is None:
                return
            await self._serve(reader, writer, device_id)
        except (EOFError, ConnectionError, asyncio.IncompleteReadError) as error:
            self._log(f"edge mux connection closed ({device_id}): {error}")
        except protocol.MuxProtocolError as error:
            self._log(f"edge mux protocol error ({device_id}): {error}")
        except Exception as error:  # noqa: BLE001 -- never let one connection crash the server
            self._log(f"edge mux handler error ({device_id}): {error}")
        finally:
            if device_id is not None:
                self._registry.deregister(device_id, session_id)
            await self._close(writer)

    async def _handshake(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        session_id: str,
        reflexive_addr: Optional[str],
    ) -> Optional[str]:
        kind, payload = await protocol.read_frame_async(reader)
        if kind != protocol.FRAME_CONTROL:
            await self._send_error(writer, "handshake must be a control frame")
            return None
        message = protocol.decode_control(payload)
        if message.get("type") != protocol.MSG_HELLO:
            await self._send_error(writer, "expected hello")
            return None
        device_id = str(message.get("device_id") or "").strip()
        token = str(message.get("token") or "")
        authorized = await self._authenticate(device_id, token)
        if not authorized:
            await self._send_error(writer, "unauthorized")
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
        await self._send_control(
            writer,
            {
                "type": protocol.MSG_HELLO_ACK,
                "session_id": session_id,
                "reflexive_addr": reflexive_addr,
                "ping_interval": self._ping_interval,
            },
        )
        self._log(f"edge mux connected: device={device_id} reflexive={reflexive_addr}")
        return device_id

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, device_id: str
    ) -> None:
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
                await self._send_control(writer, {"type": protocol.MSG_PING, "t": self._now()})
                continue
            missed = 0
            self._registry.touch(device_id)
            if kind != protocol.FRAME_CONTROL:
                continue  # DATA frames are handled by the relay (Phase 2)
            message = protocol.decode_control(payload)
            message_type = message.get("type")
            if message_type == protocol.MSG_PING:
                await self._send_control(writer, {"type": protocol.MSG_PONG, "t": message.get("t")})
            elif message_type == protocol.MSG_BYE:
                return
            # PONG and anything else: liveness already refreshed via touch().

    async def _authenticate(self, device_id: str, token: str) -> bool:
        # Auth may hit the DB; run it off the event loop so one slow lookup does
        # not stall other connections.
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._authenticator.authenticate, device_id, token)

    async def _send(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(data)
        await writer.drain()

    async def _send_control(self, writer: asyncio.StreamWriter, message: dict) -> None:
        await self._send(writer, protocol.encode_control(message))

    async def _send_error(self, writer: asyncio.StreamWriter, reason: str) -> None:
        try:
            await self._send_control(writer, {"type": protocol.MSG_ERROR, "reason": reason})
        except (ConnectionError, OSError):
            pass

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
