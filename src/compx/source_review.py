"""Map one PSREF model-detail PDF to a human-reviewed CompX CSV candidate."""

import csv
import io
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from compx.csv_import import CSV_COLUMNS, inspect_csv
from compx.psref_adapter import (
    SUPPORTED_LABELS, UNSUPPORTED_LABELS, PsrefDocument, parse_psref_text,
    validate_source_url,
)
from compx.source_pdf import read_pdf_text


@dataclass(frozen=True)
class ReviewField:
    source_label: str
    csv_field: str
    value: str
    status: str
    note: str = ""


@dataclass(frozen=True)
class SourceReview:
    source_url: str
    target_sku: str
    document_sku: str | None
    resolved: bool
    row: dict[str, str]
    fields: tuple[ReviewField, ...]
    warnings: tuple[str, ...]


def _ambiguous(raw: str) -> bool:
    return bool(re.search(
        r"\b(up to|starting at|one of|configurable|optional|varies|selected models)\b|[•]|\s/\s|\sor\s",
        raw, re.IGNORECASE,
    ))


def _single_value(document: PsrefDocument, label: str) -> tuple[str | None, str]:
    values = document.labels.get(label, ())
    if not values:
        return None, "missing"
    if len(values) != 1 or _ambiguous(values[0]):
        return None, "ambiguous"
    return values[0], "mapped"


def _cpu(raw: str) -> str | None:
    candidate, separator, remainder = raw.partition(",")
    candidate = candidate.strip()
    if not re.match(r"^(Intel|AMD)\b", candidate, re.IGNORECASE):
        return None
    if _ambiguous(candidate) or len(candidate) < 12 or len(candidate) > 90:
        return None
    if len(re.findall(r"\b(?:Intel\s+(?:Core|Xeon)|AMD\s+Ryzen)\b", candidate, re.IGNORECASE)) != 1:
        return None
    if separator:
        details = re.sub(r"\bMax Turbo up to\b|/\s*\d+T\b", "", remainder, flags=re.IGNORECASE)
        if (
            _ambiguous(details)
            or re.search(r"\b(?:Intel\s+(?:Core|Xeon)|AMD\s+Ryzen)\b", remainder, re.IGNORECASE)
            or not re.match(
                r"^\s*(?:\d+C\b|\d+\s+cores?\b|Max\b|\d+(?:\.\d+)?\s*GHz\b|\d+\s*MB\b)",
                remainder, re.IGNORECASE,
            )
        ):
            return None
    return candidate


def _gpu(raw: str) -> str | None:
    if _ambiguous(raw) or "," in raw or ";" in raw or not 10 < len(raw) <= 100 or not re.match(
        r"^(NVIDIA|AMD|Intel|Integrated Intel)\b", raw, re.IGNORECASE,
    ):
        return None
    if len(re.findall(r"\b(?:NVIDIA|AMD|Intel)\b", raw, re.IGNORECASE)) != 1:
        return None
    return raw


def _ram(raw: str) -> str | None:
    if len(re.findall(r"\d+\s*(?:GB|TB)\b", raw, re.IGNORECASE)) != 1 or "+" in raw:
        return None
    match = re.match(r"^(?:(\d+)\s*x\s*)?(\d+)\s*GB\b", raw, re.IGNORECASE)
    if not match:
        return None
    count = int(match.group(1) or 1)
    amount = count * int(match.group(2))
    return str(amount) if 0 < amount <= 2**63 - 1 else None


def _storage(raw: str) -> str | None:
    if len(re.findall(r"\d+\s*(?:GB|TB)\b", raw, re.IGNORECASE)) != 1 or "+" in raw:
        return None
    match = re.match(r"^(\d+)\s*(GB|TB)\b\s+(?:SSD|HDD)\b", raw, re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1)) * (1000 if match.group(2).upper() == "TB" else 1)
    return str(amount) if 0 < amount <= 2**63 - 1 else None


def _battery(raw: str) -> str | None:
    if len(re.findall(r"\d+(?:\.\d+)?\s*Wh\b", raw, re.IGNORECASE)) != 1:
        return None
    match = re.match(r"^(?:Integrated|Internal|Removable)?\s*(\d+(?:\.\d+)?)\s*Wh\b", raw, re.IGNORECASE)
    return match.group(1) if match and float(match.group(1)) > 0 else None


def _display(raw: str) -> str | None:
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(?:\"|inches?\b)", raw, re.IGNORECASE)
    return matches[0] if len(matches) == 1 and float(matches[0]) > 0 else None


MAPPERS = {
    "Processor": ("cpu", _cpu),
    "Graphics": ("gpu", _gpu),
    "Memory": ("ram_gb", _ram),
    "Storage": ("storage_gb", _storage),
    "Battery": ("battery_wh", _battery),
    "Display": ("display_size_in", _display),
}


def review_document(
    document: PsrefDocument, source_url: str, target_sku: str,
    *, processed_on: date | None = None,
) -> SourceReview:
    """Map only single, model-specific values; leave all uncertain values blank."""
    processed_on = processed_on or date.today()
    resolved = document.document_sku == target_sku.upper()
    model_name = document.title.removeprefix("ThinkPad ") if document.title else ""
    row = {column: "" for column in CSV_COLUMNS}
    row.update({
        "manufacturer": "Lenovo", "product_family": "ThinkPad",
        "model_name": model_name, "device_type": "laptop", "sku": target_sku.upper(),
        "source_url": source_url, "checked_on": processed_on.isoformat(),
    })
    fields = [
        ReviewField("PSREF document title", "manufacturer", "Lenovo", "mapped"),
        ReviewField("PSREF document title", "product_family", "ThinkPad", "mapped"),
        ReviewField("PSREF document title", "model_name", model_name, "mapped" if model_name else "missing"),
        ReviewField("PSREF document title", "sku", target_sku.upper(),
                    "mapped" if resolved else "ambiguous", "Document SKU must match target"),
        ReviewField("Requested category", "device_type", "laptop", "mapped"),
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
    for label in SUPPORTED_LABELS:
        if label == "Weight":
            raw_values = document.labels.get(label, ())
            status = "ambiguous" if raw_values else "missing"
            fields.append(ReviewField(
                label, "weight_g", "", status,
                "PSREF model weights are approximate or starting values; do not treat as exact",
            ))
            if raw_values:
                warnings.append("Weight is approximate or a starting value; weight_g left blank.")
            continue
        csv_field, mapper = MAPPERS[label]
        if label == "Processor":
            values = document.labels.get(label, ())
            raw = values[0] if len(values) == 1 else None
            status = "missing" if not values else "mapped" if raw else "ambiguous"
        else:
            raw, status = _single_value(document, label)
        value = mapper(raw) if raw is not None else None
        if raw is not None and value is None:
            status = "ambiguous"
        if value is not None:
            row[csv_field] = value
        fields.append(ReviewField(label, csv_field, value or "", status, raw or ""))
        if status == "ambiguous":
            warnings.append(f"{label} is ambiguous or unsupported as an exact value; {csv_field} left blank.")
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
        warnings.append("Other recognized PSREF labels have no CompX CSV field and were not copied.")
    if any(field.status == "missing" for field in fields):
        warnings.append("Missing values remain blank; do not fill them from platform options or limits.")
    fields.extend((
        ReviewField("User-supplied official PDF URL", "source_url", source_url, "mapped"),
        ReviewField("Date processed", "checked_on", processed_on.isoformat(), "mapped"),
    ))
    return SourceReview(
        source_url, target_sku.upper(), document.document_sku, resolved,
        row, tuple(fields), tuple(warnings),
    )


def build_review(
    pdf_path: Path, source_url: str, sku: str, device_type: str,
    *, processed_on: date | None = None,
) -> SourceReview:
    if device_type not in ("laptop", "desktop"):
        raise ValueError("Only --device-type laptop or desktop is supported")
    source_url = validate_source_url(source_url, sku)
    text = read_pdf_text(pdf_path)
    if device_type == "desktop":
        from compx.psref_desktop import parse_desktop_text, review_desktop_document

        document = parse_desktop_text(text)
        return review_desktop_document(document, source_url, sku, processed_on=processed_on)
    document = parse_psref_text(text)
    return review_document(document, source_url, sku, processed_on=processed_on)


def format_review(review: SourceReview) -> str:
    lines = [
        "Lenovo PSREF model-detail PDF candidate - HUMAN REVIEW REQUIRED",
        f"Source URL: {review.source_url}",
        f"Target SKU: {review.target_sku}",
        f"Document SKU: {review.document_sku or 'Not confirmed'}",
        f"Resolution: {'confirmed' if review.resolved else 'unresolved'}",
        "Status | PSREF label -> CompX field | Candidate value",
    ]
    for field in review.fields:
        value = field.value or "(blank)"
        lines.append(f"{field.status} | {field.source_label} -> {field.csv_field} | {value}")
        if field.note:
            lines.append(f"  Evidence/limit: {field.note}")
    lines.append("Warnings:")
    lines.extend(f"- {warning}" for warning in review.warnings)
    return "\n".join(lines)


def serialize_candidate(review: SourceReview) -> str:
    if not review.resolved:
        raise ValueError("Target SKU is unresolved; cannot save an import-ready candidate")
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerow(review.row)
    return stream.getvalue()


def validate_candidate(payload: str) -> None:
    """Run the existing import validator before publishing a candidate file."""
    with tempfile.TemporaryDirectory(prefix="compx-source-validate-") as directory:
        path = Path(directory) / "candidate.csv"
        path.write_text(payload, encoding="utf-8", newline="")
        plan = inspect_csv(
            path, Path(directory) / "unused.sqlite3", check_catalog_duplicates=False,
        )
    if plan.issues or len(plan.records) != 1:
        messages = "; ".join(str(issue) for issue in plan.issues)
        raise ValueError(f"Extracted candidate does not meet the CSV contract: {messages}")


def write_candidate(payload: str, output: Path, pdf_path: Path, *, overwrite: bool) -> Path:
    output = Path(output).expanduser()
    if output.suffix.lower() != ".csv":
        raise ValueError("--output must name a .csv file")
    if output.resolve() == Path(pdf_path).expanduser().resolve():
        raise ValueError("Candidate output cannot replace the input PDF")
    if output.is_file():
        with output.open("rb") as existing:
            if existing.read(16) == b"SQLite format 3\x00":
                raise ValueError("Candidate output cannot replace a SQLite catalog")
    if not overwrite:
        with output.open("x", encoding="utf-8", newline="") as stream:
            stream.write(payload)
        return output
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=f".{output.name}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output
