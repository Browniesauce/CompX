"""Plan and format manual CSV refreshes without writing to the catalog."""

import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from compx.comparison import UNKNOWN, equivalent_values
from compx.csv_import import ImportPlan, Issue, inspect_csv
from compx.database import connect, init_database
from compx.models import CsvRecord
from compx.repository import CatalogNotReadyError, _read_catalog, apply_sync_items
from compx.revision_data import snapshot


@dataclass(frozen=True)
class FieldChange:
    label: str
    before: object
    after: object
    unit: str | None = None

    @property
    def is_data_update(self) -> bool:
        return self.before is None or self.after is None

    def display(self) -> str:
        def value(item: object) -> str:
            if item is None or item == "":
                return UNKNOWN
            return f"{item} {self.unit}" if self.unit else str(item)

        note = " (data update)" if self.is_data_update else ""
        return f"{self.label}: {value(self.before)} -> {value(self.after)}{note}"


FIELDS = (
    ("Product family", "product_family", "product_family", None),
    ("Model name", "model_name", "model_name", None),
    ("Device type", "device_type", "device_type", None),
    ("Region", "region", "region", None),
    ("Model year", "model_year", "model_year", None),
    ("CPU", "cpu", "cpu", None),
    ("GPU", "gpu", "gpu", None),
    ("RAM", "ram_gb", "ram_gb", "GB"),
    ("Storage", "storage_gb", "storage_gb", "GB"),
    ("Display size", "display_size_inches", "display_size_in", "in"),
    ("Battery", "battery_wh", "battery_wh", "Wh"),
    ("Weight", "weight_g", "weight_g", "g"),
    ("Form factor", "form_factor", "form_factor", None),
    ("Power supply", "power_supply_w", "psu_watts", "W"),
)
HARDWARE_LABELS = {
    "CPU", "GPU", "RAM", "Storage", "Display size", "Battery", "Weight",
    "Form factor", "Power supply",
}


@dataclass(frozen=True)
class SyncItem:
    record: CsvRecord
    classification: str
    configuration_id: int | None = None
    changes: tuple[FieldChange, ...] = ()
    add_source: bool = False

    @property
    def revision_type(self) -> str:
        if self.classification == "New":
            return "initial"
        if self.classification == "Source-only update":
            return "source_only"
        if any(
            change.label in HARDWARE_LABELS and not change.is_data_update
            for change in self.changes
        ):
            return "hardware"
        return "data_update"


@dataclass
class SyncPlan:
    items: list[SyncItem] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    total_rows: int = 0

    def counts(self) -> Counter:
        result = Counter(item.classification for item in self.items)
        row_issues: dict[int, list[str]] = {}
        for issue in self.issues:
            row_issues.setdefault(issue.row_number, []).append(issue.message)
        for messages in row_issues.values():
            result["Conflict" if any(
                "duplicate manufacturer + SKU" in message or "ambiguous" in message
                for message in messages
            ) else "Invalid"] += 1
        return result


class CatalogChangedError(Exception):
    """The catalog changed after preview and before the write lock."""


def _from_parsed(parsed: ImportPlan, connection: sqlite3.Connection | None) -> SyncPlan:
    plan = SyncPlan(issues=list(parsed.issues), total_rows=parsed.total_rows)
    invalid_rows = {issue.row_number for issue in parsed.issues}
    identities: dict[tuple[str, str], list[int]] = {}
    if connection is not None:
        for configuration_id, manufacturer, sku in connection.execute("""
            SELECT c.id, m.name, c.sku FROM configurations AS c
            JOIN device_families AS f ON c.family_id = f.id
            JOIN manufacturers AS m ON f.manufacturer_id = m.id
            WHERE c.sku IS NOT NULL
        """):
            identities.setdefault((manufacturer.casefold(), sku.casefold()), []).append(
                configuration_id
            )
    for record in parsed.records:
        if record.row_number in invalid_rows:
            continue
        matches = identities.get(record.identity, [])
        if len(matches) > 1:
            plan.issues.append(Issue(
                record.row_number,
                f"ambiguous manufacturer + SKU matches {len(matches)} configurations",
            ))
            continue
        if not matches:
            plan.items.append(SyncItem(record, "New", add_source=True))
            continue
        configuration_id = matches[0]
        current = snapshot(connection, configuration_id)
        changes = tuple(
            FieldChange(label, current[stored], getattr(record, incoming), unit)
            for label, stored, incoming, unit in FIELDS
            if not equivalent_values(current[stored], getattr(record, incoming))
        )
        has_source = any(
            source["url"] == record.source_url and source["checked_on"] == record.checked_on
            for source in current["sources"]
        )
        classification = (
            "Changed" if changes else "Source-only update" if not has_source else "Unchanged"
        )
        plan.items.append(SyncItem(
            record, classification, configuration_id, changes, not has_source,
        ))
    return plan


def plan_sync(csv_path: Path, database_path: Path) -> tuple[ImportPlan, SyncPlan]:
    """Validate the whole file and classify rows against a read-only snapshot."""
    parsed = inspect_csv(
        csv_path, database_path,
        check_catalog_duplicates=False, report_all_duplicates=True,
    )
    path = Path(database_path).expanduser()
    if not path.exists():
        return parsed, _from_parsed(parsed, None)
    with _read_catalog(path) as connection:
        connection.execute("BEGIN")
        return parsed, _from_parsed(parsed, connection)


def apply_sync(path: Path, parsed: ImportPlan, preview: SyncPlan) -> SyncPlan:
    """Recheck under a write lock, then apply the complete plan atomically."""
    if preview.issues:
        raise ValueError("Cannot apply a sync plan with invalid or conflicting rows")
    init_database(path)
    with closing(connect(Path(path).expanduser())) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            actual = _from_parsed(parsed, connection)
            if actual.issues or actual.items != preview.items:
                raise CatalogChangedError("Catalog changed since preview; rerun compx sync")
            apply_sync_items(connection, actual.items)
    return actual


def format_sync_plan(plan: SyncPlan, *, applied: bool = False) -> str:
    lines = []
    for item in plan.items:
        suffix = f" (ID {item.configuration_id})" if item.configuration_id else ""
        lines.append(
            f"row {item.record.row_number}: {item.classification} — "
            f"{item.record.manufacturer} / {item.record.sku}{suffix}"
        )
        for change in item.changes:
            lines.append(f"  {change.display()}")
        if item.add_source and item.classification != "New":
            lines.append(f"  Source: {item.record.source_url} (checked {item.record.checked_on})")
    for issue in plan.issues:
        lines.append(str(issue))
    counts = plan.counts()
    labels = ("New", "Unchanged", "Changed", "Source-only update", "Conflict", "Invalid")
    lines.append("Summary: " + ", ".join(f"{label} {counts[label]}" for label in labels))
    if plan.issues:
        lines.append("Apply blocked; no configurations changed.")
    elif applied:
        lines.append("Applied all planned changes.")
    else:
        lines.append("Preview only; no database changes made. Use --apply to save changes.")
    return "\n".join(lines)
