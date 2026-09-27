"""Parameterized catalog reads and transactional CSV writes."""

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional, Sequence

from compx.database import connect, init_database
from compx.models import CsvRecord, DeviceDetails, SearchResult, SourceRecord
from compx.revision_data import add_revision, snapshot

if TYPE_CHECKING:
    from compx.sync import SyncItem


class ExistingConfigurationError(Exception):
    """A configuration appeared in the catalog before commit."""

    def __init__(self, row_number: int, manufacturer: str, sku: str):
        self.row_number = row_number
        super().__init__(f"row {row_number}: {manufacturer} SKU {sku} already exists in the catalog")


class CatalogNotReadyError(Exception):
    """The catalog is missing or needs setup before read commands can run."""


def existing_identities(path: Path) -> set[tuple[str, str]]:
    """Read manufacturer/SKU keys; an absent catalog is an empty catalog."""
    path = Path(path).expanduser()
    if not path.exists():
        return set()
    with closing(connect(path, read_only=True)) as connection:
        return existing_identities_in_connection(connection)


def insert_records(path: Path, records: Sequence[CsvRecord]) -> None:
    """Insert every validated record and source in one transaction."""
    init_database(path)
    with closing(connect(path)) as connection:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = existing_identities_in_connection(connection)
            for record in records:
                if record.identity in existing:
                    raise ExistingConfigurationError(
                        record.row_number, record.manufacturer, record.sku
                    )
            manufacturer_ids = {
                name.casefold(): manufacturer_id
                for manufacturer_id, name in connection.execute(
                    "SELECT id, name FROM manufacturers"
                )
            }
            family_ids = {
                (manufacturer_id, device_type, product_family.casefold(), model.casefold()): family_id
                for family_id, manufacturer_id, device_type, product_family, model
                in connection.execute("""
                    SELECT id, manufacturer_id, device_type, product_family, model
                    FROM device_families WHERE product_family IS NOT NULL
                """)
            }
            for record in records:
                manufacturer_key = record.manufacturer.casefold()
                manufacturer_id = manufacturer_ids.get(manufacturer_key)
                if manufacturer_id is None:
                    cursor = connection.execute(
                        "INSERT INTO manufacturers (name) VALUES (?)", (record.manufacturer,)
                    )
                    manufacturer_id = cursor.lastrowid
                    manufacturer_ids[manufacturer_key] = manufacturer_id
                family_key = (
                    manufacturer_id, record.device_type,
                    record.product_family.casefold(), record.model_name.casefold(),
                )
                family_id = family_ids.get(family_key)
                if family_id is None:
                    cursor = connection.execute("""
                        INSERT INTO device_families
                            (manufacturer_id, device_type, product_family, model)
                        VALUES (?, ?, ?, ?)
                    """, (
                        manufacturer_id, record.device_type,
                        record.product_family, record.model_name,
                    ))
                    family_id = cursor.lastrowid
                    family_ids[family_key] = family_id
                cursor = connection.execute("""
                    INSERT INTO configurations (
                        family_id, configuration_key, sku, region, model_year,
                        cpu, gpu, ram_gb, storage_gb, display_size_inches,
                        battery_wh, weight_g, form_factor, power_supply_w
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    family_id, record.sku, record.sku, record.region, record.model_year,
                    record.cpu, record.gpu, record.ram_gb, record.storage_gb,
                    record.display_size_in, record.battery_wh, record.weight_g,
                    record.form_factor, record.psu_watts,
                ))
                connection.execute("""
                    INSERT INTO sources (configuration_id, source_url, checked_on)
                    VALUES (?, ?, ?)
                """, (cursor.lastrowid, record.source_url, record.checked_on))
                add_revision(
                    connection, cursor.lastrowid, "initial", None,
                    snapshot(connection, cursor.lastrowid),
                    record.source_url, record.checked_on,
                )


def existing_identities_in_connection(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    rows = connection.execute("""
        SELECT manufacturers.name, configurations.sku
        FROM configurations
        JOIN device_families ON configurations.family_id = device_families.id
        JOIN manufacturers ON device_families.manufacturer_id = manufacturers.id
        WHERE configurations.sku IS NOT NULL
    """)
    return {(manufacturer.casefold(), sku.casefold()) for manufacturer, sku in rows}


def _family_for_record(
    connection: sqlite3.Connection, record: CsvRecord, configuration_id: int | None,
) -> int:
    """Find or create the target family without altering other configurations."""
    manufacturer_id = None
    if configuration_id is not None:
        row = connection.execute("""
            SELECT f.manufacturer_id FROM configurations AS c
            JOIN device_families AS f ON f.id = c.family_id WHERE c.id = ?
        """, (configuration_id,)).fetchone()
        manufacturer_id = row[0]
    else:
        for candidate_id, name in connection.execute("SELECT id, name FROM manufacturers ORDER BY id"):
            if name.casefold() == record.manufacturer.casefold():
                manufacturer_id = candidate_id
                break
    if manufacturer_id is None:
        manufacturer_id = connection.execute(
            "INSERT INTO manufacturers (name) VALUES (?)", (record.manufacturer,)
        ).lastrowid
    for family_id, device_type, product_family, model in connection.execute("""
        SELECT id, device_type, product_family, model FROM device_families
        WHERE manufacturer_id = ? ORDER BY id
    """, (manufacturer_id,)):
        if (device_type == record.device_type and
                (product_family or "").casefold() == record.product_family.casefold() and
                model.casefold() == record.model_name.casefold()):
            return family_id
    return connection.execute("""
        INSERT INTO device_families (manufacturer_id, device_type, product_family, model)
        VALUES (?, ?, ?, ?)
    """, (
        manufacturer_id, record.device_type, record.product_family, record.model_name,
    )).lastrowid


def apply_sync_items(connection: sqlite3.Connection, items: Sequence["SyncItem"]) -> None:
    """Write a previously validated sync plan inside the caller's transaction."""
    for item in items:
        if item.classification == "Unchanged":
            continue
        record = item.record
        before = None
        if item.classification == "New":
            family_id = _family_for_record(connection, record, None)
            configuration_id = connection.execute("""
                INSERT INTO configurations (
                    family_id, configuration_key, sku, region, model_year, cpu, gpu,
                    ram_gb, storage_gb, display_size_inches, battery_wh, weight_g,
                    form_factor, power_supply_w
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                family_id, record.sku, record.sku, record.region, record.model_year,
                record.cpu, record.gpu, record.ram_gb, record.storage_gb,
                record.display_size_in, record.battery_wh, record.weight_g,
                record.form_factor, record.psu_watts,
            )).lastrowid
        else:
            configuration_id = item.configuration_id
            before = snapshot(connection, configuration_id)
            if item.classification == "Changed":
                changed_labels = {change.label for change in item.changes}
                if changed_labels & {"Product family", "Model name", "Device type"}:
                    family_id = _family_for_record(connection, record, configuration_id)
                else:
                    family_id = connection.execute(
                        "SELECT family_id FROM configurations WHERE id = ?",
                        (configuration_id,),
                    ).fetchone()[0]

                def updated(label: str, key: str, incoming: object) -> object:
                    return incoming if label in changed_labels else before[key]

                connection.execute("""
                    UPDATE configurations SET
                        family_id = ?, region = ?, model_year = ?, cpu = ?, gpu = ?,
                        ram_gb = ?, storage_gb = ?, display_size_inches = ?,
                        battery_wh = ?, weight_g = ?, form_factor = ?, power_supply_w = ?
                    WHERE id = ?
                """, (
                    family_id,
                    updated("Region", "region", record.region),
                    updated("Model year", "model_year", record.model_year),
                    updated("CPU", "cpu", record.cpu),
                    updated("GPU", "gpu", record.gpu),
                    updated("RAM", "ram_gb", record.ram_gb),
                    updated("Storage", "storage_gb", record.storage_gb),
                    updated("Display size", "display_size_inches", record.display_size_in),
                    updated("Battery", "battery_wh", record.battery_wh),
                    updated("Weight", "weight_g", record.weight_g),
                    updated("Form factor", "form_factor", record.form_factor),
                    updated("Power supply", "power_supply_w", record.psu_watts),
                    configuration_id,
                ))
        if item.add_source:
            connection.execute("""
                INSERT INTO sources (configuration_id, source_url, checked_on)
                VALUES (?, ?, ?)
            """, (configuration_id, record.source_url, record.checked_on))
        add_revision(
            connection, configuration_id, item.revision_type, before,
            snapshot(connection, configuration_id), record.source_url, record.checked_on,
        )


@contextmanager
def _read_catalog(path: Path) -> Iterator[sqlite3.Connection]:
    path = Path(path).expanduser()
    if not path.is_file():
        raise CatalogNotReadyError(
            f"Catalog not found at {path}. Run compx init with --database set to this path."
        )
    with closing(connect(path, read_only=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "unicode_casefold", 1,
            lambda value: value.casefold() if isinstance(value, str) else "",
        )
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        family_columns = {row[1] for row in connection.execute("PRAGMA table_info(device_families)")}
        configuration_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(configurations)")
        }
        if not {"manufacturers", "device_families", "configurations", "sources"} <= tables or (
            "product_family" not in family_columns or "model_year" not in configuration_columns
        ):
            raise CatalogNotReadyError(
                f"Catalog at {path} needs setup or a Phase 2 schema upgrade. "
                "Run compx init with --database set to this path."
            )
        yield connection


def search_configurations(
    path: Path,
    *,
    query: Optional[str],
    device_type: Optional[str],
    manufacturer: Optional[str],
    cpu: Optional[str],
    gpu: Optional[str],
    ram_min: Optional[int],
    storage_min: Optional[int],
    limit: int,
) -> list[SearchResult]:
    """Apply all supplied filters and return stable configuration IDs."""
    conditions: list[str] = []
    parameters: list[object] = []
    if query:
        conditions.append("""(
            instr(unicode_casefold(m.name), ?) > 0 OR
            instr(unicode_casefold(f.product_family), ?) > 0 OR
            instr(unicode_casefold(f.model), ?) > 0 OR
            instr(unicode_casefold(c.sku), ?) > 0
        )""")
        parameters.extend([query.casefold()] * 4)
    if device_type:
        conditions.append("f.device_type = ?")
        parameters.append(device_type)
    if manufacturer:
        conditions.append("unicode_casefold(m.name) = ?")
        parameters.append(manufacturer.casefold())
    if cpu:
        conditions.append("instr(unicode_casefold(c.cpu), ?) > 0")
        parameters.append(cpu.casefold())
    if gpu:
        conditions.append("instr(unicode_casefold(c.gpu), ?) > 0")
        parameters.append(gpu.casefold())
    if ram_min is not None:
        conditions.append("c.ram_gb >= ?")
        parameters.append(ram_min)
    if storage_min is not None:
        conditions.append("c.storage_gb >= ?")
        parameters.append(storage_min)
    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    parameters.append(limit)
    sql = """
        SELECT c.id, m.name AS manufacturer, f.device_type,
               f.product_family, f.model AS model_name, c.sku,
               c.cpu, c.gpu, c.ram_gb, c.storage_gb
        FROM configurations AS c
        JOIN device_families AS f ON c.family_id = f.id
        JOIN manufacturers AS m ON f.manufacturer_id = m.id
    """ + where_clause + """
        ORDER BY unicode_casefold(m.name), unicode_casefold(f.model),
                 unicode_casefold(c.sku), c.id
        LIMIT ?
    """
    with _read_catalog(path) as connection:
        return [SearchResult(**dict(row)) for row in connection.execute(sql, parameters)]


def get_configuration(path: Path, device_id: int) -> Optional[DeviceDetails]:
    """Fetch one configuration and all its source records by primary key."""
    with _read_catalog(path) as connection:
        return _get_configuration(connection, device_id)


def get_configurations(path: Path, device_ids: Sequence[int]) -> list[DeviceDetails]:
    """Load selected details in the requested search order, including sources."""
    with _read_catalog(path) as connection:
        records = []
        for device_id in device_ids:
            record = _get_configuration(connection, device_id)
            if record is None:
                raise CatalogNotReadyError(
                    f"Configuration {device_id} changed during export; try again."
                )
            records.append(record)
        return records


def _get_configuration(connection: sqlite3.Connection, device_id: int) -> Optional[DeviceDetails]:
    row = connection.execute("""
            SELECT c.id, m.name AS manufacturer, f.product_family,
                   f.model AS model_name, f.device_type, c.sku,
                   c.configuration_key, c.part_number, c.region, c.model_year,
                   c.cpu, c.gpu, c.ram_gb, c.storage_gb,
                   c.display_size_inches, c.battery_wh, c.weight_g,
                   c.form_factor, c.power_supply_w
            FROM configurations AS c
            JOIN device_families AS f ON c.family_id = f.id
            JOIN manufacturers AS m ON f.manufacturer_id = m.id
            WHERE c.id = ?
    """, (device_id,)).fetchone()
    if row is None:
        return None
    source_rows = connection.execute("""
            SELECT source_url AS url, source_name AS title, checked_on
            FROM sources WHERE configuration_id = ? ORDER BY id
    """, (device_id,))
    sources = tuple(SourceRecord(**dict(source)) for source in source_rows)
    return DeviceDetails(**dict(row), sources=sources)
