"""SQLite connection and one-time schema setup for the local catalog."""

import os
import sqlite3
import sys
from contextlib import closing
from importlib import resources
from pathlib import Path

from compx.revision_data import add_revision, snapshot


def default_database_path() -> Path:
    """Return the user's default catalog location without creating it."""
    if sys.platform == "win32":
        data_home = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "compx" / "catalog.sqlite3"


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a catalog connection with SQLite foreign keys enabled."""
    if read_only:
        connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise sqlite3.DatabaseError("SQLite foreign-key enforcement is unavailable")
        return connection
    except Exception:
        connection.close()
        raise


def init_database(path: Path) -> Path:
    """Create or upgrade the catalog, preserving rows and recording baselines."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = resources.files("compx").joinpath("schema.sql").read_text(encoding="utf-8")
    with closing(connect(path)) as connection:
        connection.executescript(schema)
        _upgrade_phase_one_schema(connection)
        _baseline_revisions(connection)
    return path


def _baseline_revisions(connection: sqlite3.Connection) -> None:
    """Record the observed state of older configurations exactly once."""
    with connection:
        ids = [row[0] for row in connection.execute("""
            SELECT c.id FROM configurations AS c
            WHERE NOT EXISTS (
                SELECT 1 FROM revisions AS r WHERE r.configuration_id = c.id
            ) ORDER BY c.id
        """)]
        for configuration_id in ids:
            latest_source = connection.execute("""
                SELECT source_url, checked_on FROM sources
                WHERE configuration_id = ? ORDER BY id DESC LIMIT 1
            """, (configuration_id,)).fetchone()
            add_revision(
                connection, configuration_id, "initial", None,
                snapshot(connection, configuration_id),
                latest_source[0] if latest_source else None,
                latest_source[1] if latest_source else None,
            )


def _upgrade_phase_one_schema(connection: sqlite3.Connection) -> None:
    family_columns = {row[1] for row in connection.execute("PRAGMA table_info(device_families)")}
    configuration_columns = {row[1] for row in connection.execute("PRAGMA table_info(configurations)")}
    if "product_family" in family_columns and "model_year" in configuration_columns:
        return

    # Rebuild only the family table: SQLite cannot drop its old three-column
    # UNIQUE constraint in place. Configuration IDs and foreign keys are retained.
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        with connection:
            if "product_family" not in family_columns:
                connection.execute("""
                    CREATE TABLE device_families_new (
                        id INTEGER PRIMARY KEY,
                        manufacturer_id INTEGER NOT NULL REFERENCES manufacturers(id),
                        device_type TEXT NOT NULL CHECK (device_type IN ('laptop', 'desktop')),
                        product_family TEXT COLLATE NOCASE
                            CHECK (product_family IS NULL OR length(trim(product_family)) > 0),
                        model TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(model)) > 0),
                        UNIQUE (manufacturer_id, device_type, product_family, model)
                    )
                """)
                connection.execute("""
                    INSERT INTO device_families_new (id, manufacturer_id, device_type, model)
                    SELECT id, manufacturer_id, device_type, model FROM device_families
                """)
                connection.execute("DROP TABLE device_families")
                connection.execute("ALTER TABLE device_families_new RENAME TO device_families")
            if "model_year" not in configuration_columns:
                connection.execute(
                    "ALTER TABLE configurations ADD COLUMN model_year INTEGER CHECK (model_year > 0)"
                )
            problems = connection.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise sqlite3.IntegrityError("schema upgrade would break foreign keys")
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
