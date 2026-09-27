"""Conservative mapping for one ThinkCentre M70s Gen 5 model-detail PDF."""

import re
from dataclasses import dataclass
from datetime import date

from compx.csv_import import CSV_COLUMNS
from compx.psref_adapter import UnsupportedSourceError, _looks_like_unknown_label
from compx.source_review import ReviewField, SourceReview, _ambiguous, _gpu, _ram, _storage

FAMILY = "ThinkCentre M70s Gen 5"
MAPPED_LABELS = (
    "Processor", "Graphics", "Memory", "Storage", "Form Factor", "Power Supply", "Weight",
)
UNSUPPORTED_LABELS = (
    "Chipset", "Memory Slots", "Max Memory", "Storage Support", "Max Storage Support",
    "Storage Slot", "Card Reader", "Optical", "Audio Chip", "Speakers", "Keyboard",
    "Mouse", "Optional Bay", "Expansion Slots", "Case Color", "Stand", "Dust Filter",
    "Pen", "Dimensions", "Dimensions (WxDxH)", "Ethernet", "WLAN + Bluetooth", "WWAN", "Front Ports",
    "Rear Ports", "Optional Rear Ports", "Security Chip", "Fingerprint Reader",
    "Physical Locks", "Smart Cable Clip", "Chassis E-Lock", "Chassis Intrusion Switch",
    "System Management", "Base Warranty", "Included Upgrade", "Green Certifications",
    "Mil-Spec Test", "Operating System", "Bundled Software", "Bundled Accessories",
)
ALL_LABELS = {name.casefold(): name for name in MAPPED_LABELS + UNSUPPORTED_LABELS}
SECTIONS = {
    "PERFORMANCE", "DESIGN", "CONNECTIVITY", "SECURITY & PRIVACY",
    "MANAGEABILITY", "SERVICE", "CERTIFICATIONS", "SOFTWARE", "ACCESSORIES",
}


@dataclass(frozen=True)
class DesktopDocument:
    document_sku: str | None
    labels: dict[str, tuple[str, ...]]
    unrecognized_labels: tuple[str, ...]


def _label(line: str) -> str | None:
    plain = re.sub(r"\[\d+\]|[®™*]", "", line).strip()
    return ALL_LABELS.get(plain.casefold())


def parse_desktop_text(text: str) -> DesktopDocument:
    """Reject platform PDFs and families other than ThinkCentre M70s Gen 5."""
    if re.search(r"\bPlatform Specifications\b", text, re.IGNORECASE):
        raise UnsupportedSourceError(
            "Platform specification PDFs list options, not one exact SKU; use an M70s Gen 5 model-detail PDF"
        )
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    document_sku = None
    found_family = False
    for line in lines[:80]:
        if line.startswith("ThinkCentre ") and FAMILY not in line:
            raise UnsupportedSourceError("Only ThinkCentre M70s Gen 5 model-detail PDFs are supported")
        if line.startswith(FAMILY):
            found_family = True
            match = re.fullmatch(rf"{re.escape(FAMILY)}\s+([A-Za-z0-9]{{10}})", line)
            if match:
                document_sku = match.group(1).upper()
                break
    if not found_family:
        raise UnsupportedSourceError("Only ThinkCentre M70s Gen 5 model-detail PDFs are supported")

    blocks: dict[str, list[str]] = {}
    unrecognized: list[str] = []
    active: str | None = None
    for line in lines:
        if line == "MODEL":
            break
        label = _label(line)
        if label:
            active = label
            blocks.setdefault(label, []).append("")
            continue
        if line in SECTIONS or line.startswith("Note:") or line.startswith("PSREF"):
            active = None
            continue
        if active in MAPPED_LABELS and blocks[active][-1] and _looks_like_unknown_label(line):
            unrecognized.append(line)
            active = None
            continue
        if active is not None:
            blocks[active][-1] = (blocks[active][-1] + " " + line).strip()
    labels = {label: tuple(value for value in values if value) for label, values in blocks.items()}
    if sum(bool(labels.get(label)) for label in ("Processor", "Memory", "Storage")) < 2:
        raise UnsupportedSourceError(
            "PDF lacks the labeled specifications of an M70s Gen 5 model-detail document"
        )
    return DesktopDocument(document_sku, labels, tuple(unrecognized))


def _single(document: DesktopDocument, label: str) -> tuple[str | None, str]:
    values = document.labels.get(label, ())
    if not values:
        return None, "missing"
    if len(values) != 1 or _ambiguous(values[0]):
        return None, "ambiguous"
    return values[0], "mapped"


def _processor(raw: str) -> str | None:
    candidate, _, remainder = raw.partition(",")
    candidate = candidate.strip()
    if not re.match(r"^Intel(?:®)?\s+Core(?:™)?\b", candidate, re.IGNORECASE):
        return None
    if _ambiguous(candidate) or len(re.findall(r"\bi[3579]-\d{4,5}[A-Z]?\b", raw, re.IGNORECASE)) != 1:
        return None
    if remainder and (
        re.search(r"\b(?:or|one of|optional|configurable|choice)\b|[•;]", remainder, re.IGNORECASE)
        or not re.match(r"^\s*\d+C\b", remainder, re.IGNORECASE)
    ):
        return None
    return candidate


def _form_factor(raw: str) -> str | None:
    return raw if re.fullmatch(r"SFF(?:\s*\(\d+(?:\.\d+)?L\))?", raw, re.IGNORECASE) else None


def _power_supply(raw: str) -> str | None:
    if len(re.findall(r"\d+\s*W\b", raw, re.IGNORECASE)) != 1:
        return None
    match = re.match(r"^(\d+)\s*W\b", raw, re.IGNORECASE)
    return match.group(1) if match and int(match.group(1)) > 0 else None


MAPPERS = {
    "Processor": ("cpu", _processor),
    "Graphics": ("gpu", _gpu),
    "Memory": ("ram_gb", _ram),
    "Storage": ("storage_gb", _storage),
    "Form Factor": ("form_factor", _form_factor),
    "Power Supply": ("psu_watts", _power_supply),
}


def review_desktop_document(
    document: DesktopDocument, source_url: str, target_sku: str,
    *, processed_on: date | None = None,
) -> SourceReview:
    processed_on = processed_on or date.today()
    resolved = document.document_sku == target_sku.upper()
    row = {column: "" for column in CSV_COLUMNS}
    row.update({
        "manufacturer": "Lenovo", "product_family": "ThinkCentre",
        "model_name": "M70s Gen 5", "device_type": "desktop",
        "sku": target_sku.upper(), "source_url": source_url,
        "checked_on": processed_on.isoformat(),
    })
    fields = [
        ReviewField("PSREF document title", "manufacturer", "Lenovo", "mapped"),
        ReviewField("PSREF document title", "product_family", "ThinkCentre", "mapped"),
        ReviewField("PSREF document title", "model_name", "M70s Gen 5", "mapped"),
        ReviewField("PSREF document title", "sku", target_sku.upper(),
                    "mapped" if resolved else "ambiguous", "Document SKU must match target"),
        ReviewField("Requested category", "device_type", "desktop", "mapped"),
    ]
    warnings = [
        "REVIEW REQUIRED: extracted values may be incomplete or wrong; verify the PDF and CSV before sync.",
        "The local PDF and source URL cannot be cryptographically linked by CompX.",
    ]
    if not resolved:
        warnings.append(
            f"Unresolved SKU: document shows {document.document_sku or 'no model code'}, "
            f"but --sku is {target_sku.upper()}; no import-ready CSV can be saved."
        )
    for label in MAPPED_LABELS:
        if label == "Weight":
            raw_values = document.labels.get(label, ())
            fields.append(ReviewField(
                label, "weight_g", "", "ambiguous" if raw_values else "missing",
                "PSREF weights are approximate; do not treat as exact",
            ))
            if raw_values:
                warnings.append("Weight is approximate; weight_g left blank.")
            continue
        csv_field, mapper = MAPPERS[label]
        if label == "Processor":
            values = document.labels.get(label, ())
            raw = values[0] if len(values) == 1 else None
            status = "missing" if not values else "mapped" if raw else "ambiguous"
        else:
            raw, status = _single(document, label)
        value = mapper(raw) if raw is not None else None
        if raw is not None and value is None:
            status = "ambiguous"
        if value is not None:
            row[csv_field] = value
        fields.append(ReviewField(label, csv_field, value or "", status, raw or ""))
        if status == "ambiguous":
            warnings.append(f"{label} is not one exact installed value; {csv_field} left blank.")
        if label == "Power Supply" and raw and re.search(r"\[\d+\]", raw):
            warnings.append("Power Supply has a PSREF footnote; review it before accepting the wattage.")
    for label in ("region", "model_year"):
        fields.append(ReviewField("Not established by this PDF", label, "", "missing"))
    for label in UNSUPPORTED_LABELS:
        values = document.labels.get(label, ())
        if values:
            fields.append(ReviewField(
                label, "not in CompX CSV", "", "unsupported",
                values[0][:120] + ("..." if len(values[0]) > 120 else ""),
            ))
    for label in document.unrecognized_labels:
        fields.append(ReviewField(
            label, "not mapped", "", "unsupported",
            "Unrecognized PDF label; inspect the surrounding text in the PDF",
        ))
    if any(field.status == "unsupported" for field in fields):
        warnings.append("Recognized PSREF labels without CompX fields were not copied.")
    if any(field.status == "missing" for field in fields):
        warnings.append("Missing fields remain blank; do not fill them from platform options or limits.")
    fields.extend((
        ReviewField("User-supplied official PDF URL", "source_url", source_url, "mapped"),
        ReviewField("Date processed", "checked_on", processed_on.isoformat(), "mapped"),
    ))
    return SourceReview(
        source_url, target_sku.upper(), document.document_sku, resolved,
        row, tuple(fields), tuple(warnings),
    )
