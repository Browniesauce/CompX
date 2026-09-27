import sqlite3

from typer.testing import CliRunner

from compx.cli import app


runner = CliRunner()


def test_help_shows_application_and_init():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CompX" in result.output
    assert "Explore specs. Compare systems." in result.output
    assert "init" in result.output


def test_init_with_database_option_creates_expected_tables(tmp_path):
    path = tmp_path / "nested" / "catalog.sqlite3"

    result = runner.invoke(app, ["init", "--database", str(path)])

    assert result.exit_code == 0
    assert str(path) in result.output
    assert path.is_file()
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"manufacturers", "device_families", "configurations", "sources"} <= tables


def test_init_twice_preserves_existing_catalog_data(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    args = ["init", "--database", str(path)]
    assert runner.invoke(app, args).exit_code == 0
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO manufacturers (name) VALUES (?)", ("Synthetic Test Co",))

    second = runner.invoke(app, args)

    assert second.exit_code == 0
    with sqlite3.connect(path) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM manufacturers")]
    assert names == ["Synthetic Test Co"]


def test_init_uses_application_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    result = runner.invoke(app, ["init"])

    expected = tmp_path / "compx" / "catalog.sqlite3"
    assert result.exit_code == 0
    assert expected.is_file()
    assert str(expected) in result.output


def test_init_reports_database_error(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    path.write_text("not a SQLite database", encoding="utf-8")

    result = runner.invoke(app, ["init", "--database", str(path)])

    assert result.exit_code == 1
    assert "Could not initialize CompX catalog" in result.output
    assert path.read_text(encoding="utf-8") == "not a SQLite database"
