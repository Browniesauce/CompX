"""Plain-text presentation of catalog records."""

from collections.abc import Sequence
from textwrap import wrap

from compx.comparison import ComparisonResult
from compx.models import DeviceDetails, SearchResult

UNKNOWN = "Not provided"


def _text(value: object) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return UNKNOWN
    return str(value).replace("\r", " ").replace("\n", " ")


def _unit(value: object, unit: str) -> str:
    return UNKNOWN if value is None else f"{value} {unit}"


def format_search_results(records: Sequence[SearchResult]) -> str:
    """Build a readable table, keeping the database IDs visible."""
    if not records:
        return "No matching configurations."
    headers = ("ID", "Manufacturer", "Type", "Family", "Model", "SKU", "CPU", "GPU", "RAM", "Storage")
    rows = [
        (
            str(record.id), _text(record.manufacturer), _text(record.device_type),
            _text(record.product_family), _text(record.model_name), _text(record.sku),
            _text(record.cpu), _text(record.gpu), _unit(record.ram_gb, "GB"),
            _unit(record.storage_gb, "GB"),
        )
        for record in records
    ]
    widths = [max(len(row[index]) for row in [headers, *rows]) for index in range(len(headers))]

    def render(row: tuple[str, ...]) -> str:
        return " | ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip()

    return "\n".join([render(headers), "-+-".join("-" * width for width in widths), *(render(row) for row in rows)])


def format_device_details(device: DeviceDetails) -> str:
    """Show all stored fields and every associated source."""
    fields = (
        ("ID", device.id),
        ("Manufacturer", device.manufacturer),
        ("Product family", device.product_family),
        ("Model name", device.model_name),
        ("Device type", device.device_type),
        ("SKU", device.sku),
        ("Configuration key", device.configuration_key),
        ("Part number", device.part_number),
        ("Region", device.region),
        ("Model year", device.model_year),
        ("CPU", device.cpu),
        ("GPU", device.gpu),
        ("RAM", _unit(device.ram_gb, "GB")),
        ("Storage", _unit(device.storage_gb, "GB")),
        ("Display size", _unit(device.display_size_inches, "in")),
        ("Battery", _unit(device.battery_wh, "Wh")),
        ("Weight", _unit(device.weight_g, "g")),
        ("Form factor", device.form_factor),
        ("Power supply", _unit(device.power_supply_w, "W")),
    )
    lines = [f"{label}: {_text(value)}" for label, value in fields]
    lines.append("Sources:")
    if not device.sources:
        lines.append("  None recorded")
    for index, source in enumerate(device.sources, start=1):
        lines.extend((
            f"  {index}. URL: {_text(source.url)}",
            f"     Title: {_text(source.title)}",
            f"     Checked on: {_text(source.checked_on)}",
        ))
    return "\n".join(lines)


def format_comparison(result: ComparisonResult) -> str:
    """Render comparison values in fixed-width columns and list sources below."""
    widths = (18, 22, 22, 14)
    headers = ("Field", f"ID {result.first.id}", f"ID {result.second.id}", "Result")
    lines = []

    def add_row(cells: tuple[str, str, str, str]) -> None:
        parts = [wrap(_text(cell), width=width, break_long_words=True) or [""]
                 for cell, width in zip(cells, widths)]
        for index in range(max(map(len, parts))):
            lines.append(" | ".join(
                (part[index] if index < len(part) else "").ljust(width)
                for part, width in zip(parts, widths)
            ).rstrip())

    add_row(headers)
    lines.append("-+-".join("-" * width for width in widths))
    for row in result.rows:
        add_row((row.label, row.left, row.right, row.status))

    for device in (result.first, result.second):
        lines.append("")
        lines.append(f"Sources for ID {device.id}:")
        if not device.sources:
            lines.append("  None recorded")
        for source in device.sources:
            lines.append(f"  URL: {_text(source.url)}")
            lines.append(f"  Title: {_text(source.title)}")
            lines.append(f"  Checked on: {_text(source.checked_on)}")
    return "\n".join(lines)
