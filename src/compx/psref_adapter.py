"""Recognize Lenovo PSREF ThinkPad model-detail PDFs and their labels."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit


class UnsupportedSourceError(ValueError):
    """The source is outside the one supported manufacturer/document format."""


SUPPORTED_LABELS = (
    "Processor", "Graphics", "Memory", "Storage", "Battery", "Display", "Weight",
)
UNSUPPORTED_LABELS = (
    "AI PC Category", "NPU", "Chipset", "Memory Slots", "Max Memory",
    "Storage Support", "Max Storage Support", "Storage Slot", "Card Reader", "Audio Chip", "Speakers",
    "Camera", "Microphone", "Power Adapter", "Touchscreen", "Color Calibration",
    "Keyboard", "Case Color",
    "Case Material", "Pen", "Dimensions", "Operating System", "Bundled Software", "Ethernet",
    "WLAN + Bluetooth", "WWAN", "SIM Card", "NFC", "Standard Ports", "Optional Ports", "Docking",
    "Smart Card Reader", "Security Chip", "Fingerprint Reader",
    "Physical Locks", "Other Security", "System Management", "Base Warranty",
    "Included Upgrade", "Bundled Accessories", "Green Certifications", "Other Certifications",
    "Mil-Spec Test", "ISV Certifications",
)
ALL_LABELS = {label.casefold(): label for label in SUPPORTED_LABELS + UNSUPPORTED_LABELS}
SECTIONS = {
    "PERFORMANCE", "DESIGN", "SOFTWARE", "CONNECTIVITY", "SECURITY & PRIVACY",
    "MANAGEABILITY", "SERVICE", "CERTIFICATIONS", "MODEL", "NOTE:",
}


@dataclass(frozen=True)
class PsrefDocument:
    title: str | None
    document_sku: str | None
    labels: dict[str, tuple[str, ...]]
    unrecognized_labels: tuple[str, ...] = ()


def validate_source_url(url: str, sku: str) -> str:
    """Allow only official, SKU-specific PSREF model-detail PDF URLs."""
    if not re.fullmatch(r"[A-Za-z0-9]{10}", sku):
        raise UnsupportedSourceError("--sku must be a 10-character Lenovo model code")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsupportedSourceError("--source-url is not a valid URL") from exc
    if parsed.scheme != "https" or parsed.hostname != "psref.lenovo.com" or port not in (None, 443):
        raise UnsupportedSourceError(
            "--source-url must be an HTTPS URL on psref.lenovo.com"
        )
    if parsed.username or parsed.password or parsed.fragment:
        raise UnsupportedSourceError("--source-url contains unsupported URL components")
    path = unquote(parsed.path)
    if path == "/api/model/pdfexport/singleModel":
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"model_code", "country_code"} or any(
            len(values) != 1 for values in query.values()
        ):
            raise UnsupportedSourceError("PSREF PDF export URL needs model_code and country_code")
        if query["model_code"][0].casefold() != sku.casefold():
            raise UnsupportedSourceError("Source PDF model_code does not match --sku")
        if not re.fullmatch(r"[A-Za-z]{2}", query["country_code"][0]):
            raise UnsupportedSourceError("Source PDF country_code must be two letters")
    elif re.fullmatch(r"/syspool/TempFile/cache/[^/]+/[^/]+\.pdf", path, re.IGNORECASE):
        if not path.casefold().endswith(f"_{sku.casefold()}.pdf"):
            raise UnsupportedSourceError("Source PDF filename does not match --sku")
    else:
        raise UnsupportedSourceError(
            "Only Lenovo PSREF model-detail PDF download URLs are supported"
        )
    return url


def _recognized_label(line: str) -> str | None:
    plain = re.sub(r"\[\d+\]|[®™*]", "", line).strip()
    return ALL_LABELS.get(plain.casefold())


def _looks_like_unknown_label(line: str) -> bool:
    words = line.split()
    return (
        1 <= len(words) <= 4 and len(line) <= 45
        and all(re.fullmatch(r"[A-Za-z][A-Za-z+&/-]*", word) for word in words)
        and all(word[0].isupper() for word in words)
    )


def parse_psref_text(text: str) -> PsrefDocument:
    """Extract labeled model-detail fields, rejecting platform-level documents."""
    if re.search(r"\bPlatform Specifications\b", text, re.IGNORECASE):
        raise UnsupportedSourceError(
            "Platform specification PDFs list options, not one exact SKU; use a PSREF model-detail PDF"
        )
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    heading = None
    document_sku = None
    for line in lines[:30]:
        match = re.fullmatch(r"(ThinkPad\s+.+?)\s+([A-Za-z0-9]{10})", line)
        if match:
            heading, document_sku = match.group(1), match.group(2).upper()
            break
    if heading is None:
        for line in lines[:30]:
            if line.startswith("ThinkPad ") and len(line) < 120:
                heading = line
                break
    if heading is None:
        raise UnsupportedSourceError(
            "PDF has no recognizable ThinkPad model-detail heading"
        )
    if "Platform Specifications" in heading:
        raise UnsupportedSourceError("Platform specification PDFs are not supported")

    blocks: dict[str, list[str]] = {}
    unrecognized: list[str] = []
    active: str | None = None
    for line in lines:
        if line == "MODEL":
            break
        label = _recognized_label(line)
        if label:
            active = label
            blocks.setdefault(label, []).append("")
            continue
        if line in SECTIONS or line.startswith("Note:") or line.startswith("PSREF"):
            active = None
            continue
        if active in SUPPORTED_LABELS and blocks[active][-1] and _looks_like_unknown_label(line):
            unrecognized.append(line)
            active = None
            continue
        if active is not None:
            previous = blocks[active][-1]
            blocks[active][-1] = (previous + " " + line).strip()
    if sum(bool(blocks.get(label)) for label in ("Processor", "Memory", "Storage")) < 2:
        raise UnsupportedSourceError(
            "PDF lacks the labeled specifications of a PSREF model-detail document"
        )
    return PsrefDocument(
        heading, document_sku,
        {label: tuple(value for value in values if value) for label, values in blocks.items()},
        tuple(unrecognized),
    )
