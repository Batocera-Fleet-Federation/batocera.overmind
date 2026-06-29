"""Edge service entrypoint.

Builds configuration and components from the environment and runs the asyncio mux
server. Run as ``python -m overmind.edge.edge_app``.

In the hosted deployment this is the Fargate task command. For self-hosters it
can run in the same image as the REST app (separate process/port). TLS is either
terminated here (``EDGE_TLS_CERT`` + ``EDGE_TLS_KEY``) or upstream by a load
balancer (``EDGE_ALLOW_INSECURE=1`` -- the connection from the LB to the task is
inside the private network).

The builders are split out so they can be unit-tested without binding sockets or
requiring a database.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import socket
import ssl
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from .auth import AllowAllAuthenticator, Authenticator, DbAuthenticator
from .registry import PresenceRegistry
from .relay import RelayHub
from .server import MuxServer
from .stun import start_stun_reflector

_UNSET = object()

#: (device_id, online, reflexive_endpoint) -> None
PresenceWriter = Callable[[str, bool, Optional[str]], None]

#: (session_id, status) -> Awaitable[None] -- relay lifecycle reporter
TransferStatusWriter = Callable[[str, str], "asyncio.Future"]


def _env_bool(default: bool, *names: str) -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


@dataclass
class EdgeConfig:
    host: str = "0.0.0.0"
    port: int = 9443
    tls_cert: Optional[str] = None
    tls_key: Optional[str] = None
    tls_self_signed: bool = False
    allow_insecure: bool = False
    ping_interval: float = 20.0
    auth_mode: str = "db"  # "db" | "allow-all" (dev only)
    node_id: str = ""  # identifies this Edge node (for cross-node presence, Phase 2)
    relay_bw_limit_bps: float = 0  # per-session relay rate cap; 0 = unlimited
    transfer_secret: Optional[str] = None  # shared SECRET_KEY for token validation
    stun_port: int = 9444  # UDP STUN reflector port for hole punching; 0 disables

    @classmethod
    def from_env(cls) -> "EdgeConfig":
        return cls(
            host=os.environ.get("EDGE_HOST", "0.0.0.0"),
            port=int(os.environ.get("EDGE_PORT", "9443")),
            tls_cert=(os.environ.get("EDGE_TLS_CERT") or "").strip() or None,
            tls_key=(os.environ.get("EDGE_TLS_KEY") or "").strip() or None,
            tls_self_signed=_env_bool(False, "EDGE_TLS_SELF_SIGNED"),
            allow_insecure=_env_bool(False, "EDGE_ALLOW_INSECURE"),
            ping_interval=float(os.environ.get("EDGE_PING_INTERVAL", "20")),
            auth_mode=(os.environ.get("EDGE_AUTH") or "db").strip().lower(),
            node_id=(os.environ.get("EDGE_NODE_ID") or socket.gethostname()),
            relay_bw_limit_bps=float(os.environ.get("EDGE_RELAY_BW_LIMIT_BPS", "0")),
            transfer_secret=(
                os.environ.get("EDGE_TRANSFER_SECRET") or os.environ.get("SECRET_KEY") or ""
            ).strip()
            or None,
            stun_port=int(os.environ.get("EDGE_STUN_PORT", "9444")),
        )


def build_ssl_context(config: EdgeConfig) -> Optional[ssl.SSLContext]:
    """Build the server-side TLS context, or None when TLS is terminated upstream."""
    cert, key = config.tls_cert, config.tls_key
    if not (cert and key) and config.tls_self_signed:
        # Self-signed is for local/dev (and self-host without a real cert); Drones
        # connect with verification disabled. Reuses Overmind's generator.
        from overmind.tls_server import ensure_self_signed_cert

        gen_key, gen_cert = ensure_self_signed_cert()
        if gen_key and gen_cert:
            cert, key = str(gen_cert), str(gen_key)
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=cert, keyfile=key)
        return context
    if config.allow_insecure:
        return None
    raise RuntimeError(
        "Edge TLS not configured: set EDGE_TLS_CERT + EDGE_TLS_KEY, "
        "EDGE_TLS_SELF_SIGNED=1 (dev/self-host), or EDGE_ALLOW_INSECURE=1 when TLS "
        "is terminated upstream (e.g. by a load balancer)."
    )


def _default_device_lookup(device_id: str) -> Optional[dict]:
    # Lazy import so importing this module (and unit-testing the builders) does
    # not require psycopg or a configured database.
    from overmind.postgres_store import postgres_store

    return postgres_store.get_device_by_device_id(device_id)


def build_authenticator(
    config: EdgeConfig, *, lookup_device: Optional[Callable[[str], Optional[dict]]] = None
) -> Authenticator:
    if config.auth_mode == "allow-all":
        return AllowAllAuthenticator()
    return DbAuthenticator(lookup_device or _default_device_lookup)


def _log(message: str) -> None:
    print(f"[edge] {message}", file=sys.stdout, flush=True)


def make_db_presence_writer(
    *,
    edge_node: Optional[str] = None,
    executor: Optional[concurrent.futures.Executor] = None,
    log: Callable[[str], None] = _log,
) -> PresenceWriter:
    """Return a presence writer that persists Edge presence to Postgres off the
    event loop. Best-effort: failures are logged, never raised, so a flaky DB can
    never break a Drone's connection."""
    own_executor = executor or concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="edge-presence"
    )

    def write(device_id: str, online: bool, reflexive_endpoint: Optional[str]) -> None:
        def task() -> None:
            try:
                from overmind.postgres_store import postgres_store

                postgres_store.update_device_edge_presence(
                    device_id,
                    online=online,
                    edge_node=edge_node,
                    reflexive_endpoint=reflexive_endpoint,
                )
            except Exception as error:  # noqa: BLE001 -- best-effort persistence
                log(f"presence persist failed for device={device_id}: {error}")

        own_executor.submit(task)

    return write


def make_db_transfer_status_writer(
    *, log: Callable[[str], None] = _log
) -> TransferStatusWriter:
    """Return an async reporter that records relay transfer lifecycle
    transitions (active/completed/aborted) to the transfer_sessions table off
    the event loop. Best-effort: failures are logged, never raised."""

    def _persist(session_id: str, status: str) -> None:
        try:
            from overmind.postgres_store import postgres_store

            postgres_store.update_transfer_session(session_id, status=status)
        except Exception as error:  # noqa: BLE001 -- best-effort persistence
            log(f"transfer status persist failed for session={session_id}: {error}")

    async def write(session_id: str, status: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _persist, session_id, status)

    return write


def build_registry(
    log: Callable[[str], None] = _log, presence_writer: Optional[PresenceWriter] = None
) -> PresenceRegistry:
    def on_connect(entry) -> None:
        log(
            f"connected device={entry.device_id} reflexive={entry.reflexive_addr} "
            f"caps={entry.capabilities}"
        )
        if presence_writer is not None:
            presence_writer(entry.device_id, True, entry.reflexive_addr)

    def on_disconnect(entry) -> None:
        log(f"disconnected device={entry.device_id}")
        if presence_writer is not None:
            presence_writer(entry.device_id, False, entry.reflexive_addr)

    return PresenceRegistry(on_connect=on_connect, on_disconnect=on_disconnect)


def build_server(
    config: EdgeConfig,
    *,
    authenticator: Optional[Authenticator] = None,
    registry: Optional[PresenceRegistry] = None,
    relay: Optional[RelayHub] = None,
    presence_writer: Optional[PresenceWriter] = None,
    transfer_status: Optional[TransferStatusWriter] = None,
    ssl_context: object = _UNSET,
    log: Callable[[str], None] = _log,
) -> MuxServer:
    """Assemble a :class:`MuxServer` from config + (optionally injected) parts.

    ``ssl_context`` defaults to building one from config; pass an explicit value
    (including ``None``) to override -- tests pass ``None`` to skip TLS.
    """
    # Use explicit None checks, not `x or default`: a PresenceRegistry is falsy
    # when empty (it defines __len__), which would silently drop an injected one.
    if authenticator is None:
        authenticator = build_authenticator(config)
    if registry is None:
        registry = build_registry(log, presence_writer=presence_writer)
    if relay is None:
        relay = RelayHub(bw_limit_bps=config.relay_bw_limit_bps)
    if ssl_context is _UNSET:
        ssl_context = build_ssl_context(config)
    return MuxServer(
        authenticator=authenticator,
        registry=registry,
        relay=relay,
        transfer_secret=config.transfer_secret,
        transfer_status=transfer_status,
        host=config.host,
        port=config.port,
        ssl_context=ssl_context,
        ping_interval=config.ping_interval,
        log=log,
    )


async def _serve(config: EdgeConfig, server: MuxServer) -> None:
    """Run the mux server plus the UDP STUN reflector (for hole punching)."""
    stun_transport = None
    if config.stun_port:
        try:
            stun_transport = await start_stun_reflector(config.host, config.stun_port, log=_log)
        except OSError as error:
            _log(f"STUN reflector failed to bind on :{config.stun_port}: {error}")
    try:
        await server.serve_forever()
    finally:
        if stun_transport is not None:
            stun_transport.close()


def main() -> None:
    config = EdgeConfig.from_env()
    presence_writer = make_db_presence_writer(edge_node=config.node_id)
    transfer_status = make_db_transfer_status_writer()
    server = build_server(
        config, presence_writer=presence_writer, transfer_status=transfer_status
    )
    tls_mode = "upstream" if config.allow_insecure and not config.tls_cert else "local"
    _log(
        f"starting edge mux server on {config.host}:{config.port} "
        f"(auth={config.auth_mode}, tls={tls_mode}, stun={config.stun_port or 'off'})"
    )
    asyncio.run(_serve(config, server))


if __name__ == "__main__":
    main()
