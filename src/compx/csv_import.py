"""Parse and validate the complete CSV before any catalog writes."""

import csv
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from compx.models import CsvRecord
from compx.repository import existing_identities

REQUIRED_COLUMNS = (
    "manufacturer", "product_family", "model_name", "device_type", "sku",
    "source_url", "checked_on",
)
OPTIONAL_COLUMNS = (
    "region", "model_year", "cpu", "gpu", "ram_gb", "storage_gb",
    "display_size_in", "battery_wh", "weight_g", "form_factor", "psu_watts",
)
ALL_COLUMNS = set(REQUIRED_COLUMNS + OPTIONAL_COLUMNS)
CSV_COLUMNS = (
    "manufacturer", "product_family", "model_name", "device_type", "sku",
    "source_url", "checked_on", "region", "model_year", "cpu", "gpu",
    "ram_gb", "storage_gb", "display_size_in", "battery_wh", "weight_g",
    "form_factor", "psu_watts",
)
INTEGER_COLUMNS = ("model_year", "ram_gb", "storage_gb", "weight_g", "psu_watts")
DECIMAL_COLUMNS = ("display_size_in", "battery_wh")


@dataclass(frozen=True)
class Issue:
    row_number: int
    message: str

    def __str__(self) -> str:
        return f"row {self.row_number}: {self.message}"


@dataclass
class ImportPlan:
    records: list[CsvRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    total_rows: int = 0

    @property
    def valid_rows(self) -> int:
        invalid_rows = {issue.row_number for issue in self.issues if issue.row_number > 1}
        return 0 if any(issue.row_number == 1 for issue in self.issues) else self.total_rows - len(invalid_rows)


def inspect_csv(
    csv_path: Path, database_path: Path, *, check_catalog_duplicates: bool = True,
    report_all_duplicates: bool = False,
) -> ImportPlan:
    """Validate file contents and duplicate identities without writing."""
    plan = ImportPlan()
    identity_rows: list[tuple[int, tuple[str, str], str, str]] = []
    with Path(csv_path).open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, strict=True)
        if reader.fieldnames is None:
            plan.issues.append(Issue(1, "CSV is empty; required headers are missing"))
            return plan
        headers = [name.strip() for name in reader.fieldnames]
        reader.fieldnames = headers
        missing = sorted(set(REQUIRED_COLUMNS) - set(headers))
        unexpected = sorted(set(headers) - ALL_COLUMNS)
        repeated = sorted({name for name in headers if headers.count(name) > 1})
        if missing:
            plan.issues.append(Issue(1, "missing required columns: " + ", ".join(missing)))
        if unexpected:
            plan.issues.append(Issue(1, "unexpected columns: " + ", ".join(repr(name) for name in unexpected)))
        if repeated:
            plan.issues.append(Issue(1, "duplicate columns: " + ", ".join(repeated)))
        if plan.issues:
            return plan

        seen: dict[tuple[str, str], int] = {}
        flagged_duplicates: set[tuple[str, str]] = set()
        for row_number, raw in enumerate(reader, start=2):
            plan.total_rows += 1
            issue_count_before_row = len(plan.issues)
            if None in raw:
                plan.issues.append(Issue(row_number, "more cells than columns"))
            values = {name: (value or "").strip() for name, value in raw.items() if name is not None}
            for name in REQUIRED_COLUMNS:
                if not values[name]:
                    plan.issues.append(Issue(row_number, f"{name} is required"))

            manufacturer, sku = values["manufacturer"], values["sku"]
            if manufacturer and sku:
                key = (manufacturer.casefold(), sku.casefold())
                identity_rows.append((row_number, key, manufacturer, sku))
                if key in seen:
                    plan.issues.append(Issue(
                        row_number, f"duplicate manufacturer + SKU (first at row {seen[key]})"
                    ))
                    if report_all_duplicates and key not in flagged_duplicates:
                        plan.issues.append(Issue(
                            seen[key],
                            f"duplicate manufacturer + SKU (also at row {row_number})",
                        ))
                        flagged_duplicates.add(key)
                else:
                    seen[key] = row_number

            if values["device_type"] and values["device_type"] not in ("laptop", "desktop"):
                plan.issues.append(Issue(row_number, "device_type must be laptop or desktop"))

            numbers = {}
            for name in INTEGER_COLUMNS:
                numbers[name] = _positive_integer(values.get(name, ""), name, row_number, plan)
            for name in DECIMAL_COLUMNS:
                numbers[name] = _positive_decimal(values.get(name, ""), name, row_number, plan)

            checked_on = values["checked_on"]
            if checked_on and not _valid_date(checked_on):
                plan.issues.append(Issue(row_number, "checked_on must be a real date in YYYY-MM-DD format"))
            source_url = values["source_url"]
            if source_url and not _valid_url(source_url):
                plan.issues.append(Issue(row_number, "source_url must be a valid HTTP(S) URL"))

            if len(plan.issues) == issue_count_before_row:
                plan.records.append(CsvRecord(
                    row_number=row_number,
                    manufacturer=manufacturer,
                    product_family=values["product_family"],
                    model_name=values["model_name"],
                    device_type=values["device_type"],
                    sku=sku,
                    source_url=source_url,
                    checked_on=checked_on,
                    region=values.get("region") or None,
                    model_year=numbers["model_year"],
                    cpu=values.get("cpu") or None,
                    gpu=values.get("gpu") or None,
                    ram_gb=numbers["ram_gb"],
                    storage_gb=numbers["storage_gb"],
                    display_size_in=numbers["display_size_in"],
                    battery_wh=numbers["battery_wh"],
                    weight_g=numbers["weight_g"],
                    form_factor=values.get("form_factor") or None,
                    psu_watts=numbers["psu_watts"],
                ))

    if plan.total_rows == 0:
        plan.issues.append(Issue(1, "CSV has no device rows"))
    if check_catalog_duplicates:
        existing = existing_identities(database_path)
        for row_number, key, manufacturer, sku in identity_rows:
            if key in existing:
                plan.issues.append(Issue(
                    row_number, f"{manufacturer} SKU {sku} already exists in the catalog"
                ))
    return plan


def _positive_integer(value: str, column: str, row_number: int, plan: ImportPlan) -> int | None:
    if not value:
        return None
    try:
        number = int(value) if re.fullmatch(r"[0-9]+", value) else 0
    except ValueError:
        number = 0
    if not 0 < number <= 2**63 - 1:
        plan.issues.append(Issue(row_number, f"{column} must be a positive integer"))
        return None
    return number


def _positive_decimal(value: str, column: str, row_number: int, plan: ImportPlan) -> float | None:
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        number = 0
    if not math.isfinite(number) or number <= 0:
        plan.issues.append(Issue(row_number, f"{column} must be a positive number"))
        return None
    return number


def _valid_date(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_url(value: str) -> bool:
    if any(character.isspace() or ord(character) < 32 for character in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and (
            parsed.port is None or parsed.port > 0
        )
    except ValueError:
        return False
