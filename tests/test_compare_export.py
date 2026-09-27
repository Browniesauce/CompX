"""Phase 4 tests use synthetic configurations and example.test sources only."""

import csv
import io
import json
import sqlite3
from contextlib import closing

import pytest
from typer.testing import CliRunner

from compx.catalog import MAX_SQLITE_INTEGER, SearchFilters, search_catalog
from compx.cli import app
from compx.comparison import FIELDS, compare_catalog
from compx.database import connect, init_database
from compx.exporting import CSV_COLUMNS, EXPORT_SCHEMA_VERSION, select_export_records


runner = CliRunner()


@pytest.fixture
def synthetic_catalog(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    init_database(path)
    with closing(connect(path)) as connection, connection:
        connection.executemany(
            "INSERT INTO manufacturers (id, name) VALUES (?, ?)",
            [(1, "Synthetic Alpha"), (2, "Synthetic Beta")],
        )
        connection.executemany("""
            INSERT INTO device_families
                (id, manufacturer_id, device_type, product_family, model)
            VALUES (?, ?, ?, ?, ?)
        """, [
            (1, 1, "laptop", "Notebook", "Model X"),
            (2, 1, "laptop", "Notebook", "Model Y"),
            (3, 2, "desktop", "Tower", 'Tower, "Pro"\nEdition — 测试'),
            (4, 2, "laptop", "Notebook", "Model Unknown"),
        ])
        connection.executemany("""
            INSERT INTO configurations (
                id, family_id, configuration_key, sku, region, model_year,
                cpu, gpu, ram_gb, storage_gb, display_size_inches, battery_wh,
                weight_g, form_factor, power_supply_w
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (1, 1, "L-001", "L-001", "IN", 2026, "Intel Core i7", "RTX 4060",
             16, 512, 15.6, 60.0, 1500, None, None),
            (2, 2, "L-002", "L-002", None, 2026, "  intel  CORE   I7 ", "RTX 4070",
             16, 1024, 15.6, None, 1500, None, None),
            (3, 3, "D-003", "D-003", None, None, "  ", None,
             None, 500, None, None, None, "mini tower", 600),
            (4, 4, "L-004", "L-004", None, None, None, None,
             None, None, None, None, None, None, None),
        ])
        connection.executemany("""
            INSERT INTO sources (configuration_id, source_url, checked_on, source_name)
            VALUES (?, ?, ?, ?)
        """, [
            (1, "https://example.test/lap-one", "2026-09-27", "Primary synthetic source"),
            (1, "https://example.test/lap-one/extra", "2026-09-28", None),
            (2, "https://example.test/lap-two", "2026-09-27", "Synthetic second source"),
            (3, "https://example.test/desk-three", "2026-09-27", 'Spec, "quoted"\n新'),
            (4, "https://example.test/lap-four", "2026-09-27", "  "),
        ])
    return path


def compare_rows(path, first, second):
    return {row.label: row for row in compare_catalog(path, first, second).rows}


def export_cli(path, fmt="csv", *extra):
    return runner.invoke(app, ["export", "--format", fmt, "--database", str(path), *extra])


def test_compare_classifies_same_and_different_known_values(synthetic_catalog):
    rows = compare_rows(synthetic_catalog, 1, 2)

    assert rows["CPU"].status == "Same"
    assert rows["CPU"].left != rows["CPU"].right
    assert rows["Manufacturer"].status == "Same"
    assert rows["GPU"].status == "Different"
    assert rows["Storage"].status == "Different"
    assert rows["RAM"].status == "Same"


def test_compare_unknown_on_one_and_both_sides(synthetic_catalog):
    one_unknown = compare_rows(synthetic_catalog, 1, 2)
    both_unknown = compare_rows(synthetic_catalog, 2, 4)

    assert one_unknown["Battery"].status == "Unknown"
    assert one_unknown["Battery"].right == "Not provided"
    assert both_unknown["Battery"].status == "Unknown"
    assert both_unknown["Battery"].left == both_unknown["Battery"].right == "Not provided"


def test_compare_category_specific_fields_and_mixed_types(synthetic_catalog):
    rows = compare_rows(synthetic_catalog, 1, 3)

    assert rows["Device type"].status == "Different"
    for label in ("Display size", "Battery", "Weight"):
        assert rows[label].status == "Not applicable"
        assert rows[label].right == "Not applicable"
    for label in ("Form factor", "Power supply"):
        assert rows[label].status == "Not applicable"
        assert rows[label].left == "Not applicable"
    assert rows["CPU"].status == "Unknown"
    assert compare_rows(synthetic_catalog, 1, 2)["Power supply"].status == "Not applicable"


def test_compare_field_order_sources_and_cli_output(synthetic_catalog):
    result = compare_catalog(synthetic_catalog, 1, 2)
    cli = runner.invoke(app, ["compare", "1", "2", "--database", str(synthetic_catalog)])

    assert [row.label for row in result.rows] == [field.label for field in FIELDS]
    assert cli.exit_code == 0
    assert "ID 1" in cli.output and "ID 2" in cli.output
    assert "Same" in cli.output and "Different" in cli.output and "Unknown" in cli.output
    assert cli.output.index("Manufacturer") < cli.output.index("CPU") < cli.output.index("Battery")
    assert "Sources for ID 1:" in cli.output
    assert "Sources for ID 2:" in cli.output
    assert "https://example.test/lap-one/extra" in cli.output
    assert "Checked on: 2026-09-28" in cli.output


@pytest.mark.parametrize("first, second, expected", [
    ("0", "2", "DEVICE_ID must be a positive integer"),
    ("1", "999", "No configuration with ID 999"),
    ("999", "1", "No configuration with ID 999"),
    ("not-an-id", "2", "Invalid value"),
])
def test_compare_invalid_and_missing_ids(synthetic_catalog, first, second, expected):
    result = runner.invoke(app, ["compare", first, second, "--database", str(synthetic_catalog)])

    assert result.exit_code != 0
    assert expected in result.output


def test_csv_header_order_all_records_and_sources(synthetic_catalog):
    result = export_cli(synthetic_catalog)
    parsed = list(csv.DictReader(io.StringIO(result.stdout)))

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.splitlines()[0].split(",") == list(CSV_COLUMNS)
    assert [row["sku"] for row in parsed] == ["L-001", "L-002", "L-004", "D-003"]
    assert [int(row["id"]) for row in parsed] == [row.id for row in search_catalog(
        synthetic_catalog, SearchFilters(limit=MAX_SQLITE_INTEGER)
    )]
    sources = json.loads(parsed[0]["sources_json"])
    assert len(sources) == 2
    assert sources[0] == {
        "url": "https://example.test/lap-one",
        "title": "Primary synthetic source",
        "checked_on": "2026-09-27",
    }
    assert parsed[3]["cpu"] == ""
    assert parsed[3]["ram_gb"] == ""


def test_csv_escaping_preserves_commas_quotes_newlines_and_unicode(synthetic_catalog):
    result = export_cli(synthetic_catalog)
    parsed = list(csv.DictReader(io.StringIO(result.stdout)))
    desktop = next(row for row in parsed if row["sku"] == "D-003")

    assert result.exit_code == 0
    assert desktop["model_name"] == 'Tower, "Pro"\nEdition — 测试'
    assert json.loads(desktop["sources_json"])[0]["title"] == 'Spec, "quoted"\n新'


def test_csv_filters_match_search_and_limit(synthetic_catalog):
    filters = SearchFilters(
        query="Notebook", device_type="laptop", manufacturer="synthetic alpha",
        cpu="CORE", ram_min=16, limit=1,
    )
    expected = [row.id for row in search_catalog(synthetic_catalog, filters)]
    result = export_cli(
        synthetic_catalog, "csv", "Notebook", "--type", "laptop",
        "--manufacturer", "synthetic alpha", "--cpu", "CORE",
        "--ram-min", "16", "--limit", "1",
    )
    parsed = list(csv.DictReader(io.StringIO(result.stdout)))

    assert result.exit_code == 0
    assert [int(row["id"]) for row in parsed] == expected == [1]


def test_csv_empty_match_still_has_header(synthetic_catalog):
    result = export_cli(synthetic_catalog, "csv", "nothing-matches")

    assert result.exit_code == 0
    assert list(csv.reader(io.StringIO(result.stdout))) == [list(CSV_COLUMNS)]


def test_json_structure_nulls_sources_and_stable_order(synthetic_catalog):
    first = export_cli(synthetic_catalog, "json")
    second = export_cli(synthetic_catalog, "json")
    document = json.loads(first.stdout)

    assert first.exit_code == second.exit_code == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    assert list(document) == ["schema_version", "configurations"]
    assert document["schema_version"] == EXPORT_SCHEMA_VERSION
    assert [record["sku"] for record in document["configurations"]] == [
        "L-001", "L-002", "L-004", "D-003",
    ]
    first_record = document["configurations"][0]
    assert first_record["id"] == 1
    assert first_record["ram_gb"] == 16
    assert len(first_record["sources"]) == 2
    assert document["configurations"][3]["cpu"] is None
    assert document["configurations"][3]["battery_wh"] is None
    assert document["configurations"][3]["sources"][0]["title"] == 'Spec, "quoted"\n新'
    assert document["configurations"][2]["sources"][0]["title"] is None


def test_json_filters_match_search_and_empty_result(synthetic_catalog):
    filtered = export_cli(
        synthetic_catalog, "json", "Notebook", "--type", "laptop",
        "--manufacturer", "Synthetic Alpha", "--ram-min", "16", "--limit", "1",
    )
    empty = export_cli(synthetic_catalog, "json", "nothing-matches")

    assert filtered.exit_code == empty.exit_code == 0
    assert [row["id"] for row in json.loads(filtered.stdout)["configurations"]] == [1]
    assert json.loads(empty.stdout) == {"schema_version": 1, "configurations": []}


def test_export_defaults_to_all_even_when_more_than_search_default(tmp_path):
    path = tmp_path / "bulk.sqlite3"
    init_database(path)
    with closing(connect(path)) as connection, connection:
        connection.execute("INSERT INTO manufacturers (name) VALUES (?)", ("Synthetic Bulk Co",))
        connection.execute(
            "INSERT INTO device_families (manufacturer_id, device_type, product_family, model) "
            "VALUES (?, ?, ?, ?)", (1, "desktop", "Bulk", "Bulk Model"),
        )
        connection.executemany(
            "INSERT INTO configurations (family_id, configuration_key, sku) VALUES (?, ?, ?)",
            [(1, f"BULK-{index:03d}", f"BULK-{index:03d}") for index in range(25)],
        )

    assert len(search_catalog(path, SearchFilters())) == 20
    assert len(select_export_records(path, SearchFilters(limit=MAX_SQLITE_INTEGER))) == 25
    assert len(json.loads(export_cli(path, "json").stdout)["configurations"]) == 25


def test_output_file_no_overwrite_by_default_and_explicit_overwrite(synthetic_catalog, tmp_path):
    output = tmp_path / "devices.csv"
    first = export_cli(synthetic_catalog, "csv", "--output", str(output))

    assert first.exit_code == 0
    assert first.stdout == ""
    assert "Exported 4 configurations" in first.stderr
    original = output.read_text(encoding="utf-8")
    assert len(list(csv.DictReader(io.StringIO(original)))) == 4

    second = export_cli(synthetic_catalog, "json", "--output", str(output))
    assert second.exit_code == 1
    assert "already exists" in second.stderr
    assert output.read_text(encoding="utf-8") == original

    overwritten = export_cli(synthetic_catalog, "json", "--output", str(output), "--overwrite")
    assert overwritten.exit_code == 0
    assert overwritten.stdout == ""
    assert len(json.loads(output.read_text(encoding="utf-8"))["configurations"]) == 4


def test_export_cannot_overwrite_catalog_database(synthetic_catalog):
    before = synthetic_catalog.read_bytes()

    result = export_cli(
        synthetic_catalog, "json", "--output", str(synthetic_catalog), "--overwrite",
    )

    assert result.exit_code == 1
    assert "cannot be the catalog database file" in result.stderr
    assert synthetic_catalog.read_bytes() == before
    with sqlite3.connect(synthetic_catalog) as connection:
        assert connection.execute("SELECT count(*) FROM configurations").fetchone()[0] == 4


@pytest.mark.parametrize("args, expected", [
    (["--format", "xml"], "--format must be csv or json"),
    (["--format", "csv", "--type", "tablet"], "--type must be laptop or desktop"),
    (["--format", "json", "--limit", "0"], "--limit must be a positive integer"),
    (["--format", "csv", "--overwrite"], "--overwrite requires --output"),
])
def test_invalid_export_options_return_nonzero(synthetic_catalog, args, expected):
    result = runner.invoke(app, ["export", *args, "--database", str(synthetic_catalog)])

    assert result.exit_code != 0
    assert expected in result.output


def test_compare_and_export_do_not_modify_catalog(synthetic_catalog):
    before = synthetic_catalog.read_bytes()

    assert runner.invoke(app, ["compare", "1", "3", "--database", str(synthetic_catalog)]).exit_code == 0
    assert export_cli(synthetic_catalog, "csv").exit_code == 0
    assert export_cli(synthetic_catalog, "json").exit_code == 0
    assert synthetic_catalog.read_bytes() == before
