"""The public CompX command-line interface."""

import csv
import sqlite3
from pathlib import Path
from typing import Optional

import typer

from compx.catalog import (
    DEFAULT_SEARCH_LIMIT, MAX_SQLITE_INTEGER, InvalidFilter, SearchFilters, search_catalog,
    show_configuration,
)
from compx.comparison import MissingConfigurationError, compare_catalog
from compx.database import default_database_path, init_database
from compx.csv_import import inspect_csv
from compx.exporting import InvalidExportFormat, build_export, write_export
from compx.formatting import format_comparison, format_device_details, format_search_results
from compx.history import format_history, get_history
from compx.repository import CatalogNotReadyError, ExistingConfigurationError, insert_records
from compx.psref_adapter import UnsupportedSourceError
from compx.source_pdf import SourcePdfError
from compx.source_review import (
    build_review, format_review, serialize_candidate, validate_candidate, write_candidate,
)
from compx.sync import CatalogChangedError, apply_sync, format_sync_plan, plan_sync

app = typer.Typer(
    help="CompX — Explore specs. Compare systems.",
    no_args_is_help=True,
    add_completion=False,
)
source_app = typer.Typer(help="Review locally downloaded official source documents.")
app.add_typer(source_app, name="source")


@app.callback()
def main() -> None:
    """Explore specs. Compare systems."""


@source_app.command("parse")
def parse_source(
    file: Path = typer.Argument(..., help="Locally downloaded PSREF model-detail PDF."),
    source_url: str = typer.Option(..., "--source-url", help="Official PSREF PDF download URL."),
    sku: str = typer.Option(..., "--sku", help="10-character Lenovo model code."),
    device_type: str = typer.Option(
        ..., "--device-type", help="laptop or desktop (ThinkCentre M70s Gen 5)."
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", help="Save a CSV candidate after preview.", dir_okay=False,
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing candidate CSV."),
) -> None:
    """Extract a reviewable Lenovo CSV candidate without changing the catalog."""
    if overwrite and output is None:
        typer.echo("--overwrite requires --output.", err=True)
        raise typer.Exit(code=2)
    try:
        review = build_review(file, source_url, sku, device_type)
    except (SourcePdfError, UnsupportedSourceError, ValueError, OSError) as exc:
        typer.echo(f"Could not parse source: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(format_review(review))
    if not review.resolved:
        typer.echo("Candidate unresolved; no import-ready CSV saved.", err=True)
        raise typer.Exit(code=1)
    if output is None:
        typer.echo("Preview only; use --output PATH.csv after reviewing the PDF.")
        return
    try:
        payload = serialize_candidate(review)
        validate_candidate(payload)
        written = write_candidate(payload, output, file, overwrite=overwrite)
    except FileExistsError as exc:
        typer.echo(f"Candidate already exists: {output}. Use --overwrite to replace it.", err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not save candidate: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Saved review-required CSV candidate to {written}. Run compx sync to preview it.")


@app.command()
def init(
    database: Optional[Path] = typer.Option(
        None,
        "--database",
        help="SQLite database path (default: the user's application data directory).",
        dir_okay=False,
    ),
) -> None:
    """Create a local catalog, keeping any existing records."""
    path = database if database is not None else default_database_path()
    try:
        created_path = init_database(path)
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"Could not initialize CompX catalog at {path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"CompX catalog ready at {created_path}")


@app.command("import")
def import_csv(
    file: Path = typer.Argument(..., help="CSV file of exact configurations."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without changing the catalog."),
    database: Optional[Path] = typer.Option(
        None,
        "--database",
        help="SQLite database path (default: the user's application data directory).",
        dir_okay=False,
    ),
) -> None:
    """Validate and import a CSV file as one transaction."""
    path = database if database is not None else default_database_path()
    try:
        plan = inspect_csv(file, path)
    except (OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
        typer.echo(f"Could not validate CSV or catalog: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    for issue in plan.issues:
        typer.echo(str(issue), err=True)
    if plan.issues:
        typer.echo(
            f"{plan.valid_rows}/{plan.total_rows} rows valid; "
            "import blocked. No configurations saved.",
            err=True,
        )
        raise typer.Exit(code=1)
    if dry_run:
        typer.echo(
            f"Dry run: {plan.valid_rows}/{plan.total_rows} rows valid; "
            "import would succeed. No database changes made."
        )
        return

    try:
        insert_records(path, plan.records)
    except (ExistingConfigurationError, OSError, sqlite3.Error) as exc:
        typer.echo(f"Import failed; no configurations saved: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    count = len(plan.records)
    configuration_word = "configuration" if count == 1 else "configurations"
    source_word = "source" if count == 1 else "sources"
    typer.echo(f"Imported {count} {configuration_word} and {count} {source_word} into {path}.")


@app.command()
def sync(
    file: Path = typer.Argument(..., help="Refreshed CSV of exact configurations."),
    apply: bool = typer.Option(False, "--apply", help="Apply all planned changes."),
    database: Optional[Path] = typer.Option(
        None, "--database", help="SQLite database path.", dir_okay=False,
    ),
) -> None:
    """Preview or apply a source-backed CSV refresh."""
    path = database if database is not None else default_database_path()
    try:
        parsed, plan = plan_sync(file, path)
    except CatalogNotReadyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
        typer.echo(f"Could not validate CSV or catalog: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if plan.issues:
        typer.echo(format_sync_plan(plan), err=True)
        raise typer.Exit(code=1)
    if not apply:
        typer.echo(format_sync_plan(plan))
        return
    try:
        applied = apply_sync(path, parsed, plan)
    except (CatalogChangedError, OSError, sqlite3.Error, ValueError) as exc:
        typer.echo(f"Sync failed; no planned changes saved: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(format_sync_plan(applied, applied=True))


@app.command()
def history(
    device_id: int = typer.Argument(..., help="Configuration ID from compx search."),
    database: Optional[Path] = typer.Option(
        None, "--database", help="SQLite database path.", dir_okay=False,
    ),
) -> None:
    """Show a configuration's recorded changes in chronological order."""
    path = database if database is not None else default_database_path()
    try:
        revisions = get_history(path, device_id)
    except InvalidFilter as exc:
        typer.echo(f"Invalid device ID: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except CatalogNotReadyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, sqlite3.Error, ValueError) as exc:
        typer.echo(f"Could not read history at {path}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if revisions is None:
        typer.echo(f"No configuration with ID {device_id} in {path}.", err=True)
        raise typer.Exit(code=1)
    typer.echo(format_history(device_id, revisions))


@app.command()
def search(
    query: Optional[str] = typer.Argument(None, help="Text in manufacturer, family, model, or SKU."),
    device_type: Optional[str] = typer.Option(None, "--type", help="laptop or desktop."),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", help="Exact manufacturer name."),
    cpu: Optional[str] = typer.Option(None, "--cpu", help="Part of the CPU description."),
    gpu: Optional[str] = typer.Option(None, "--gpu", help="Part of the GPU description."),
    ram_min: Optional[int] = typer.Option(None, "--ram-min", help="Minimum installed RAM in GB."),
    storage_min: Optional[int] = typer.Option(None, "--storage-min", help="Minimum storage in GB."),
    limit: int = typer.Option(DEFAULT_SEARCH_LIMIT, "--limit", help="Maximum results (default: 20)."),
    database: Optional[Path] = typer.Option(
        None, "--database", help="SQLite database path.", dir_okay=False,
    ),
) -> None:
    """Find exact configurations using any combination of filters."""
    path = database if database is not None else default_database_path()
    filters = SearchFilters(
        query=query, device_type=device_type, manufacturer=manufacturer,
        cpu=cpu, gpu=gpu, ram_min=ram_min, storage_min=storage_min, limit=limit,
    )
    try:
        records = search_catalog(path, filters)
    except InvalidFilter as exc:
        typer.echo(f"Invalid search filter: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except CatalogNotReadyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"Could not read catalog at {path}. Check the database file and try again.", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(format_search_results(records))


@app.command()
def show(
    device_id: int = typer.Argument(..., help="Configuration ID from compx search."),
    database: Optional[Path] = typer.Option(
        None, "--database", help="SQLite database path.", dir_okay=False,
    ),
) -> None:
    """Display one exact configuration and all its sources."""
    path = database if database is not None else default_database_path()
    try:
        device = show_configuration(path, device_id)
    except InvalidFilter as exc:
        typer.echo(f"Invalid device ID: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except CatalogNotReadyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"Could not read catalog at {path}. Check the database file and try again.", err=True)
        raise typer.Exit(code=1) from exc
    if device is None:
        typer.echo(f"No configuration with ID {device_id} in {path}.", err=True)
        raise typer.Exit(code=1)
    typer.echo(format_device_details(device))


@app.command()
def compare(
    first_id: int = typer.Argument(..., help="First configuration ID from compx search."),
    second_id: int = typer.Argument(..., help="Second configuration ID from compx search."),
    database: Optional[Path] = typer.Option(
        None, "--database", help="SQLite database path.", dir_okay=False,
    ),
) -> None:
    """Compare two exact configurations and their source information."""
    path = database if database is not None else default_database_path()
    try:
        result = compare_catalog(path, first_id, second_id)
    except InvalidFilter as exc:
        typer.echo(f"Invalid device ID: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except MissingConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except CatalogNotReadyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"Could not read catalog at {path}. Check the database file and try again.", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(format_comparison(result))


@app.command("export")
def export_catalog_command(
    query: Optional[str] = typer.Argument(None, help="Text in manufacturer, family, model, or SKU."),
    output_format: str = typer.Option(..., "--format", help="csv or json."),
    output: Optional[Path] = typer.Option(
        None, "--output", help="Write to this file instead of standard output.", dir_okay=False,
    ),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing output file."),
    device_type: Optional[str] = typer.Option(None, "--type", help="laptop or desktop."),
    manufacturer: Optional[str] = typer.Option(None, "--manufacturer", help="Exact manufacturer name."),
    cpu: Optional[str] = typer.Option(None, "--cpu", help="Part of the CPU description."),
    gpu: Optional[str] = typer.Option(None, "--gpu", help="Part of the GPU description."),
    ram_min: Optional[int] = typer.Option(None, "--ram-min", help="Minimum installed RAM in GB."),
    storage_min: Optional[int] = typer.Option(None, "--storage-min", help="Minimum storage in GB."),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum results (default: all)."),
    database: Optional[Path] = typer.Option(
        None, "--database", help="SQLite database path.", dir_okay=False,
    ),
) -> None:
    """Export selected configurations and all their sources."""
    if overwrite and output is None:
        typer.echo("--overwrite requires --output.", err=True)
        raise typer.Exit(code=2)
    path = database if database is not None else default_database_path()
    filters = SearchFilters(
        query=query, device_type=device_type, manufacturer=manufacturer,
        cpu=cpu, gpu=gpu, ram_min=ram_min, storage_min=storage_min,
        limit=MAX_SQLITE_INTEGER if limit is None else limit,
    )
    try:
        payload, count = build_export(path, filters, output_format)
    except (InvalidExportFormat, InvalidFilter) as exc:
        typer.echo(f"Invalid export option: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except CatalogNotReadyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, sqlite3.Error) as exc:
        typer.echo(f"Could not read catalog at {path}. Check the database file and try again.", err=True)
        raise typer.Exit(code=1) from exc

    if output is None:
        typer.echo(payload, nl=False)
        return
    try:
        written_path = write_export(payload, output, path, overwrite=overwrite)
    except FileExistsError as exc:
        typer.echo(f"Output file already exists: {output}. Use --overwrite to replace it.", err=True)
        raise typer.Exit(code=1) from exc
    except (OSError, ValueError) as exc:
        typer.echo(f"Could not write export: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Exported {count} configurations to {written_path}.", err=True)
