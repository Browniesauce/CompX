"""Phase 6 examples use invented configurations and reserved example.test URLs."""

import csv
import json
import sqlite3

from typer.testing import CliRunner

from compx.cli import app
from compx.database import init_database

HEADERS = (
    "manufacturer", "product_family", "model_name", "device_type", "sku",
    "source_url", "checked_on", "region", "model_year", "cpu", "gpu",
    "ram_gb", "storage_gb", "display_size_in", "battery_wh", "weight_g",
    "form_factor", "psu_watts",
)
runner = CliRunner()


def row(sku="TEST-001", **changes):
    result = {
        "manufacturer": "Synthetic Test Co",
        "product_family": "Demo Family",
        "model_name": "Demo Model",
        "device_type": "laptop",
        "sku": sku,
        "source_url": f"https://example.test/{sku.lower()}",
        "checked_on": "2026-09-27",
        "ram_gb": "16",
    }
    result.update(changes)
    return result


def csv_file(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def command(name, path, *args):
    return runner.invoke(app, [name, *map(str, args), "--database", str(path)])


def count(path, table):
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_preview_classifies_all_kinds_without_changing_database(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    original = csv_file(tmp_path / "original.csv", [row(), row("TEST-002"), row("TEST-003")])
    assert command("import", db, original).exit_code == 0
    before = db.read_bytes()
    refreshed = csv_file(tmp_path / "refreshed.csv", [
        row(),
        row("TEST-002", ram_gb="32", gpu="Example GPU"),
        row("TEST-003", checked_on="2026-09-28"),
        row("TEST-004"),
    ])

    result = command("sync", db, refreshed)

    assert result.exit_code == 0
    for label in ("Unchanged", "Changed", "Source-only update", "New"):
        assert f"{label} 1" in result.output
    assert "RAM: 16 GB -> 32 GB" in result.output
    assert "GPU: Not provided -> Example GPU (data update)" in result.output
    assert db.read_bytes() == before
    assert count(db, "configurations") == 3
    assert count(db, "revisions") == 3


def test_sync_preview_on_absent_catalog_does_not_create_it(tmp_path):
    db = tmp_path / "missing" / "catalog.sqlite3"
    source = csv_file(tmp_path / "source.csv", [row()])
    result = command("sync", db, source)
    assert result.exit_code == 0
    assert "New 1" in result.output
    assert not db.exists()


def test_apply_preserves_sources_history_ids_and_omitted_configuration(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    source = csv_file(tmp_path / "source.csv", [row(), row("TEST-002")])
    assert command("import", db, source).exit_code == 0
    refreshed = csv_file(tmp_path / "refreshed.csv", [
        row(ram_gb="32", gpu="Example GPU", checked_on="2026-09-28"),
        row("TEST-003"),
    ])

    result = command("sync", db, refreshed, "--apply")

    assert result.exit_code == 0
    assert "Applied all planned changes" in result.output
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT id, ram_gb, gpu FROM configurations WHERE sku = 'TEST-001'"
        ).fetchone() == (1, 32, "Example GPU")
        assert connection.execute(
            "SELECT id, ram_gb FROM configurations WHERE sku = 'TEST-002'"
        ).fetchone() == (2, 16)
        assert connection.execute(
            "SELECT source_url, checked_on FROM sources WHERE configuration_id = 1 ORDER BY id"
        ).fetchall() == [
            ("https://example.test/test-001", "2026-09-27"),
            ("https://example.test/test-001", "2026-09-28"),
        ]
        revisions = connection.execute(
            "SELECT change_type, before_json, after_json FROM revisions "
            "WHERE configuration_id = 1 ORDER BY id"
        ).fetchall()
    assert [revision[0] for revision in revisions] == ["initial", "hardware"]
    assert json.loads(revisions[1][1])["gpu"] is None
    assert json.loads(revisions[1][2])["gpu"] == "Example GPU"
    assert count(db, "configurations") == 3
    assert count(db, "revisions") == 4
    assert "TEST-003" in command("search", db).output
    assert "Example GPU" in command("show", db, 1).output
    assert command("compare", db, 1, 2).exit_code == 0
    assert "TEST-003" in command("export", db, "--format", "csv").output
    assert "TEST-003" in command("export", db, "--format", "json").output


def test_source_only_and_unknown_to_known_revisions(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    original = csv_file(tmp_path / "original.csv", [row(ram_gb="")])
    assert command("import", db, original).exit_code == 0
    source_update = csv_file(tmp_path / "source.csv", [row(ram_gb="", checked_on="2026-09-28")])
    assert command("sync", db, source_update, "--apply").exit_code == 0
    data_update = csv_file(tmp_path / "data.csv", [row(checked_on="2026-09-28")])
    preview = command("sync", db, data_update)
    assert "Changed 1" in preview.output
    assert "RAM: Not provided -> 16 GB (data update)" in preview.output
    assert command("sync", db, data_update, "--apply").exit_code == 0
    with sqlite3.connect(db) as connection:
        types = [r[0] for r in connection.execute(
            "SELECT change_type FROM revisions WHERE configuration_id = 1 ORDER BY id"
        )]
    assert types == ["initial", "source_only", "data_update"]
    history = command("history", db, 1)
    assert history.exit_code == 0
    assert history.output.index("Initial record") < history.output.index("Source-only update")
    assert history.output.index("Source-only update") < history.output.index("Data update")
    assert "RAM: Not provided -> 16 GB (data update)" in history.output
    assert "Source added: https://example.test/test-001 (checked 2026-09-28)" in history.output
    assert "Update source: https://example.test/test-001" in history.output
    assert "Checked on: 2026-09-28" in history.output
    repeated = command("sync", db, data_update, "--apply")
    assert repeated.exit_code == 0
    assert "Unchanged 1" in repeated.output
    assert count(db, "revisions") == 3
    assert count(db, "sources") == 2


def test_family_change_moves_only_matched_configuration(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    original = csv_file(tmp_path / "original.csv", [row(), row("TEST-002")])
    assert command("import", db, original).exit_code == 0
    changed = csv_file(tmp_path / "changed.csv", [
        row(product_family="New Demo Family", model_name="New Demo Model"),
    ])
    preview = command("sync", db, changed)
    assert preview.exit_code == 0
    assert "Product family: Demo Family -> New Demo Family" in preview.output
    assert command("sync", db, changed, "--apply").exit_code == 0
    with sqlite3.connect(db) as connection:
        families = connection.execute("""
            SELECT c.id, f.product_family, f.model FROM configurations AS c
            JOIN device_families AS f ON f.id = c.family_id ORDER BY c.id
        """).fetchall()
        revision_type = connection.execute("""
            SELECT change_type FROM revisions WHERE configuration_id = 1 ORDER BY id DESC LIMIT 1
        """).fetchone()[0]
    assert families == [
        (1, "New Demo Family", "New Demo Model"),
        (2, "Demo Family", "Demo Model"),
    ]
    assert revision_type == "data_update"


def test_known_to_unknown_remains_null_in_history_and_export(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    assert command("import", db, csv_file(tmp_path / "original.csv", [row()])).exit_code == 0
    refresh = csv_file(tmp_path / "refresh.csv", [row(ram_gb="")])
    preview = command("sync", db, refresh)
    assert "RAM: 16 GB -> Not provided (data update)" in preview.output
    assert command("sync", db, refresh, "--apply").exit_code == 0
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT ram_gb FROM configurations WHERE id = 1").fetchone() == (None,)
    history = command("history", db, 1)
    assert "RAM: 16 GB -> Not provided (data update)" in history.output
    export = command("export", db, "--format", "json")
    assert export.exit_code == 0
    assert json.loads(export.output)["configurations"][0]["ram_gb"] is None


def test_case_and_spacing_only_text_does_not_claim_hardware_change(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    original = csv_file(tmp_path / "original.csv", [row(cpu="Example Processor")])
    assert command("import", db, original).exit_code == 0
    refresh = csv_file(tmp_path / "refresh.csv", [row(
        manufacturer="SYNTHETIC TEST CO", product_family="demo family",
        model_name="Demo  Model", sku="test-001", cpu="example  processor",
    )])
    preview = command("sync", db, refresh)
    assert preview.exit_code == 0
    assert "Unchanged 1" in preview.output
    assert command("sync", db, refresh, "--apply").exit_code == 0
    assert count(db, "revisions") == 1
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT cpu FROM configurations WHERE id = 1").fetchone() == (
            "Example Processor",
        )


def test_invalid_and_duplicate_rows_block_every_write(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    assert command("import", db, csv_file(tmp_path / "original.csv", [row()])).exit_code == 0
    before = db.read_bytes()
    source = csv_file(tmp_path / "bad.csv", [
        row("TEST-002"), row("test-002"), row("TEST-003", checked_on="2026-02-30"),
    ])
    result = command("sync", db, source, "--apply")
    assert result.exit_code == 1
    assert "duplicate manufacturer + SKU" in result.output
    assert "checked_on must be a real date" in result.output
    assert "Conflict 2" in result.output
    assert "Invalid 1" in result.output
    assert db.read_bytes() == before
    assert count(db, "configurations") == 1


def test_ambiguous_identity_is_conflict(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    init_database(db)
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO manufacturers (name) VALUES ('Synthetic Test Co')")
        connection.execute("""
            INSERT INTO device_families (manufacturer_id, device_type, product_family, model)
            VALUES (1, 'laptop', 'Demo Family', 'Demo Model')
        """)
        connection.execute("""
            INSERT INTO device_families (manufacturer_id, device_type, product_family, model)
            VALUES (1, 'desktop', 'Another Family', 'Another Model')
        """)
        connection.execute("""
            INSERT INTO configurations (family_id, configuration_key, sku)
            VALUES (1, 'TEST-001', 'TEST-001'), (2, 'TEST-001', 'TEST-001')
        """)
    source = csv_file(tmp_path / "source.csv", [row()])
    before = db.read_bytes()
    result = command("sync", db, source, "--apply")
    assert result.exit_code == 1
    assert "ambiguous manufacturer + SKU matches 2 configurations" in result.output
    assert "Conflict 1" in result.output
    assert db.read_bytes() == before


def test_write_failure_rolls_back_entire_sync(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    assert command("import", db, csv_file(tmp_path / "original.csv", [row()])).exit_code == 0
    with sqlite3.connect(db) as connection:
        connection.execute("""
            CREATE TRIGGER reject_sync_source BEFORE INSERT ON sources
            WHEN NEW.source_url = 'https://example.test/fail'
            BEGIN SELECT RAISE(ABORT, 'synthetic sync failure'); END
        """)
    refreshed = csv_file(tmp_path / "refreshed.csv", [
        row(ram_gb="32", checked_on="2026-09-28"),
        row("TEST-002", source_url="https://example.test/fail"),
    ])
    before = db.read_bytes()
    result = command("sync", db, refreshed, "--apply")
    assert result.exit_code == 1
    assert "synthetic sync failure" in result.output
    assert db.read_bytes() == before
    assert count(db, "configurations") == 1
    assert count(db, "sources") == 1
    assert count(db, "revisions") == 1


def test_init_migrates_existing_records_to_one_baseline(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    assert command("import", db, csv_file(tmp_path / "source.csv", [row()])).exit_code == 0
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE revisions")
    before_preview = db.read_bytes()
    preview = command("sync", db, csv_file(tmp_path / "preview.csv", [row()]))
    assert preview.exit_code == 0
    assert "Unchanged 1" in preview.output
    assert db.read_bytes() == before_preview
    init_database(db)
    init_database(db)
    with sqlite3.connect(db) as connection:
        revision = connection.execute(
            "SELECT change_type, before_json, after_json FROM revisions"
        ).fetchone()
    assert count(db, "revisions") == 1
    assert revision[0] == "initial"
    assert revision[1] is None
    assert json.loads(revision[2])["sources"][0]["url"] == "https://example.test/test-001"


def test_history_missing_id_and_invalid_id(tmp_path):
    db = tmp_path / "catalog.sqlite3"
    init_database(db)
    missing = command("history", db, 99)
    assert missing.exit_code == 1
    assert "No configuration with ID 99" in missing.output
    invalid = command("history", db, 0)
    assert invalid.exit_code == 2
    assert "Invalid device ID" in invalid.output
