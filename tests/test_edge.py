"""Tests for the Edge service foundations: protocol codec, registry, auth."""

import asyncio
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.edge import protocol
from overmind.edge.auth import AllowAllAuthenticator, DbAuthenticator
from overmind.edge.registry import PresenceEntry, PresenceRegistry
from overmind.edge.server import MuxServer


def _reader(data: bytes):
    buf = io.BytesIO(data)

    def read_exactly(n: int) -> bytes:
        return buf.read(n)

    return read_exactly


class ProtocolTests(unittest.TestCase):
    def test_control_round_trip(self):
        msg = {"type": protocol.MSG_HELLO_ACK, "session_id": "s1", "reflexive_addr": "1.2.3.4:5"}
        frame = protocol.encode_control(msg)
        kind, payload = protocol.read_frame(_reader(frame))
        self.assertEqual(kind, protocol.FRAME_CONTROL)
        self.assertEqual(protocol.decode_control(payload), msg)

    def test_golden_vector_matches_drone_wire_format(self):
        # This exact byte string must also be produced by the Drone's mux codec.
        # If either side changes the framing/JSON separators, this breaks first.
        self.assertEqual(
            protocol.encode_control({"type": "ping"}),
            b"\x01\x00\x00\x00\x0f" + b'{"type":"ping"}',
        )

    def test_requires_type(self):
        with self.assertRaises(protocol.MuxProtocolError):
            protocol.encode_control({"session_id": "x"})

    def test_oversized_declared_length_rejected(self):
        import struct

        header = struct.pack(">BI", protocol.FRAME_DATA, protocol.MAX_FRAME_PAYLOAD + 1)
        with self.assertRaises(protocol.MuxProtocolError):
            protocol.read_frame(_reader(header))

    def test_eof_at_boundary(self):
        with self.assertRaises(EOFError):
            protocol.read_frame(_reader(b""))


class RegistryTests(unittest.TestCase):
    def _clock(self):
        state = {"t": 100.0}

        def now():
            return state["t"]

        return state, now

    def test_register_and_get(self):
        connects, disconnects = [], []
        reg = PresenceRegistry(on_connect=connects.append, on_disconnect=disconnects.append)
        entry = PresenceEntry(device_id="d1", session_id="s1", capabilities=["relay"])
        reg.register(entry)
        self.assertTrue(reg.is_online("d1"))
        self.assertEqual(reg.online_ids(), ["d1"])
        self.assertEqual(len(connects), 1)
        self.assertEqual(reg.get("d1").capabilities, ["relay"])

    def test_connected_at_and_last_seen_set_from_clock(self):
        state, now = self._clock()
        reg = PresenceRegistry(now=now)
        entry = PresenceEntry(device_id="d1", session_id="s1")
        reg.register(entry)
        self.assertEqual(reg.get("d1").connected_at, 100.0)
        state["t"] = 150.0
        reg.touch("d1")
        self.assertEqual(reg.get("d1").last_seen, 150.0)

    def test_deregister_session_guard(self):
        disconnects = []
        reg = PresenceRegistry(on_disconnect=disconnects.append)
        reg.register(PresenceEntry(device_id="d1", session_id="s-new"))
        # A stale socket (older session) closing must not evict the newer session.
        self.assertFalse(reg.deregister("d1", session_id="s-old"))
        self.assertTrue(reg.is_online("d1"))
        self.assertEqual(disconnects, [])
        # Matching session removes it and fires the hook.
        self.assertTrue(reg.deregister("d1", session_id="s-new"))
        self.assertFalse(reg.is_online("d1"))
        self.assertEqual(len(disconnects), 1)

    def test_reconnect_replaces_entry(self):
        reg = PresenceRegistry()
        reg.register(PresenceEntry(device_id="d1", session_id="s1"))
        reg.register(PresenceEntry(device_id="d1", session_id="s2"))
        self.assertEqual(len(reg), 1)
        self.assertEqual(reg.get("d1").session_id, "s2")

    def test_snapshot_shape(self):
        reg = PresenceRegistry()
        reg.register(PresenceEntry(device_id="d1", session_id="s1", reflexive_addr="9.9.9.9:1"))
        snap = reg.snapshot()
        self.assertEqual(snap[0]["device_id"], "d1")
        self.assertEqual(snap[0]["reflexive_addr"], "9.9.9.9:1")


class AuthTests(unittest.TestCase):
    def test_db_authenticator_accepts_valid_token(self):
        devices = {"d1": {"drone_token_hash": "HASH"}}
        auth = DbAuthenticator(
            devices.get, verify=lambda token, stored: token == "good" and stored == "HASH"
        )
        self.assertTrue(auth.authenticate("d1", "good"))
        self.assertFalse(auth.authenticate("d1", "bad"))

    def test_db_authenticator_rejects_unknown_device(self):
        auth = DbAuthenticator(lambda device_id: None)
        self.assertFalse(auth.authenticate("nope", "tok"))

    def test_db_authenticator_rejects_empty(self):
        auth = DbAuthenticator(lambda device_id: {"drone_token_hash": "H"}, verify=lambda *_: True)
        self.assertFalse(auth.authenticate("", "tok"))
        self.assertFalse(auth.authenticate("d1", ""))

    def test_db_authenticator_rejects_device_without_hash(self):
        auth = DbAuthenticator(lambda device_id: {"drone_token_hash": ""}, verify=lambda *_: True)
        self.assertFalse(auth.authenticate("d1", "tok"))

    def test_db_authenticator_treats_lookup_error_as_failure(self):
        def boom(device_id):
            raise RuntimeError("db down")

        auth = DbAuthenticator(boom, verify=lambda *_: True)
        self.assertFalse(auth.authenticate("d1", "tok"))

    def test_allow_all_for_dev(self):
        auth = AllowAllAuthenticator()
        self.assertTrue(auth.authenticate("d1", "anything"))
        self.assertFalse(auth.authenticate("", "anything"))


class MuxServerTests(unittest.TestCase):
    """End-to-end over a real loopback TCP connection (no TLS in tests)."""

    async def _start(self, server: MuxServer):
        srv = await asyncio.start_server(server.handle_connection, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        return srv, port

    @staticmethod
    async def _read_control(reader):
        _, payload = await asyncio.wait_for(protocol.read_frame_async(reader), timeout=5)
        return protocol.decode_control(payload)

    def test_handshake_presence_ping_bye(self):
        async def scenario():
            registry = PresenceRegistry()
            server = MuxServer(
                authenticator=AllowAllAuthenticator(), registry=registry, ping_interval=30.0
            )
            srv, port = await self._start(server)
            async with srv:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    writer.write(
                        protocol.encode_control(
                            {
                                "type": protocol.MSG_HELLO,
                                "device_id": "d1",
                                "token": "t",
                                "capabilities": ["relay"],
                                "lan_addrs": ["192.168.1.9"],
                            }
                        )
                    )
                    await writer.drain()

                    ack = await self._read_control(reader)
                    self.assertEqual(ack["type"], protocol.MSG_HELLO_ACK)
                    self.assertTrue(ack["session_id"])
                    self.assertTrue(str(ack["reflexive_addr"]).startswith("127.0.0.1:"))
                    self.assertTrue(registry.is_online("d1"))
                    self.assertEqual(registry.get("d1").capabilities, ["relay"])
                    self.assertEqual(registry.get("d1").lan_addrs, ["192.168.1.9"])

                    writer.write(protocol.encode_control({"type": protocol.MSG_PING, "t": 7}))
                    await writer.drain()
                    pong = await self._read_control(reader)
                    self.assertEqual(pong, {"type": protocol.MSG_PONG, "t": 7})

                    writer.write(protocol.encode_control({"type": protocol.MSG_BYE}))
                    await writer.drain()
                finally:
                    writer.close()

                for _ in range(100):
                    if not registry.is_online("d1"):
                        break
                    await asyncio.sleep(0.02)
                self.assertFalse(registry.is_online("d1"))

        asyncio.run(scenario())

    def test_rejects_non_hello_first_frame(self):
        async def scenario():
            registry = PresenceRegistry()
            server = MuxServer(authenticator=AllowAllAuthenticator(), registry=registry)
            srv, port = await self._start(server)
            async with srv:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    writer.write(protocol.encode_control({"type": protocol.MSG_PING}))
                    await writer.drain()
                    err = await self._read_control(reader)
                    self.assertEqual(err["type"], protocol.MSG_ERROR)
                finally:
                    writer.close()
            self.assertEqual(registry.online_ids(), [])

        asyncio.run(scenario())

    def test_auth_rejection_closes_without_presence(self):
        class DenyAll:
            def authenticate(self, device_id, token):
                return False

        async def scenario():
            registry = PresenceRegistry()
            server = MuxServer(authenticator=DenyAll(), registry=registry)
            srv, port = await self._start(server)
            async with srv:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    writer.write(
                        protocol.encode_control(
                            {"type": protocol.MSG_HELLO, "device_id": "d1", "token": "bad"}
                        )
                    )
                    await writer.drain()
                    err = await self._read_control(reader)
                    self.assertEqual(err["type"], protocol.MSG_ERROR)
                    self.assertEqual(err["reason"], "unauthorized")
                finally:
                    writer.close()
            self.assertEqual(registry.online_ids(), [])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
