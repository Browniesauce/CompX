"""Validated values shared by CSV parsing and database writes."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CsvRecord:
    row_number: int
    manufacturer: str
    product_family: str
    model_name: str
    device_type: str
    sku: str
    source_url: str
    checked_on: str
    region: Optional[str]
    model_year: Optional[int]
    cpu: Optional[str]
    gpu: Optional[str]
    ram_gb: Optional[int]
    storage_gb: Optional[int]
    display_size_in: Optional[float]
    battery_wh: Optional[float]
    weight_g: Optional[int]
    form_factor: Optional[str]
    psu_watts: Optional[int]

    @property
    def identity(self) -> tuple[str, str]:
        return self.manufacturer.casefold(), self.sku.casefold()


@dataclass(frozen=True)
class SearchResult:
    id: int
    manufacturer: str
    device_type: str
    product_family: Optional[str]
    model_name: str
    sku: Optional[str]
    cpu: Optional[str]
    gpu: Optional[str]
    ram_gb: Optional[int]
    storage_gb: Optional[int]


@dataclass(frozen=True)
class SourceRecord:
    url: str
    title: Optional[str]
    checked_on: str


@dataclass(frozen=True)
class DeviceDetails:
    id: int
    manufacturer: str
    product_family: Optional[str]
    model_name: str
    device_type: str
    sku: Optional[str]
    configuration_key: str
    part_number: Optional[str]
    region: Optional[str]
    model_year: Optional[int]
    cpu: Optional[str]
    gpu: Optional[str]
    ram_gb: Optional[int]
    storage_gb: Optional[int]
    display_size_inches: Optional[float]
    battery_wh: Optional[float]
    weight_g: Optional[int]
    form_factor: Optional[str]
    power_supply_w: Optional[int]
    sources: tuple[SourceRecord, ...]
