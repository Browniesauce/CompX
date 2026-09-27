"""Classify stored values for side-by-side device comparison."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from compx.catalog import show_configuration
from compx.models import DeviceDetails

UNKNOWN = "Not provided"
NOT_APPLICABLE = "Not applicable"


class MissingConfigurationError(Exception):
    """A requested configuration ID does not exist."""


@dataclass(frozen=True)
class ComparisonField:
    label: str
    attribute: str
    unit: Optional[str] = None
    device_type: Optional[str] = None


FIELDS = (
    ComparisonField("Manufacturer", "manufacturer"),
    ComparisonField("Product family", "product_family"),
    ComparisonField("Model name", "model_name"),
    ComparisonField("Device type", "device_type"),
    ComparisonField("SKU", "sku"),
    ComparisonField("Part number", "part_number"),
    ComparisonField("Region", "region"),
    ComparisonField("Model year", "model_year"),
    ComparisonField("CPU", "cpu"),
    ComparisonField("GPU", "gpu"),
    ComparisonField("RAM", "ram_gb", "GB"),
    ComparisonField("Storage", "storage_gb", "GB"),
    ComparisonField("Display size", "display_size_inches", "in", "laptop"),
    ComparisonField("Battery", "battery_wh", "Wh", "laptop"),
    ComparisonField("Weight", "weight_g", "g", "laptop"),
    ComparisonField("Form factor", "form_factor", device_type="desktop"),
    ComparisonField("Power supply", "power_supply_w", "W", "desktop"),
)


@dataclass(frozen=True)
class ComparisonRow:
    label: str
    left: str
    right: str
    status: str


@dataclass(frozen=True)
class ComparisonResult:
    first: DeviceDetails
    second: DeviceDetails
    rows: tuple[ComparisonRow, ...]


def _unknown(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _display(value: object, unit: Optional[str], applicable: bool) -> str:
    if not applicable:
        return NOT_APPLICABLE
    if _unknown(value):
        return UNKNOWN
    return f"{value} {unit}" if unit else str(value)


def _equal_known(left: object, right: object) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()
    return left == right


def equivalent_values(left: object, right: object) -> bool:
    """Use the comparison text rules while keeping unknown distinct from known."""
    if _unknown(left) or _unknown(right):
        return _unknown(left) and _unknown(right)
    return _equal_known(left, right)


def compare_devices(first: DeviceDetails, second: DeviceDetails) -> ComparisonResult:
    """Classify each field without treating missing values as a difference."""
    rows = []
    for field in FIELDS:
        left_applicable = field.device_type is None or first.device_type == field.device_type
        right_applicable = field.device_type is None or second.device_type == field.device_type
        left_value = getattr(first, field.attribute)
        right_value = getattr(second, field.attribute)
        if not left_applicable or not right_applicable:
            status = NOT_APPLICABLE
        elif _unknown(left_value) or _unknown(right_value):
            status = "Unknown"
        elif _equal_known(left_value, right_value):
            status = "Same"
        else:
            status = "Different"
        rows.append(ComparisonRow(
            field.label,
            _display(left_value, field.unit, left_applicable),
            _display(right_value, field.unit, right_applicable),
            status,
        ))
    return ComparisonResult(first, second, tuple(rows))


def compare_catalog(path: Path, first_id: int, second_id: int) -> ComparisonResult:
    """Load both stable IDs and compare their stored fields."""
    first = show_configuration(path, first_id)
    if first is None:
        raise MissingConfigurationError(f"No configuration with ID {first_id} in {path}.")
    second = show_configuration(path, second_id)
    if second is None:
        raise MissingConfigurationError(f"No configuration with ID {second_id} in {path}.")
    return compare_devices(first, second)
