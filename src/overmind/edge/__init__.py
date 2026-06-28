"""Overmind Edge service: the always-on data-plane coordinator.

Overmind's REST API runs on Lambda (request/response) and so cannot hold the
persistent connections or stream relayed bytes that the outbound-only networking
model needs. The Edge is a separate, always-on process (ECS Fargate in the
hosted deployment; co-located in the container for self-hosters) that:

* terminates each Drone's single persistent **outbound** mux connection,
* tracks presence (who is online) and reports each Drone its reflexive
  (NAT-observed) address,
* brokers transfer signaling between Drones, and
* relays file bytes end-to-end only when a direct path cannot be established.

This package reuses Overmind's CA (:mod:`overmind.drone_ca`), token verification
(:mod:`overmind.drone_security`), Redis client (:mod:`overmind.cache`) and
relational store (:mod:`overmind.postgres_store`) so the control plane and the
Edge share one source of truth for identity and device records.

Phase 1 scope: the mux server, authentication, presence, and keepalive. The
signaling broker and relay land in Phase 2.
"""

from .protocol import (
    FRAME_CONTROL,
    FRAME_DATA,
    MAX_FRAME_PAYLOAD,
    MSG_BYE,
    MSG_ERROR,
    MSG_HELLO,
    MSG_HELLO_ACK,
    MSG_PING,
    MSG_PONG,
    MSG_PRESENCE,
    MSG_RELAY_CLOSE,
    MSG_RELAY_OPEN,
    MSG_RELAY_READY,
    MSG_TRANSFER_ERROR,
    MSG_TRANSFER_OFFER,
    MSG_TRANSFER_REQUEST,
    RELAY_SESSION_ID_LEN,
    MuxProtocolError,
    decode_control,
    encode_control,
    encode_frame,
    encode_relay_data,
    parse_relay_data,
    read_frame,
)
from .auth import Authenticator, DbAuthenticator
from .registry import PresenceEntry, PresenceRegistry
from .relay import RateLimiter, RelayHub, RelayLeg, RelaySession

__all__ = [
    "FRAME_CONTROL",
    "FRAME_DATA",
    "MAX_FRAME_PAYLOAD",
    "MSG_BYE",
    "MSG_ERROR",
    "MSG_HELLO",
    "MSG_HELLO_ACK",
    "MSG_PING",
    "MSG_PONG",
    "MSG_PRESENCE",
    "MSG_RELAY_CLOSE",
    "MSG_RELAY_OPEN",
    "MSG_RELAY_READY",
    "MSG_TRANSFER_ERROR",
    "MSG_TRANSFER_OFFER",
    "MSG_TRANSFER_REQUEST",
    "RELAY_SESSION_ID_LEN",
    "MuxProtocolError",
    "decode_control",
    "encode_control",
    "encode_frame",
    "encode_relay_data",
    "parse_relay_data",
    "read_frame",
    "Authenticator",
    "DbAuthenticator",
    "PresenceEntry",
    "PresenceRegistry",
    "RateLimiter",
    "RelayHub",
    "RelayLeg",
    "RelaySession",
]
