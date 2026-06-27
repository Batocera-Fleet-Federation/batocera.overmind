"""Mux wire protocol codec (Edge side).

This is a byte-compatible mirror of the Drone's ``app/transport/mux.py`` in the
``batocera.drone`` repo. The two repos are versioned independently, so -- like
``overmind_contract.py`` mirrors ``models.py`` -- this is kept in sync by hand.
A golden-vector test in each repo pins the exact bytes so drift is caught.

Wire format -- every frame is::

    +----------+---------------------+========================+
    | kind     | length (uint32, BE) | payload (length bytes) |
    | (1 byte) |                     |                        |
    +----------+---------------------+========================+
"""

from __future__ import annotations

import json
import struct
from typing import Any, Callable, Mapping, Tuple

# Frame kinds (first header byte).
FRAME_CONTROL = 0x01  # JSON control message
FRAME_DATA = 0x02  # binary payload (relay chunk data)

#: Hard cap on a single frame payload (16 MiB) so a malformed/hostile length can
#: never make a peer attempt a huge allocation.
MAX_FRAME_PAYLOAD = 16 * 1024 * 1024

_HEADER = struct.Struct(">BI")  # kind (uint8), length (uint32 big-endian)
HEADER_SIZE = _HEADER.size

# Control message ``type`` values (single source of truth shared with the Drone).
MSG_HELLO = "hello"
MSG_HELLO_ACK = "hello_ack"
MSG_PING = "ping"
MSG_PONG = "pong"
MSG_PRESENCE = "presence"
MSG_BYE = "bye"
MSG_ERROR = "error"


class MuxProtocolError(Exception):
    """Raised on a malformed frame, oversized payload, or bad control JSON."""


def encode_frame(kind: int, payload: bytes) -> bytes:
    """Encode a single frame (header + payload)."""
    if kind < 0 or kind > 0xFF:
        raise MuxProtocolError(f"invalid frame kind: {kind}")
    if len(payload) > MAX_FRAME_PAYLOAD:
        raise MuxProtocolError(f"frame payload too large: {len(payload)} > {MAX_FRAME_PAYLOAD}")
    return _HEADER.pack(kind, len(payload)) + payload


def encode_control(message: Mapping[str, Any]) -> bytes:
    """Encode a CONTROL frame from a JSON-serializable mapping with a 'type'."""
    if "type" not in message:
        raise MuxProtocolError("control message requires a 'type' field")
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return encode_frame(FRAME_CONTROL, payload)


def decode_control(payload: bytes) -> dict:
    """Decode a CONTROL frame payload into a dict (must be a JSON object)."""
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise MuxProtocolError(f"invalid control JSON: {error}") from error
    if not isinstance(message, dict):
        raise MuxProtocolError("control message must be a JSON object")
    return message


def read_frame(read_exactly: Callable[[int], bytes]) -> Tuple[int, bytes]:
    """Read one frame using a ``read_exactly(n) -> bytes`` callable.

    Returns ``(kind, payload)``. Raises :class:`MuxProtocolError` on a truncated
    stream or oversized frame, and :class:`EOFError` at a clean frame boundary.
    """
    header = read_exactly(HEADER_SIZE)
    if not header:
        raise EOFError("connection closed at frame boundary")
    if len(header) != HEADER_SIZE:
        raise MuxProtocolError("truncated frame header")
    kind, length = _HEADER.unpack(header)
    if length > MAX_FRAME_PAYLOAD:
        raise MuxProtocolError(f"declared frame payload too large: {length}")
    if length == 0:
        return kind, b""
    payload = read_exactly(length)
    if len(payload) != length:
        raise MuxProtocolError("truncated frame payload")
    return kind, payload


async def read_frame_async(reader: Any) -> Tuple[int, bytes]:
    """Read one frame from an :class:`asyncio.StreamReader`.

    Uses ``readexactly`` and translates its EOF into the same ``EOFError`` the
    sync :func:`read_frame` raises at a clean boundary.
    """
    import asyncio

    try:
        header = await reader.readexactly(HEADER_SIZE)
    except asyncio.IncompleteReadError as error:
        if not error.partial:
            raise EOFError("connection closed at frame boundary") from error
        raise MuxProtocolError("truncated frame header") from error
    kind, length = _HEADER.unpack(header)
    if length > MAX_FRAME_PAYLOAD:
        raise MuxProtocolError(f"declared frame payload too large: {length}")
    if length == 0:
        return kind, b""
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as error:
        raise MuxProtocolError("truncated frame payload") from error
    return kind, payload
