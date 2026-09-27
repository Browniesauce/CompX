"""Generated PSREF-like desktop PDFs; no Lenovo document is checked in."""

import csv

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner

from compx.cli import app
from compx.csv_import import CSV_COLUMNS, inspect_csv
from compx.database import init_database


SKU = "12U8004HGR"
URL = (
    "https://psref.lenovo.com/api/model/pdfexport/singleModel"
    "?model_code=12U8004HGR&country_code=GR"
)
runner = CliRunner()


def make_pdf(path, lines, *, encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    content = ["BT /F1 10 Tf 50 760 Td"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content.append(f"({safe}) Tj 0 -15 Td")
    content.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(content).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("synthetic-password")
    with path.open("wb") as output:
        writer.write(output)
    return path


def model_lines(**overrides):
    values = {
        "Processor": "Intel Core i7-14700, 20C (8P + 12E) / 28T, Max Turbo up to 5.4GHz",
        "Graphics": "Integrated Intel UHD Graphics 770",
        "Memory": "1x 8GB UDIMM DDR5-5600",
        "Max Memory[1]": "Up to 64GB DDR5",
        "Storage": "512GB SSD M.2 2280",
        "Max Storage Support[2]": "Up to three drives",
        "Power Supply": "260W 90%[4]",
        "Form Factor": "SFF (8.2L)",
        "Dimensions (WxDxH)[6]": "92.5 x 297.7 x 339.5 mm",
        "Weight[7]": "Around 5.3 kg",
        "Front Ports": "1x USB-C, 4x USB-A",
        "Expansion Slots": "One PCIe x16, one PCIe x1",
    }
    values.update(overrides)
    lines = [f"ThinkCentre M70s Gen 5  {SKU}", "PERFORMANCE"]
    for label, value in values.items():
        if value is not None:
            lines.extend((label, value))
    lines.extend(("MODEL", "PSREF", "Product Specifications Reference"))
    return lines


def parse(pdf, *extra):
    return runner.invoke(app, [
        "source", "parse", str(pdf), "--source-url", URL,
        "--sku", SKU, "--device-type", "desktop", *map(str, extra),
    ])


def row_at(path):
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        assert tuple(reader.fieldnames) == CSV_COLUMNS
        rows = list(reader)
    assert len(rows) == 1
    return rows[0]


def test_desktop_candidate_and_read_only_sync_preview(tmp_path):
    pdf = make_pdf(tmp_path / "desktop.pdf", model_lines())
    candidate = tmp_path / "candidate.csv"
    database = tmp_path / "catalog.sqlite3"
    init_database(database)
    before = database.read_bytes()

    preview = parse(pdf)
    assert preview.exit_code == 0
    assert "HUMAN REVIEW REQUIRED" in preview.output
    assert "Resolution: confirmed" in preview.output
    assert "mapped | Form Factor -> form_factor | SFF (8.2L)" in preview.output
    assert "mapped | Power Supply -> psu_watts | 260" in preview.output
    assert "unsupported | Dimensions (WxDxH) -> not in CompX CSV" in preview.output
    assert "unsupported | Front Ports -> not in CompX CSV" in preview.output
    assert "unsupported | Expansion Slots -> not in CompX CSV" in preview.output
    assert not candidate.exists()
    assert database.read_bytes() == before

    saved = parse(pdf, "--output", candidate)
    assert saved.exit_code == 0
    row = row_at(candidate)
    assert row["manufacturer"] == "Lenovo"
    assert row["product_family"] == "ThinkCentre"
    assert row["model_name"] == "M70s Gen 5"
    assert row["device_type"] == "desktop"
    assert row["sku"] == SKU
    assert row["cpu"] == "Intel Core i7-14700"
    assert row["gpu"] == "Integrated Intel UHD Graphics 770"
    assert row["ram_gb"] == "8"
    assert row["storage_gb"] == "512"
    assert row["form_factor"] == "SFF (8.2L)"
    assert row["psu_watts"] == "260"
    assert row["weight_g"] == row["region"] == row["model_year"] == ""
    assert row["source_url"] == URL
    assert len(row["checked_on"]) == 10
    assert inspect_csv(candidate, database, check_catalog_duplicates=False).issues == []
    sync = runner.invoke(app, ["sync", str(candidate), "--database", str(database)])
    assert sync.exit_code == 0
    assert "New 1" in sync.output
    assert database.read_bytes() == before


def test_options_and_platform_limits_remain_blank(tmp_path):
    pdf = make_pdf(tmp_path / "options.pdf", model_lines(
        Processor="Intel Core i5-14400 or Intel Core i7-14700",
        Graphics="Integrated Intel UHD Graphics 770 / NVIDIA T400",
        Memory="Up to 64GB DDR5",
        Storage="512GB SSD or 1TB SSD",
        **{"Power Supply": "260W or 310W"},
    ))
    candidate = tmp_path / "candidate.csv"
    result = parse(pdf, "--output", candidate)
    assert result.exit_code == 0
    row = row_at(candidate)
    for field in ("cpu", "gpu", "ram_gb", "storage_gb", "psu_watts"):
        assert row[field] == ""
        assert f"-> {field} | (blank)" in result.output
    assert row["form_factor"] == "SFF (8.2L)"


def test_unit_normalization_and_missing_values(tmp_path):
    pdf = make_pdf(tmp_path / "units.pdf", model_lines(
        Memory="2x 16GB UDIMM DDR5",
        Storage="1TB SSD M.2 2280",
        Graphics=None,
        **{"Form Factor": None},
    ))
    candidate = tmp_path / "candidate.csv"
    result = parse(pdf, "--output", candidate)
    assert result.exit_code == 0
    row = row_at(candidate)
    assert row["ram_gb"] == "32"
    assert row["storage_gb"] == "1000"
    assert row["gpu"] == row["form_factor"] == ""


@pytest.mark.parametrize("replacement,expected", [
    (["ThinkCentre M90s Gen 5  12U8004HGR"], "Only ThinkCentre M70s Gen 5"),
    (["ThinkCentre M70s Gen 5 Platform Specifications"], "Platform specification PDFs"),
    (["ThinkPad Test Model 12U8004HGR"], "Only ThinkCentre M70s Gen 5"),
])
def test_other_families_and_platform_pdfs_rejected(tmp_path, replacement, expected):
    pdf = make_pdf(tmp_path / "unsupported.pdf", replacement + model_lines()[1:])
    result = parse(pdf, "--output", tmp_path / "candidate.csv")
    assert result.exit_code == 1
    assert expected in result.output
    assert not (tmp_path / "candidate.csv").exists()


def test_mismatched_document_sku_cannot_save(tmp_path):
    pdf = make_pdf(tmp_path / "mismatch.pdf", [
        "ThinkCentre M70s Gen 5  12U8005HGR", "PERFORMANCE",
        "Processor", "Intel Core i7-14700", "Memory", "8GB UDIMM",
        "MODEL", "PSREF",
    ])
    candidate = tmp_path / "candidate.csv"
    result = parse(pdf, "--output", candidate)
    assert result.exit_code == 1
    assert "Resolution: unresolved" in result.output
    assert not candidate.exists()


@pytest.mark.parametrize("option,value,expected", [
    ("--source-url", "https://example.com/file.pdf", "psref.lenovo.com"),
    ("--source-url", "", "psref.lenovo.com"),
    ("--sku", "BAD", "10-character"),
    ("--device-type", "tablet", "laptop or desktop"),
])
def test_invalid_options(tmp_path, option, value, expected):
    pdf = make_pdf(tmp_path / "desktop.pdf", model_lines())
    args = ["source", "parse", str(pdf), "--source-url", URL,
            "--sku", SKU, "--device-type", "desktop"]
    args[args.index(option) + 1] = value
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert expected in result.output


def test_missing_pdf_and_unsupported_document_errors(tmp_path):
    assert "PDF file not found" in parse(tmp_path / "missing.pdf").output
    assert runner.invoke(app, ["source", "parse", "missing.pdf"]).exit_code == 2
    txt = tmp_path / "notes.txt"
    txt.write_text("not a PDF", encoding="utf-8")
    assert "Unsupported document type" in parse(txt).output
    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"%PDF-not-a-real-document")
    assert "Malformed or unreadable PDF" in parse(malformed).output
    scanned = make_pdf(tmp_path / "scanned.pdf", [])
    assert "may be scanned" in parse(scanned).output
    encrypted = make_pdf(tmp_path / "encrypted.pdf", model_lines(), encrypted=True)
    assert "Password-protected PDFs" in parse(encrypted).output


def test_output_protection_and_catalog_unchanged(tmp_path):
    pdf = make_pdf(tmp_path / "desktop.pdf", model_lines())
    candidate = tmp_path / "candidate.csv"
    candidate.write_text("keep this", encoding="utf-8")
    assert "already exists" in parse(pdf, "--output", candidate).output
    assert candidate.read_text(encoding="utf-8") == "keep this"
    assert parse(pdf, "--output", candidate, "--overwrite").exit_code == 0
    database = tmp_path / "catalog.csv"
    init_database(database)
    before = database.read_bytes()
    result = parse(pdf, "--output", database, "--overwrite")
    assert result.exit_code == 1
    assert "cannot replace a SQLite catalog" in result.output
    assert database.read_bytes() == before
