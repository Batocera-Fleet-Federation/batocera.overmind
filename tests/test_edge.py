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
from overmind.edge.relay import RateLimiter, RelayHub
from overmind.edge.server import MuxServer
from overmind.postgres_store import PostgresMetadataStore
from overmind.transfer_tokens import mint_transfer_token


async def _aread_control(reader):
    _, payload = await asyncio.wait_for(protocol.read_frame_async(reader), timeout=5)
    return protocol.decode_control(payload)


async def _aread_frame(reader):
    return await asyncio.wait_for(protocol.read_frame_async(reader), timeout=5)


def _async_recorder():
    sent = []

    async def send(data):
        sent.append(data)

    return sent, send


class _FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    async def sleep(self, delay):
        self.slept.append(delay)
        self.t += delay


class _FakeCursor:
    def __init__(self, rowcount=1, fetchone_result=None):
        self.executed = []
        self.rowcount = rowcount
        self.fetchone_result = fetchone_result

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.fetchone_result

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


class DeviceRowMappingTests(unittest.TestCase):
    """_device_from_row must surface the edge_online / reflexive_endpoint columns
    that _select_device_sql now projects (positions 26 & 27, after checked_at)."""

    def _row(self):
        # Mostly-None row of the exact width _device_from_row unpacks; the helper
        # tolerates None everywhere (list(x or []), bool(x), isinstance checks).
        # Update the width here if the device column list changes.
        row = [None] * 61
        row[1] = "bff-drone-a"  # device_id
        row[25] = True  # edge_online
        row[26] = "203.0.113.9:5555"  # reflexive_endpoint
        return tuple(row)

    def test_maps_edge_presence_columns(self):
        device = PostgresMetadataStore()._device_from_row(self._row())
        self.assertEqual(device["device_id"], "bff-drone-a")
        self.assertTrue(device["edge_online"])
        self.assertEqual(device["reflexive_endpoint"], "203.0.113.9:5555")

    def test_edge_online_defaults_false(self):
        row = list(self._row())
        row[25] = None  # no presence row joined
        device = PostgresMetadataStore()._device_from_row(tuple(row))
        self.assertFalse(device["edge_online"])


class TransferStoreTests(unittest.TestCase):
    def _store(self, cursor):
        store = PostgresMetadataStore()
        store._core_connection = lambda ensure_schema=False: _FakeConn(cursor)
        return store

    def test_create_transfer_session(self):
        cursor = _FakeCursor(rowcount=1)
        store = self._store(cursor)
        ok = store.create_transfer_session(
            session_id="s" * 32,
            from_device="B",
            to_device="A",
            asset={"kind": "rom", "relative_path": "g"},
            token_hash="h",
            expires_at_epoch=1_700_000_000,
            swarm_id="sw",
        )
        self.assertTrue(ok)
        sql, params = cursor.executed[0]
        self.assertIn("INSERT INTO transfer_sessions", sql)
        self.assertEqual(params[0], "s" * 32)
        self.assertEqual(params[4], '{"kind":"rom","relative_path":"g"}')
        self.assertEqual(params[7], 1_700_000_000)

    def test_get_transfer_session_decodes_asset(self):
        row = (
            "s" * 32, "sw", "B", "A", '{"kind":"rom","relative_path":"g"}',
            "relay", "active", 100, 40, None, 1_700_000_000.0,
        )
        store = self._store(_FakeCursor(fetchone_result=row))
        result = store.get_transfer_session("s" * 32)
        self.assertEqual(result["from_device"], "B")
        self.assertEqual(result["asset"], {"kind": "rom", "relative_path": "g"})
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["bytes_done"], 40)
        self.assertEqual(result["expires_at_epoch"], 1_700_000_000)

    def test_update_transfer_session(self):
        cursor = _FakeCursor(rowcount=1)
        store = self._store(cursor)
        ok = store.update_transfer_session(
            "s" * 32, status="completed", transport_used="relay", bytes_total=100, bytes_done=100
        )
        self.assertTrue(ok)
        sql, params = cursor.executed[0]
        self.assertIn("UPDATE transfer_sessions", sql)
        self.assertEqual(params, ("completed", "relay", 100, 100, None, "s" * 32))

    def test_expire_transfer_sessions(self):
        cursor = _FakeCursor(rowcount=3)
        store = self._store(cursor)
        self.assertEqual(store.expire_transfer_sessions(), 3)
        self.assertIn("expired", cursor.executed[0][0])

    def test_no_connection_is_safe(self):
        store = PostgresMetadataStore()
        store._core_connection = lambda ensure_schema=False: None
        self.assertFalse(
            store.create_transfer_session(
                session_id="s", from_device="B", to_device="A", asset={}, token_hash="h",
                expires_at_epoch=1,
            )
        )
        self.assertIsNone(store.get_transfer_session("s"))
        self.assertEqual(store.expire_transfer_sessions(), 0)


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


class RateLimiterTests(unittest.TestCase):
    def test_unlimited_never_sleeps(self):
        clock = _FakeClock()

        async def scenario():
            limiter = RateLimiter(0, now=clock.now, sleep=clock.sleep)
            await limiter.acquire(10_000_000)

        asyncio.run(scenario())
        self.assertEqual(clock.slept, [])

    def test_rate_limit_paces_with_sleep(self):
        clock = _FakeClock()

        async def scenario():
            # 1000 bytes/s, burst 1000: first 1000 free, next 1000 waits ~1s.
            limiter = RateLimiter(1000.0, capacity=1000.0, now=clock.now, sleep=clock.sleep)
            await limiter.acquire(1000)
            await limiter.acquire(1000)

        asyncio.run(scenario())
        self.assertAlmostEqual(clock.t, 1.0, places=3)
        self.assertEqual(len(clock.slept), 1)


class RelayHubTests(unittest.TestCase):
    def test_pair_forward_and_close(self):
        async def scenario():
            hub = RelayHub()
            sent_sender, send_sender = _async_recorder()
            sent_receiver, send_receiver = _async_recorder()
            session = hub.open_leg("s1", "sender", "B", 1, send_sender)
            self.assertFalse(session.is_ready())
            session = hub.open_leg("s1", "receiver", "A", 2, send_receiver)
            self.assertTrue(session.is_ready())

            payload = ("s1".ljust(32, "0")).encode() + b"hello"
            self.assertTrue(await hub.forward("s1", 2, payload))  # receiver -> sender
            self.assertEqual(sent_sender, [protocol.encode_frame(protocol.FRAME_DATA, payload)])
            self.assertEqual(sent_receiver, [])
            self.assertTrue(await hub.forward("s1", 1, payload))  # sender -> receiver
            self.assertEqual(len(sent_receiver), 1)

            self.assertEqual(len(hub.close_session("s1")), 2)
            self.assertEqual(hub.session_count(), 0)

        asyncio.run(scenario())

    def test_forward_without_peer_returns_false(self):
        async def scenario():
            hub = RelayHub()
            _, send = _async_recorder()
            hub.open_leg("x", "sender", "B", 1, send)
            return await hub.forward("x", 1, b"0" * 32 + b"data")

        self.assertFalse(asyncio.run(scenario()))

    def test_drop_connection_notifies_peer(self):
        hub = RelayHub()
        _, send_s = _async_recorder()
        _, send_r = _async_recorder()
        hub.open_leg("s", "sender", "B", 1, send_s)
        hub.open_leg("s", "receiver", "A", 2, send_r)
        notify = hub.drop_connection(1)  # sender's connection drops
        self.assertEqual(len(notify), 1)
        session_id, peer = notify[0]
        self.assertEqual(session_id, "s")
        self.assertEqual(peer.conn_id, 2)
        # Lone receiver leg lingers until it too drops.
        self.assertEqual(hub.drop_connection(2), [])
        self.assertEqual(hub.session_count(), 0)


class RelayEndToEndTests(unittest.TestCase):
    def test_relay_forwards_data_between_two_legs(self):
        async def scenario():
            server = MuxServer(
                authenticator=AllowAllAuthenticator(),
                registry=PresenceRegistry(),
                ping_interval=30.0,
            )
            srv = await asyncio.start_server(server.handle_connection, "127.0.0.1", 0)
            port = srv.sockets[0].getsockname()[1]
            session = "a" * 32
            async with srv:
                ra, wa = await asyncio.open_connection("127.0.0.1", port)
                rb, wb = await asyncio.open_connection("127.0.0.1", port)
                try:
                    for writer, device in ((wa, "A"), (wb, "B")):
                        writer.write(
                            protocol.encode_control(
                                {"type": protocol.MSG_HELLO, "device_id": device, "token": "t"}
                            )
                        )
                        await writer.drain()
                    await _aread_control(ra)  # HELLO_ACK
                    await _aread_control(rb)

                    def relay_open(role):
                        return protocol.encode_control(
                            {"type": protocol.MSG_RELAY_OPEN, "session_id": session, "role": role}
                        )

                    wa.write(relay_open("receiver"))
                    await wa.drain()
                    wb.write(relay_open("sender"))
                    await wb.drain()
                    self.assertEqual((await _aread_control(ra))["type"], protocol.MSG_RELAY_READY)
                    self.assertEqual((await _aread_control(rb))["type"], protocol.MSG_RELAY_READY)

                    # receiver -> sender
                    wa.write(protocol.encode_relay_data(session, b"hello-from-A"))
                    await wa.drain()
                    kind, payload = await _aread_frame(rb)
                    self.assertEqual(kind, protocol.FRAME_DATA)
                    sid, data = protocol.parse_relay_data(payload)
                    self.assertEqual((sid, data), (session, b"hello-from-A"))

                    # sender -> receiver
                    wb.write(protocol.encode_relay_data(session, b"hi-from-B"))
                    await wb.drain()
                    _, payload = await _aread_frame(ra)
                    _, data = protocol.parse_relay_data(payload)
                    self.assertEqual(data, b"hi-from-B")
                finally:
                    for writer in (wa, wb):
                        writer.close()

        asyncio.run(scenario())


class TransferSignalingTests(unittest.TestCase):
    SECRET = "edge-shared-secret"
    ASSET = {"kind": "rom", "system": "snes", "relative_path": "game.sfc"}

    async def _server(self):
        server = MuxServer(
            authenticator=AllowAllAuthenticator(),
            registry=PresenceRegistry(),
            transfer_secret=self.SECRET,
            ping_interval=30.0,
        )
        srv = await asyncio.start_server(server.handle_connection, "127.0.0.1", 0)
        return srv, srv.sockets[0].getsockname()[1]

    @staticmethod
    async def _connect(port, device_id):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            protocol.encode_control(
                {"type": protocol.MSG_HELLO, "device_id": device_id, "token": "t"}
            )
        )
        await writer.drain()
        await _aread_control(reader)  # HELLO_ACK
        return reader, writer

    def test_valid_request_offers_to_sender(self):
        session = "c" * 32

        async def scenario():
            srv, port = await self._server()
            async with srv:
                r_reader, r_writer = await self._connect(port, "RX")
                s_reader, s_writer = await self._connect(port, "TX")
                try:
                    token = mint_transfer_token(
                        self.SECRET,
                        session_id=session,
                        from_device="TX",
                        to_device="RX",
                        asset=self.ASSET,
                    )
                    r_writer.write(
                        protocol.encode_control(
                            {
                                "type": protocol.MSG_TRANSFER_REQUEST,
                                "session_id": session,
                                "token": token,
                                "from_device": "TX",
                                "asset": self.ASSET,
                            }
                        )
                    )
                    await r_writer.drain()
                    offer = await _aread_control(s_reader)
                    self.assertEqual(offer["type"], protocol.MSG_TRANSFER_OFFER)
                    self.assertEqual(offer["session_id"], session)
                    self.assertEqual(offer["from_device"], "TX")
                    self.assertEqual(offer["to_device"], "RX")
                    self.assertEqual(offer["asset"], self.ASSET)
                finally:
                    for writer in (r_writer, s_writer):
                        writer.close()

        asyncio.run(scenario())

    def test_invalid_token_returns_error_to_receiver(self):
        async def scenario():
            srv, port = await self._server()
            async with srv:
                r_reader, r_writer = await self._connect(port, "RX")
                await self._connect(port, "TX")
                try:
                    r_writer.write(
                        protocol.encode_control(
                            {
                                "type": protocol.MSG_TRANSFER_REQUEST,
                                "session_id": "c" * 32,
                                "token": "not-a-valid-token",
                                "from_device": "TX",
                                "asset": self.ASSET,
                            }
                        )
                    )
                    await r_writer.drain()
                    error = await _aread_control(r_reader)
                    self.assertEqual(error["type"], protocol.MSG_TRANSFER_ERROR)
                finally:
                    r_writer.close()

        asyncio.run(scenario())

    def test_offline_sender_returns_error_to_receiver(self):
        session = "c" * 32

        async def scenario():
            srv, port = await self._server()
            async with srv:
                r_reader, r_writer = await self._connect(port, "RX")
                try:
                    token = mint_transfer_token(
                        self.SECRET,
                        session_id=session,
                        from_device="GHOST",
                        to_device="RX",
                        asset=self.ASSET,
                    )
                    r_writer.write(
                        protocol.encode_control(
                            {
                                "type": protocol.MSG_TRANSFER_REQUEST,
                                "session_id": session,
                                "token": token,
                                "from_device": "GHOST",
                                "asset": self.ASSET,
                            }
                        )
                    )
                    await r_writer.drain()
                    error = await _aread_control(r_reader)
                    self.assertEqual(error["type"], protocol.MSG_TRANSFER_ERROR)
                    self.assertIn("offline", error["reason"])
                finally:
                    r_writer.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
