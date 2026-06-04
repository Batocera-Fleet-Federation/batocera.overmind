"""Tests for PostgresMetadataStore schema-drift self-heal on device reads.

A column the deployed database is missing (schema drift after a deploy whose
migration has not landed) must not blank the fleet: the read should re-run
migrations once and retry instead of propagating the failure.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.postgres_store import PostgresMetadataStore, _is_missing_column_error


class UndefinedColumn(Exception):
    """Stand-in for psycopg.errors.UndefinedColumn (matched by class name)."""


class _SqlStateError(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


def test_is_missing_column_error_by_sqlstate():
    assert _is_missing_column_error(_SqlStateError("42703")) is True
    assert _is_missing_column_error(_SqlStateError("23505")) is False


def test_is_missing_column_error_by_class_name():
    assert _is_missing_column_error(UndefinedColumn("column d.x does not exist")) is True
    assert _is_missing_column_error(ValueError("nope")) is False


def test_self_heal_retries_once_after_rerunning_migrations():
    store = PostgresMetadataStore()
    calls = {"query": 0, "schema": 0}

    def fake_ensure_schema():
        calls["schema"] += 1
    store.ensure_schema = fake_ensure_schema  # type: ignore[assignment]
    store._ready = True

    def flaky():
        calls["query"] += 1
        if calls["query"] == 1:
            raise UndefinedColumn("column d.rom_inventory_fingerprint does not exist")
        return ["device"]

    result = store._with_schema_self_heal("list_user_devices", flaky)

    assert result == ["device"]
    assert calls["query"] == 2          # original + one retry
    assert calls["schema"] == 1         # migrations re-run between attempts
    assert store._ready is False        # forced a fresh schema check


def test_self_heal_does_not_swallow_unrelated_errors():
    store = PostgresMetadataStore()
    store.ensure_schema = lambda: None  # type: ignore[assignment]

    def boom():
        raise ValueError("unrelated failure")

    with pytest.raises(ValueError):
        store._with_schema_self_heal("get_device", boom)


def test_self_heal_propagates_when_retry_still_fails():
    store = PostgresMetadataStore()
    store.ensure_schema = lambda: None  # type: ignore[assignment]
    attempts = {"n": 0}

    def always_missing():
        attempts["n"] += 1
        raise UndefinedColumn("still missing")

    with pytest.raises(UndefinedColumn):
        store._with_schema_self_heal("get_device", always_missing)
    assert attempts["n"] == 2  # tried once, retried once, then gave up
