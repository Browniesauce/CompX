"""Search and detail tests use only clearly synthetic device records."""

import csv
import re
import sqlite3

import pytest
from typer.testing import CliRunner

from compx.catalog import SearchFilters, search_catalog
from compx.cli import app
from compx.database import init_database


runner = CliRunner()
HEADERS = [
    "manufacturer", "product_family", "model_name", "device_type", "sku",
    "source_url", "checked_on", "region", "model_year", "cpu", "gpu",
    "ram_gb", "storage_gb", "display_size_in", "battery_wh", "weight_g",
    "form_factor", "psu_watts",
]


@pytest.fixture
def synthetic_catalog(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    csv_path = tmp_path / "synthetic.csv"
    rows = [
        {
            "manufacturer": "Synthetic Alpha", "product_family": "Notebook Line",
            "model_name": "Zen Test", "device_type": "laptop", "sku": "LAP-001",
            "source_url": "https://example.test/lap-one", "checked_on": "2026-09-27",
            "region": "IN", "model_year": "2026", "cpu": "Intel i7 Test",
            "gpu": "RTX 4060 Test", "ram_gb": "16", "storage_gb": "512",
            "display_size_in": "15.6", "battery_wh": "60", "weight_g": "1500",
        },
        {
            "manufacturer": "Synthetic Alpha", "product_family": "Tower Line",
            "model_name": "Tower Test", "device_type": "desktop", "sku": "DESK-002",
            "source_url": "https://example.test/desk-two", "checked_on": "2026-09-27",
            "cpu": "AMD Ryzen 5 Test", "ram_gb": "32", "storage_gb": "1000",
            "form_factor": "tower", "psu_watts": "500",
        },
        {
            "manufacturer": "Synthetic Beta", "product_family": "Notebook Line",
            "model_name": "Work Test", "device_type": "laptop", "sku": "LAP-003",
            "source_url": "https://example.test/lap-three", "checked_on": "2026-09-27",
            "gpu": "Integrated Test",
        },
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    imported = runner.invoke(app, ["import", str(csv_path), "--database", str(path)])
    assert imported.exit_code == 0, imported.output
    with sqlite3.connect(path) as connection:
        lap_id = connection.execute(
            "SELECT id FROM configurations WHERE sku = ?", ("LAP-001",)
        ).fetchone()[0]
        connection.execute(
            "UPDATE sources SET source_name = ? WHERE configuration_id = ?",
            ("Synthetic spec sheet", lap_id),
        )
        connection.execute(
            "INSERT INTO sources (configuration_id, source_url, checked_on, source_name) "
            "VALUES (?, ?, ?, ?)",
            (lap_id, "https://example.test/lap-one/extra", "2026-09-28", None),
        )
    return path


def skus(path, **filters):
    return [row.sku for row in search_catalog(path, SearchFilters(**filters))]


def test_search_without_filters_has_stable_order_and_ids(synthetic_catalog):
    rows = search_catalog(synthetic_catalog, SearchFilters())

    assert [row.sku for row in rows] == ["DESK-002", "LAP-001", "LAP-003"]
    assert len({row.id for row in rows}) == 3
    assert all(row.id > 0 for row in rows)
    assert [row.id for row in rows] == [row.id for row in search_catalog(synthetic_catalog, SearchFilters())]


def test_default_limit_is_twenty(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    init_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO manufacturers (name) VALUES (?)", ("Synthetic Bulk Co",))
        connection.execute(
            "INSERT INTO device_families (manufacturer_id, device_type, product_family, model) "
            "VALUES (?, ?, ?, ?)",
            (1, "desktop", "Bulk Family", "Bulk Model"),
        )
        connection.executemany(
            "INSERT INTO configurations (family_id, configuration_key, sku) VALUES (?, ?, ?)",
            [(1, f"BULK-{index:03d}", f"BULK-{index:03d}") for index in range(25)],
        )

    assert len(search_catalog(path, SearchFilters())) == 20
    assert len(search_catalog(path, SearchFilters(limit=25))) == 25


@pytest.mark.parametrize("filters, expected", [
    ({"device_type": "laptop"}, ["LAP-001", "LAP-003"]),
    ({"device_type": "desktop"}, ["DESK-002"]),
    ({"manufacturer": "synthetic alpha"}, ["DESK-002", "LAP-001"]),
    ({"cpu": "I7"}, ["LAP-001"]),
    ({"gpu": "rtx 4060"}, ["LAP-001"]),
    ({"ram_min": 16}, ["DESK-002", "LAP-001"]),
    ({"ram_min": 32}, ["DESK-002"]),
    ({"storage_min": 512}, ["DESK-002", "LAP-001"]),
    ({"storage_min": 1000}, ["DESK-002"]),
    ({"limit": 1}, ["DESK-002"]),
])
def test_independent_filters(synthetic_catalog, filters, expected):
    assert skus(synthetic_catalog, **filters) == expected


@pytest.mark.parametrize("query, expected", [
    ("SYNTHETIC ALPHA", ["DESK-002", "LAP-001"]),
    ("notebook line", ["LAP-001", "LAP-003"]),
    ("zEn tEsT", ["LAP-001"]),
    ("lap-003", ["LAP-003"]),
])
def test_free_text_matches_each_identity_field_case_insensitively(synthetic_catalog, query, expected):
    assert skus(synthetic_catalog, query=query) == expected


def test_multiple_filters_use_and_logic(synthetic_catalog):
    assert skus(
        synthetic_catalog, query="notebook", device_type="laptop",
        manufacturer="SYNTHETIC ALPHA", gpu="RTX", ram_min=16,
        storage_min=512, limit=10,
    ) == ["LAP-001"]

    result = runner.invoke(app, [
        "search", "notebook", "--type", "laptop", "--manufacturer", "SYNTHETIC ALPHA",
        "--gpu", "RTX", "--ram-min", "16", "--storage-min", "512",
        "--limit", "10", "--database", str(synthetic_catalog),
    ])
    assert result.exit_code == 0
    assert "LAP-001" in result.output
    assert "LAP-003" not in result.output


def test_search_table_shows_stable_ids_and_unknown_values(synthetic_catalog):
    result = runner.invoke(app, ["search", "--database", str(synthetic_catalog)])
    lap_id = next(
        row.id for row in search_catalog(synthetic_catalog, SearchFilters())
        if row.sku == "LAP-001"
    )

    assert result.exit_code == 0
    assert all(label in result.output for label in ("ID", "Manufacturer", "Type", "Model", "SKU", "CPU", "GPU", "RAM", "Storage"))
    assert "Not provided" in result.output
    assert re.search(rf"^{lap_id}\s+\|", result.output, flags=re.MULTILINE)
    detail = runner.invoke(app, ["show", str(lap_id), "--database", str(synthetic_catalog)])
    assert detail.exit_code == 0
    assert "SKU: LAP-001" in detail.output


def test_no_matches_is_successful(synthetic_catalog):
    result = runner.invoke(app, ["search", "nothing-matches", "--database", str(synthetic_catalog)])

    assert result.exit_code == 0
    assert "No matching configurations." in result.output


@pytest.mark.parametrize("args, expected", [
    (["--type", "tablet"], "--type must be laptop or desktop"),
    (["--limit", "0"], "--limit must be a positive integer"),
    (["--limit", "9223372036854775808"], "--limit must be a positive integer"),
    (["--ram-min", "-1"], "--ram-min must be a positive integer"),
    (["--storage-min", "0"], "--storage-min must be a positive integer"),
    (["--manufacturer", "  "], "--manufacturer cannot be empty"),
])
def test_invalid_search_filters_return_nonzero(synthetic_catalog, args, expected):
    result = runner.invoke(app, ["search", *args, "--database", str(synthetic_catalog)])

    assert result.exit_code != 0
    assert expected in result.output


def test_sql_looking_text_and_like_wildcards_are_literal(synthetic_catalog):
    for query in ("%' OR 1=1 --", "%", "_", "!", "' OR 1=1 --"):
        assert skus(synthetic_catalog, query=query) == []
    assert skus(synthetic_catalog, cpu="%' OR 1=1 --") == []


def test_unicode_casefold_search_and_filters(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    init_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO manufacturers (name) VALUES (?)", ("Synthetic Ålpha",)
        )
        connection.execute(
            "INSERT INTO device_families "
            "(manufacturer_id, device_type, product_family, model) VALUES (?, ?, ?, ?)",
            (1, "laptop", "Mödel Line", "Démo"),
        )
        connection.execute(
            "INSERT INTO configurations "
            "(family_id, configuration_key, sku, cpu, gpu) VALUES (?, ?, ?, ?, ?)",
            (1, "SKU-Ü", "SKU-Ü", "CÖRE Test", "GrÄphics Test"),
        )

    for query in ("synthetic ålpha", "mödel line", "démo", "sku-ü"):
        assert skus(path, query=query) == ["SKU-Ü"]
    assert skus(path, manufacturer="synthetic ålpha") == ["SKU-Ü"]
    assert skus(path, cpu="cöre") == ["SKU-Ü"]
    assert skus(path, gpu="gräphics") == ["SKU-Ü"]


def test_show_displays_all_fields_and_all_sources(synthetic_catalog):
    lap_id = next(
        row.id for row in search_catalog(synthetic_catalog, SearchFilters())
        if row.sku == "LAP-001"
    )

    result = runner.invoke(app, ["show", str(lap_id), "--database", str(synthetic_catalog)])

    assert result.exit_code == 0
    for expected in (
        "Manufacturer: Synthetic Alpha", "Product family: Notebook Line",
        "Model name: Zen Test", "Device type: laptop", "SKU: LAP-001",
        "Region: IN", "Model year: 2026", "CPU: Intel i7 Test",
        "GPU: RTX 4060 Test", "RAM: 16 GB", "Storage: 512 GB",
        "Display size: 15.6 in", "Battery: 60.0 Wh", "Weight: 1500 g",
        "Form factor: Not provided", "Power supply: Not provided",
        "Title: Synthetic spec sheet", "Title: Not provided",
        "https://example.test/lap-one", "https://example.test/lap-one/extra",
        "Checked on: 2026-09-27", "Checked on: 2026-09-28",
    ):
        assert expected in result.output


def test_whitespace_only_legacy_values_display_as_unknown(synthetic_catalog):
    with sqlite3.connect(synthetic_catalog) as connection:
        connection.execute(
            "UPDATE configurations SET cpu = ? WHERE sku = ?", ("  ", "LAP-001")
        )
        connection.execute(
            "UPDATE sources SET source_name = ? WHERE configuration_id = "
            "(SELECT id FROM configurations WHERE sku = ?)",
            ("  ", "LAP-001"),
        )
    device_id = next(
        row.id for row in search_catalog(synthetic_catalog, SearchFilters())
        if row.sku == "LAP-001"
    )

    shown = runner.invoke(app, ["show", str(device_id), "--database", str(synthetic_catalog)])
    searched = runner.invoke(app, ["search", "LAP-001", "--database", str(synthetic_catalog)])

    assert shown.exit_code == searched.exit_code == 0
    assert "CPU: Not provided" in shown.output
    assert "Title: Not provided" in shown.output
    assert "Not provided" in searched.output


@pytest.mark.parametrize("device_id, expected", [
    ("999", "No configuration with ID 999"),
    ("0", "DEVICE_ID must be a positive integer"),
    ("9223372036854775808", "DEVICE_ID must be a positive integer"),
    ("not-an-id", "Invalid value"),
])
def test_show_missing_or_invalid_id(synthetic_catalog, device_id, expected):
    result = runner.invoke(app, ["show", device_id, "--database", str(synthetic_catalog)])

    assert result.exit_code != 0
    assert expected in result.output


def test_read_commands_report_missing_catalog_without_creating_it(tmp_path):
    path = tmp_path / "missing.sqlite3"

    search = runner.invoke(app, ["search", "--database", str(path)])
    show = runner.invoke(app, ["show", "1", "--database", str(path)])

    assert search.exit_code == 1
    assert show.exit_code == 1
    assert "Catalog not found" in search.output
    assert "Run compx init with --database set to this path" in show.output
    assert not path.exists()


def test_search_reports_outdated_schema_without_upgrading_it(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE manufacturers (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE device_families (id INTEGER PRIMARY KEY, model TEXT);
            CREATE TABLE configurations (id INTEGER PRIMARY KEY, sku TEXT);
            CREATE TABLE sources (id INTEGER PRIMARY KEY, source_url TEXT);
        """)
    before = path.read_bytes()

    result = runner.invoke(app, ["search", "--database", str(path)])

    assert result.exit_code == 1
    assert "needs setup or a Phase 2 schema upgrade" in result.output
    assert path.read_bytes() == before


def test_database_error_is_reported_without_traceback(tmp_path):
    path = tmp_path / "broken.sqlite3"
    path.write_text("not a SQLite catalog", encoding="utf-8")

    result = runner.invoke(app, ["show", "1", "--database", str(path)])

    assert result.exit_code == 1
    assert "Could not read catalog" in result.output
    assert "Traceback" not in result.output
