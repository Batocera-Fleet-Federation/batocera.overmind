"""Tests for the Edge service foundations: protocol codec, registry, auth."""

import asyncio
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.drone_security import hash_drone_token
from overmind.edge import protocol
from overmind.edge.auth import AllowAllAuthenticator, DbAuthenticator
from overmind.edge.edge_app import (
    EdgeConfig,
    build_authenticator,
    build_registry,
    build_server,
    build_ssl_context,
    make_db_presence_writer,
)
from overmind.edge.registry import PresenceEntry, PresenceRegistry
from overmind.edge.server import MuxServer
from overmind.postgres_store import PostgresMetadataStore


class _FakeCursor:
    def __init__(self, rowcount=1):
        self.executed = []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self._cursor


class _SyncExecutor:
    """Runs submitted work inline so presence-writer tests are deterministic."""

    def submit(self, fn):
        fn()


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


class EdgeAppTests(unittest.TestCase):
    def test_config_from_env(self):
        env = {
            "EDGE_HOST": "127.0.0.1",
            "EDGE_PORT": "9000",
            "EDGE_ALLOW_INSECURE": "1",
            "EDGE_PING_INTERVAL": "5",
            "EDGE_AUTH": "allow-all",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            config = EdgeConfig.from_env()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 9000)
        self.assertTrue(config.allow_insecure)
        self.assertEqual(config.ping_interval, 5.0)
        self.assertEqual(config.auth_mode, "allow-all")

    def test_build_ssl_context_insecure_returns_none(self):
        config = EdgeConfig(allow_insecure=True)
        self.assertIsNone(build_ssl_context(config))

    def test_build_ssl_context_requires_tls_or_insecure(self):
        config = EdgeConfig(allow_insecure=False, tls_cert=None, tls_key=None)
        with self.assertRaises(RuntimeError):
            build_ssl_context(config)

    def test_build_ssl_context_self_signed(self):
        import shutil
        import ssl
        import tempfile

        if shutil.which("openssl") is None:
            self.skipTest("openssl not available to generate a self-signed cert")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"TLS_SELF_SIGNED_DIR": tmp}, clear=False):
                context = build_ssl_context(EdgeConfig(tls_self_signed=True))
        self.assertIsInstance(context, ssl.SSLContext)

    def test_build_authenticator_allow_all(self):
        auth = build_authenticator(EdgeConfig(auth_mode="allow-all"))
        self.assertIsInstance(auth, AllowAllAuthenticator)

    def test_build_authenticator_db_uses_injected_lookup(self):
        token = "raw-token-123"
        devices = {"d1": {"drone_token_hash": hash_drone_token(token)}}
        auth = build_authenticator(EdgeConfig(auth_mode="db"), lookup_device=devices.get)
        self.assertIsInstance(auth, DbAuthenticator)
        self.assertTrue(auth.authenticate("d1", token))
        self.assertFalse(auth.authenticate("d1", "wrong"))

    def test_build_server_end_to_end(self):
        async def scenario():
            config = EdgeConfig(
                host="127.0.0.1",
                port=0,
                allow_insecure=True,
                auth_mode="allow-all",
                ping_interval=30.0,
            )
            registry = PresenceRegistry()
            server = build_server(config, registry=registry, ssl_context=None)
            srv = await asyncio.start_server(server.handle_connection, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]
            async with srv:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                try:
                    writer.write(
                        protocol.encode_control(
                            {"type": protocol.MSG_HELLO, "device_id": "d9", "token": "x"}
                        )
                    )
                    await writer.drain()
                    _, payload = await asyncio.wait_for(
                        protocol.read_frame_async(reader), timeout=5
                    )
                    ack = protocol.decode_control(payload)
                    self.assertEqual(ack["type"], protocol.MSG_HELLO_ACK)
                    self.assertTrue(registry.is_online("d9"))
                finally:
                    writer.close()

        asyncio.run(scenario())


class StorePresenceTests(unittest.TestCase):
    def _store_with_cursor(self, cursor):
        store = PostgresMetadataStore()
        store._core_connection = lambda ensure_schema=False: _FakeConn(cursor)
        return store

    def test_update_device_edge_presence_online(self):
        cursor = _FakeCursor(rowcount=1)
        store = self._store_with_cursor(cursor)
        ok = store.update_device_edge_presence(
            "dev1", online=True, edge_node="node-a", reflexive_endpoint="9.9.9.9:5"
        )
        self.assertTrue(ok)
        sql, params = cursor.executed[0]
        self.assertIn("drone_network_state", sql)
        self.assertIn("edge_online", sql)
        # (online, edge_node, reflexive, online-again-for-CASE, device_id)
        self.assertEqual(params, (True, "node-a", "9.9.9.9:5", True, "dev1"))

    def test_update_device_edge_presence_offline_no_match(self):
        cursor = _FakeCursor(rowcount=0)
        store = self._store_with_cursor(cursor)
        self.assertFalse(store.update_device_edge_presence("missing", online=False))
        _, params = cursor.executed[0]
        self.assertEqual(params, (False, None, None, False, "missing"))

    def test_update_device_edge_presence_no_connection(self):
        store = PostgresMetadataStore()
        store._core_connection = lambda ensure_schema=False: None
        self.assertFalse(store.update_device_edge_presence("dev1", online=True))


class PresenceWiringTests(unittest.TestCase):
    def test_build_registry_invokes_presence_writer(self):
        calls = []
        registry = build_registry(
            presence_writer=lambda device_id, online, refl: calls.append((device_id, online, refl))
        )
        registry.register(
            PresenceEntry(device_id="d1", session_id="s1", reflexive_addr="1.1.1.1:9")
        )
        registry.deregister("d1", "s1")
        self.assertEqual(calls, [("d1", True, "1.1.1.1:9"), ("d1", False, "1.1.1.1:9")])

    def test_make_db_presence_writer_persists(self):
        import overmind.postgres_store as ps

        captured = {}

        def fake(device_id, **kwargs):
            captured.update({"device_id": device_id, **kwargs})
            return True

        with mock.patch.object(ps.postgres_store, "update_device_edge_presence", side_effect=fake):
            writer = make_db_presence_writer(edge_node="node-x", executor=_SyncExecutor())
            writer("d7", True, "2.2.2.2:7")
        self.assertEqual(captured["device_id"], "d7")
        self.assertTrue(captured["online"])
        self.assertEqual(captured["edge_node"], "node-x")
        self.assertEqual(captured["reflexive_endpoint"], "2.2.2.2:7")

    def test_make_db_presence_writer_swallows_errors(self):
        import overmind.postgres_store as ps

        logs = []
        with mock.patch.object(
            ps.postgres_store, "update_device_edge_presence", side_effect=RuntimeError("db down")
        ):
            writer = make_db_presence_writer(executor=_SyncExecutor(), log=logs.append)
            writer("d7", True, None)  # must not raise
        self.assertTrue(any("presence persist failed" in message for message in logs))


if __name__ == "__main__":
    unittest.main()
