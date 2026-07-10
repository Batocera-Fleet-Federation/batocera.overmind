"""Width-contract tests for the domain-asset projection.

``page_master_assets`` slices its rows by the width of the ``_DOMAIN_ASSET_SOURCE``
column list, and ``_domain_asset_payload`` unpacks exactly that many values; a
column added to one but not the other silently misaligns every master row. These
tests pin the two to each other.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.postgres_store import PostgresMetadataStore


def test_payload_unpacks_exactly_the_source_columns():
    for asset_type, (_table, columns, _order) in PostgresMetadataStore._DOMAIN_ASSET_SOURCE.items():
        width = len(columns.split(","))
        row = tuple(f"v{i}" for i in range(width))
        payload = PostgresMetadataStore._domain_asset_payload(asset_type, row)
        assert isinstance(payload, dict) and payload, asset_type


def test_rom_payload_carries_entry_type():
    columns = PostgresMetadataStore._DOMAIN_ASSET_SOURCE["rom"][1]
    assert "entry_type" in columns
    row = ("lindbergh", "42", "House of the Dead 4", "fp", 5_000_000_000, "folder")
    payload = PostgresMetadataStore._domain_asset_payload("rom", row)
    assert payload["entry_type"] == "folder"
    assert payload["file_size"] == 5_000_000_000
    # Pre-upgrade rows (NULL column) default to 'file'.
    payload = PostgresMetadataStore._domain_asset_payload("rom", row[:5] + (None,))
    assert payload["entry_type"] == "file"
