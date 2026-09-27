"""Stable revision snapshots and writes shared by import, sync, and migration."""

import json
import sqlite3
from datetime import datetime, timezone

SNAPSHOT_FIELDS = (
    "manufacturer", "product_family", "model_name", "device_type", "sku",
    "configuration_key", "part_number", "region", "model_year", "cpu", "gpu",
    "ram_gb", "storage_gb", "display_size_inches", "battery_wh", "weight_g",
    "form_factor", "power_supply_w",
)


def snapshot(connection: sqlite3.Connection, configuration_id: int) -> dict:
    """Read one complete state, including all preserved sources, as JSON-safe values."""
    cursor = connection.execute("""
        SELECT m.name, f.product_family, f.model, f.device_type, c.sku,
               c.configuration_key, c.part_number, c.region, c.model_year,
               c.cpu, c.gpu, c.ram_gb, c.storage_gb, c.display_size_inches,
               c.battery_wh, c.weight_g, c.form_factor, c.power_supply_w
        FROM configurations AS c
        JOIN device_families AS f ON c.family_id = f.id
        JOIN manufacturers AS m ON f.manufacturer_id = m.id
        WHERE c.id = ?
    """, (configuration_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"Configuration {configuration_id} does not exist")
    result = dict(zip(SNAPSHOT_FIELDS, row))
    result["sources"] = [
        {"url": url, "title": title, "checked_on": checked_on}
        for url, title, checked_on in connection.execute("""
            SELECT source_url, source_name, checked_on
            FROM sources WHERE configuration_id = ? ORDER BY id
        """, (configuration_id,))
    ]
    return result


def add_revision(
    connection: sqlite3.Connection,
    configuration_id: int,
    change_type: str,
    before: dict | None,
    after: dict,
    source_url: str | None,
    checked_on: str | None,
) -> None:
    connection.execute("""
        INSERT INTO revisions
            (configuration_id, recorded_at, change_type, before_json, after_json,
             source_url, checked_on)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        configuration_id,
        datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        change_type,
        None if before is None else json.dumps(before, ensure_ascii=False, sort_keys=True),
        json.dumps(after, ensure_ascii=False, sort_keys=True),
        source_url,
        checked_on,
    ))
