"""Search and detail behavior independent of command-line output."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from compx.models import DeviceDetails, SearchResult
from compx.repository import get_configuration, search_configurations

DEFAULT_SEARCH_LIMIT = 20
MAX_SQLITE_INTEGER = 2**63 - 1


class InvalidFilter(ValueError):
    """A search or detail argument is outside its supported range."""


@dataclass(frozen=True)
class SearchFilters:
    query: Optional[str] = None
    device_type: Optional[str] = None
    manufacturer: Optional[str] = None
    cpu: Optional[str] = None
    gpu: Optional[str] = None
    ram_min: Optional[int] = None
    storage_min: Optional[int] = None
    limit: int = DEFAULT_SEARCH_LIMIT


def search_catalog(path: Path, filters: SearchFilters) -> list[SearchResult]:
    """Validate, normalize, and combine all supplied search filters."""
    if filters.device_type is not None and filters.device_type not in ("laptop", "desktop"):
        raise InvalidFilter("--type must be laptop or desktop")
    for label, value in (
        ("--limit", filters.limit),
        ("--ram-min", filters.ram_min),
        ("--storage-min", filters.storage_min),
    ):
        if value is not None and not 0 < value <= MAX_SQLITE_INTEGER:
            raise InvalidFilter(f"{label} must be a positive integer within SQLite's range")
    for label, value in (
        ("--manufacturer", filters.manufacturer),
        ("--cpu", filters.cpu),
        ("--gpu", filters.gpu),
    ):
        if value is not None and not value.strip():
            raise InvalidFilter(f"{label} cannot be empty")
    return search_configurations(
        path,
        query=filters.query.strip() if filters.query else None,
        device_type=filters.device_type,
        manufacturer=filters.manufacturer.strip() if filters.manufacturer else None,
        cpu=filters.cpu.strip() if filters.cpu else None,
        gpu=filters.gpu.strip() if filters.gpu else None,
        ram_min=filters.ram_min,
        storage_min=filters.storage_min,
        limit=filters.limit,
    )


def show_configuration(path: Path, device_id: int) -> Optional[DeviceDetails]:
    """Return one exact configuration by its stable database ID."""
    if not 0 < device_id <= MAX_SQLITE_INTEGER:
        raise InvalidFilter("DEVICE_ID must be a positive integer within SQLite's range")
    return get_configuration(path, device_id)
