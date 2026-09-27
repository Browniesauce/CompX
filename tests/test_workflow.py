"""Full CLI workflow using the repository's synthetic, non-verified demo CSV."""

import csv
import io
import json
import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from compx.cli import app


runner = CliRunner()
DEMO_CSV = Path(__file__).resolve().parents[1] / "data" / "synthetic_demo.csv"


def _device_id(search_output: str, sku: str) -> int:
    return next(
        int(line.split("|", 1)[0].strip())
        for line in search_output.splitlines()
        if sku in line and "|" in line
    )


def test_synthetic_demo_workflow_is_safe_and_complete(tmp_path):
    database = tmp_path / "catalog.sqlite3"
    database_option = ["--database", str(database)]

    initialized = runner.invoke(app, ["init", *database_option])
    assert initialized.exit_code == 0
    empty_catalog = database.read_bytes()

    preview = runner.invoke(app, ["import", str(DEMO_CSV), "--dry-run", *database_option])
    assert preview.exit_code == 0
    assert "2/2 rows valid" in preview.output
    assert database.read_bytes() == empty_catalog

    with DEMO_CSV.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames
        rows = list(reader)
    rows[1]["checked_on"] = "2026-02-30"
    invalid_csv = tmp_path / "synthetic_invalid.csv"
    with invalid_csv.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    invalid_preview = runner.invoke(app, [
        "import", str(invalid_csv), "--dry-run", *database_option,
    ])
    invalid_import = runner.invoke(app, ["import", str(invalid_csv), *database_option])
    assert invalid_preview.exit_code == invalid_import.exit_code == 1
    assert "row 3: checked_on" in invalid_preview.output
    assert database.read_bytes() == empty_catalog

    imported = runner.invoke(app, ["import", str(DEMO_CSV), *database_option])
    assert imported.exit_code == 0
    assert "Imported 2 configurations and 2 sources" in imported.output

    search = runner.invoke(app, ["search", "Demo", *database_option])
    assert search.exit_code == 0
    laptop_id = _device_id(search.output, "DEMO-LAP-001")
    desktop_id = _device_id(search.output, "DEMO-DESK-002")
    assert laptop_id != desktop_id

    shown = runner.invoke(app, ["show", str(laptop_id), *database_option])
    assert shown.exit_code == 0
    assert "SKU: DEMO-LAP-001" in shown.output
    assert "GPU: Not provided" in shown.output
    assert "https://example.test/compx/laptop" in shown.output
    assert "Checked on: 2026-09-27" in shown.output

    compared = runner.invoke(app, [
        "compare", str(laptop_id), str(desktop_id), *database_option,
    ])
    assert compared.exit_code == 0
    assert "Different" in compared.output
    assert "Unknown" in compared.output
    assert "Not applicable" in compared.output
    assert "https://example.test/compx/desktop" in compared.output

    csv_export = runner.invoke(app, ["export", "--format", "csv", *database_option])
    json_export = runner.invoke(app, ["export", "--format", "json", *database_option])
    assert csv_export.exit_code == json_export.exit_code == 0
    assert csv_export.stderr == json_export.stderr == ""
    csv_rows = list(csv.DictReader(io.StringIO(csv_export.stdout)))
    json_rows = json.loads(json_export.stdout)["configurations"]
    assert {int(row["id"]) for row in csv_rows} == {laptop_id, desktop_id}
    assert {row["id"] for row in json_rows} == {laptop_id, desktop_id}
    assert all(len(json.loads(row["sources_json"])) == 1 for row in csv_rows)
    assert all(len(row["sources"]) == 1 for row in json_rows)
    assert next(row for row in csv_rows if row["sku"] == "DEMO-LAP-001")["gpu"] == ""
    assert next(row for row in json_rows if row["sku"] == "DEMO-LAP-001")["gpu"] is None

    assert runner.invoke(app, ["init", *database_option]).exit_code == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM configurations").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM sources").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_default_database_path_is_shared_by_all_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    database = tmp_path / "compx" / "catalog.sqlite3"

    assert runner.invoke(app, ["init"]).exit_code == 0
    assert database.exists()
    assert runner.invoke(app, ["import", str(DEMO_CSV), "--dry-run"]).exit_code == 0
    assert runner.invoke(app, ["import", str(DEMO_CSV)]).exit_code == 0
    assert "DEMO-LAP-001" in runner.invoke(app, ["search"]).output
    assert "SKU: DEMO-LAP-001" in runner.invoke(app, ["show", "1"]).output
    assert "Not applicable" in runner.invoke(app, ["compare", "1", "2"]).output
    export = runner.invoke(app, ["export", "--format", "json"])
    assert export.exit_code == 0
    assert len(json.loads(export.stdout)["configurations"]) == 2
