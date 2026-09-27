"""Select, serialize, and safely write catalog exports."""

import csv
import io
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from compx.catalog import MAX_SQLITE_INTEGER, SearchFilters, search_catalog
from compx.models import DeviceDetails
from compx.repository import get_configurations

EXPORT_SCHEMA_VERSION = 1
CSV_COLUMNS = (
    "id", "manufacturer", "product_family", "model_name", "device_type",
    "configuration_key", "sku", "part_number", "region", "model_year",
    "cpu", "gpu", "ram_gb", "storage_gb", "display_size_inches",
    "battery_wh", "weight_g", "form_factor", "power_supply_w", "sources_json",
)
OPTIONAL_TEXT_FIELDS = (
    "product_family", "sku", "part_number", "region", "cpu", "gpu", "form_factor",
)


class InvalidExportFormat(ValueError):
    """Only documented export formats are accepted."""


def select_export_records(path: Path, filters: SearchFilters) -> list[DeviceDetails]:
    """Use search's validation, filters, limit, and stable ordering."""
    matches = search_catalog(path, filters)
    return get_configurations(path, [match.id for match in matches]) if matches else []


def _record_document(record: DeviceDetails) -> dict:
    values = asdict(record)
    for field in OPTIONAL_TEXT_FIELDS:
        if isinstance(values[field], str) and not values[field].strip():
            values[field] = None
    for source in values["sources"]:
        if isinstance(source["title"], str) and not source["title"].strip():
            source["title"] = None
    return values


def serialize_csv(records: list[DeviceDetails]) -> str:
    """One row per configuration; sources_json retains every source."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(CSV_COLUMNS)
    for record in records:
        values = _record_document(record)
        values["sources_json"] = json.dumps(values.pop("sources"), ensure_ascii=False)
        writer.writerow(["" if values[column] is None else values[column] for column in CSV_COLUMNS])
    return stream.getvalue()


def serialize_json(records: list[DeviceDetails]) -> str:
    """Emit a deterministic UTF-8-friendly document with explicit nulls."""
    document = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "configurations": [_record_document(record) for record in records],
    }
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def build_export(
    path: Path, filters: SearchFilters, output_format: str,
) -> tuple[str, int]:
    """Return serialized output and the number of selected configurations."""
    if output_format not in ("csv", "json"):
        raise InvalidExportFormat("--format must be csv or json")
    records = select_export_records(path, filters)
    payload = serialize_csv(records) if output_format == "csv" else serialize_json(records)
    return payload, len(records)


def write_export(
    payload: str, output_path: Path, database_path: Path, *, overwrite: bool,
) -> Path:
    """Write UTF-8 output without replacing an existing file by default."""
    output_path = Path(output_path).expanduser()
    if output_path.resolve() == Path(database_path).expanduser().resolve():
        raise ValueError("export output cannot be the catalog database file")
    if not overwrite:
        with output_path.open("x", encoding="utf-8", newline="") as file:
            file.write(payload)
        return output_path

    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output_path.parent,
            prefix=f".{output_path.name}.", delete=False,
        ) as file:
            temporary_path = Path(file.name)
            file.write(payload)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return output_path
