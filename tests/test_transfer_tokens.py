"""Tests for short-lived relayed-transfer authorization tokens."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.transfer_tokens import mint_transfer_token, verify_transfer_token

_ASSET = {"kind": "rom", "system": "snes", "relative_path": "game.sfc"}


class TransferTokenTests(unittest.TestCase):
    def _mint(self, **overrides):
        params = dict(
            secret="shared-secret",
            session_id="s" * 32,
            from_device="drone-b",
            to_device="drone-a",
            asset=_ASSET,
            ttl_seconds=300,
            now=1_000_000,
        )
        params.update(overrides)
        secret = params.pop("secret")
        return secret, mint_transfer_token(secret, **params)

    def test_valid_round_trip(self):
        secret, token = self._mint()
        payload = verify_transfer_token(secret, token, now=1_000_010)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sid"], "s" * 32)
        self.assertEqual(payload["from"], "drone-b")
        self.assertEqual(payload["to"], "drone-a")
        self.assertEqual(payload["asset"], _ASSET)
        self.assertEqual(payload["exp"], 1_000_300)

    def test_wrong_secret_rejected(self):
        _, token = self._mint()
        self.assertIsNone(verify_transfer_token("other-secret", token, now=1_000_010))

    def test_expired_rejected(self):
        secret, token = self._mint()
        self.assertIsNone(verify_transfer_token(secret, token, now=1_000_301))

    def test_tampered_payload_rejected(self):
        secret, token = self._mint()
        payload_b64, signature_b64 = token.split(".", 1)
        # Flip the last char of the payload; signature no longer matches.
        tampered = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
        token = f"{tampered}.{signature_b64}"
        self.assertIsNone(verify_transfer_token(secret, token, now=1_000_010))

    def test_malformed_token_rejected(self):
        self.assertIsNone(verify_transfer_token("s", "not-a-token", now=1))
        self.assertIsNone(verify_transfer_token("s", "", now=1))


if __name__ == "__main__":
    unittest.main()
