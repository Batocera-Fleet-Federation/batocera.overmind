"""Runtime configuration overrides from AWS Secrets Manager."""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Callable, Optional

logger = logging.getLogger("overmind.runtime_secrets")


def _secret_name() -> str:
    return (os.getenv("OVERMIND_RUNTIME_SECRET_NAME") or os.getenv("AWS_RUNTIME_SECRET_NAME") or "overmind").strip()


def _region() -> Optional[str]:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")


def _parse_secret(value: Optional[str]) -> dict[str, str]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        logger.warning("Runtime secret ignored: secret JSON is not an object")
        return {}
    return {str(key): str(item) for key, item in parsed.items() if key and item is not None}


def _checksum(values: dict[str, str]) -> str:
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def apply_overrides(values: dict[str, str], on_apply: Optional[Callable[[dict[str, str]], None]] = None) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    if on_apply:
        on_apply(values)


@dataclass
class RuntimeSecretRefresher:
    secret_name: str = field(default_factory=_secret_name)
    region: Optional[str] = field(default_factory=_region)
    interval_seconds: int = field(default_factory=lambda: max(15, int(os.getenv("OVERMIND_SECRET_REFRESH_SECONDS", "60"))))
    client: object = None
    on_apply: Optional[Callable[[dict[str, str]], None]] = None
    _last_version_id: Optional[str] = None
    _last_checksum: Optional[str] = None
    _last_good_values: dict[str, str] = field(default_factory=dict)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def _client(self):
        if self.client is not None:
            return self.client
        if not self.region:
            raise RuntimeError("AWS_REGION or AWS_DEFAULT_REGION is not set")
        import boto3  # type: ignore

        self.client = boto3.client("secretsmanager", region_name=self.region)
        return self.client

    def refresh_once(self) -> bool:
        logger.info("Runtime secret refresh attempted secret=%s", self.secret_name)
        try:
            response = self._client().get_secret_value(SecretId=self.secret_name)
            values = _parse_secret(response.get("SecretString"))
            version_id = response.get("VersionId")
            checksum = _checksum(values)
            if version_id == self._last_version_id and checksum == self._last_checksum:
                logger.info("Runtime secret unchanged secret=%s keys=%s", self.secret_name, len(values))
                return False
            apply_overrides(values, self.on_apply)
            self._last_version_id = version_id
            self._last_checksum = checksum
            self._last_good_values = dict(values)
            logger.info("Runtime secret applied secret=%s keys=%s", self.secret_name, len(values))
            return True
        except Exception as error:
            logger.warning(
                "Runtime secret refresh failed secret=%s error=%s",
                self.secret_name,
                error.__class__.__name__,
            )
            return False

    def start(self) -> None:
        if self._thread or self.interval_seconds <= 0:
            return

        def loop() -> None:
            while not self._stop.wait(self.interval_seconds):
                self.refresh_once()

        self._thread = threading.Thread(target=loop, name="overmind-runtime-secret-refresh", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def load_runtime_secret_once(on_apply: Optional[Callable[[dict[str, str]], None]] = None) -> RuntimeSecretRefresher:
    refresher = RuntimeSecretRefresher(on_apply=on_apply)
    refresher.refresh_once()
    return refresher
