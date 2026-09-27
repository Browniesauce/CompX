"""Read and present chronological configuration revisions."""

import json
from dataclasses import dataclass
from pathlib import Path

from compx.catalog import MAX_SQLITE_INTEGER, InvalidFilter
from compx.comparison import UNKNOWN
from compx.repository import CatalogNotReadyError, _read_catalog
from compx.sync import FIELDS, FieldChange


@dataclass(frozen=True)
class Revision:
    recorded_at: str
    change_type: str
    before: dict | None
    after: dict
    source_url: str | None
    checked_on: str | None


def get_history(path: Path, device_id: int) -> list[Revision] | None:
    if not 0 < device_id <= MAX_SQLITE_INTEGER:
        raise InvalidFilter("DEVICE_ID must be a positive integer within SQLite's range")
    with _read_catalog(path) as connection:
        if connection.execute(
            "SELECT 1 FROM configurations WHERE id = ?", (device_id,)
        ).fetchone() is None:
            return None
        if connection.execute("""
            SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'revisions'
        """).fetchone() is None:
            raise CatalogNotReadyError(
                f"Catalog at {path} needs a history upgrade. Run compx init --database {path}."
            )
        return [
            Revision(
                recorded_at, change_type,
                json.loads(before_json) if before_json is not None else None,
                json.loads(after_json), source_url, checked_on,
            )
            for recorded_at, change_type, before_json, after_json, source_url, checked_on
            in connection.execute("""
                SELECT recorded_at, change_type, before_json, after_json,
                       source_url, checked_on
                FROM revisions WHERE configuration_id = ? ORDER BY id
            """, (device_id,))
        ]


def format_history(device_id: int, revisions: list[Revision]) -> str:
    labels = {
        "initial": "Initial record",
        "hardware": "Hardware change",
        "data_update": "Data update",
        "source_only": "Source-only update",
    }
    lines = [f"History for configuration {device_id}:"]
    if not revisions:
        lines.append("  No revisions recorded. Run compx init to create a baseline.")
        return "\n".join(lines)
    for index, revision in enumerate(revisions, start=1):
        lines.append(
            f"{index}. {revision.recorded_at} — "
            f"{labels.get(revision.change_type, revision.change_type)}"
        )
        if revision.before is None:
            lines.append(f"   Manufacturer: {revision.after['manufacturer']}")
            lines.append(f"   SKU: {revision.after['sku'] or UNKNOWN}")
            for label, stored, _, unit in FIELDS:
                value = revision.after.get(stored)
                displayed = UNKNOWN if value is None else f"{value} {unit}" if unit else str(value)
                lines.append(f"   {label}: {displayed}")
            for source in revision.after.get("sources", []):
                lines.append(
                    f"   Source: {source['url']} (checked {source['checked_on']})"
                )
        else:
            for label, stored, _, unit in FIELDS:
                old, new = revision.before.get(stored), revision.after.get(stored)
                if old != new:
                    lines.append("   " + FieldChange(label, old, new, unit).display())
            before_sources = revision.before.get("sources", [])
            for source in revision.after.get("sources", []):
                if source not in before_sources:
                    lines.append(
                        f"   Source added: {source['url']} "
                        f"(checked {source['checked_on']})"
                    )
        if revision.source_url:
            lines.append(f"   Update source: {revision.source_url}")
        if revision.checked_on:
            lines.append(f"   Checked on: {revision.checked_on}")
    return "\n".join(lines)
