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
import os
import ssl
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from .auth import AllowAllAuthenticator, Authenticator, DbAuthenticator
from .registry import PresenceRegistry
from .server import MuxServer

_UNSET = object()


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


def build_registry(log: Callable[[str], None] = _log) -> PresenceRegistry:
    return PresenceRegistry(
        on_connect=lambda entry: log(
            f"connected device={entry.device_id} reflexive={entry.reflexive_addr} "
            f"caps={entry.capabilities}"
        ),
        on_disconnect=lambda entry: log(f"disconnected device={entry.device_id}"),
    )


def build_server(
    config: EdgeConfig,
    *,
    authenticator: Optional[Authenticator] = None,
    registry: Optional[PresenceRegistry] = None,
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
        registry = build_registry(log)
    if ssl_context is _UNSET:
        ssl_context = build_ssl_context(config)
    return MuxServer(
        authenticator=authenticator,
        registry=registry,
        host=config.host,
        port=config.port,
        ssl_context=ssl_context,
        ping_interval=config.ping_interval,
        log=log,
    )


def main() -> None:
    config = EdgeConfig.from_env()
    server = build_server(config)
    tls_mode = "upstream" if config.allow_insecure and not config.tls_cert else "local"
    _log(
        f"starting edge mux server on {config.host}:{config.port} "
        f"(auth={config.auth_mode}, tls={tls_mode})"
    )
    asyncio.run(server.serve_forever())


if __name__ == "__main__":
    main()
