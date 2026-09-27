"""Synthetic, generated PSREF-like PDFs; no manufacturer PDF is stored here."""

import csv
import sqlite3

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from typer.testing import CliRunner

from compx.cli import app
from compx.csv_import import CSV_COLUMNS, inspect_csv
from compx.database import init_database


runner = CliRunner()
SKU = "21AB0001US"
URL = (
    "https://psref.lenovo.com/syspool/TempFile/cache/synthetic/"
    "ThinkPad_Test_Model_21AB0001US.pdf"
)


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
        "Processor": "Intel Core Ultra 7 155H, 16C, Max Turbo up to 4.8GHz",
        "Graphics": "NVIDIA RTX 500 Ada Generation 4GB GDDR6",
        "Memory": "1x 16GB SO-DIMM DDR5-5600",
        "Storage": "512GB SSD M.2 2280 PCIe 4.0x4",
        "Battery": "Integrated 57Wh",
        "Display": '14.5" WUXGA (1920x1200) IPS 300nits',
        "Weight": "Starting at 1.61 kg",
        "Power Adapter": "135W USB-C Slim",
    }
    values.update(overrides)
    lines = [f"ThinkPad Test Model {SKU}", "PERFORMANCE"]
    for label, value in values.items():
        if value is not None:
            lines.extend((label, value))
    lines.extend(("MODEL", "PSREF", "Product Specifications Reference"))
    return lines


def parse(pdf, *extra):
    return runner.invoke(app, [
        "source", "parse", str(pdf), "--source-url", URL,
        "--sku", SKU, "--device-type", "laptop", *map(str, extra),
    ])


def test_supported_pdf_produces_reviewable_import_compatible_candidate(tmp_path):
    pdf = make_pdf(tmp_path / "source.pdf", model_lines())
    output = tmp_path / "candidate.csv"
    database = tmp_path / "catalog.sqlite3"
    init_database(database)
    before = database.read_bytes()

    preview = parse(pdf)
    assert preview.exit_code == 0
    assert "HUMAN REVIEW REQUIRED" in preview.output
    assert "mapped | Processor -> cpu | Intel Core Ultra 7 155H" in preview.output
    assert "ambiguous | Weight -> weight_g | (blank)" in preview.output
    assert "unsupported | Power Adapter -> not in CompX CSV" in preview.output
    assert not output.exists()
    assert database.read_bytes() == before

    saved = parse(pdf, "--output", output)
    assert saved.exit_code == 0
    assert "Saved review-required CSV candidate" in saved.output
    with output.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        assert tuple(reader.fieldnames) == CSV_COLUMNS
        rows = list(reader)
    assert len(rows) == 1
    row = rows[0]
    assert row["manufacturer"] == "Lenovo"
    assert row["product_family"] == "ThinkPad"
    assert row["model_name"] == "Test Model"
    assert row["sku"] == SKU
    assert row["cpu"] == "Intel Core Ultra 7 155H"
    assert row["gpu"] == "NVIDIA RTX 500 Ada Generation 4GB GDDR6"
    assert row["ram_gb"] == "16"
    assert row["storage_gb"] == "512"
    assert row["battery_wh"] == "57"
    assert row["display_size_in"] == "14.5"
    assert row["weight_g"] == ""
    assert row["source_url"] == URL
    assert len(row["checked_on"]) == 10
    assert inspect_csv(output, database, check_catalog_duplicates=False).issues == []
    sync_preview = runner.invoke(app, ["sync", str(output), "--database", str(database)])
    assert sync_preview.exit_code == 0
    assert "New 1" in sync_preview.output
    assert database.read_bytes() == before


def test_options_and_platform_limits_are_left_blank_with_warnings(tmp_path):
    pdf = make_pdf(tmp_path / "options.pdf", model_lines(
        Processor="Intel Core Ultra 5 or Intel Core Ultra 7",
        Graphics="NVIDIA RTX 500 / NVIDIA RTX 1000",
        Memory="Up to 64GB DDR5",
        Storage="512GB SSD or 1TB SSD",
        Battery="57Wh / 75Wh",
        Display='14.5" or 16" panel',
    ))
    output = tmp_path / "candidate.csv"
    result = parse(pdf, "--output", output)
    assert result.exit_code == 0
    for field in ("cpu", "gpu", "ram_gb", "storage_gb", "battery_wh", "display_size_in"):
        assert f"-> {field} | (blank)" in result.output
    with output.open(newline="", encoding="utf-8") as source:
        row = next(csv.DictReader(source))
    assert all(row[field] == "" for field in (
        "cpu", "gpu", "ram_gb", "storage_gb", "battery_wh", "display_size_in",
    ))
    assert "ambiguous or unsupported as an exact value" in result.output


def test_unit_normalization_and_missing_fields(tmp_path):
    pdf = make_pdf(tmp_path / "units.pdf", model_lines(
        Memory="2x 16GB SO-DIMM DDR5",
        Storage="1TB SSD M.2 2280",
        Battery="Integrated 57.5Wh",
        Graphics=None,
    ))
    output = tmp_path / "candidate.csv"
    assert parse(pdf, "--output", output).exit_code == 0
    with output.open(newline="", encoding="utf-8") as source:
        row = next(csv.DictReader(source))
    assert row["ram_gb"] == "32"
    assert row["storage_gb"] == "1000"
    assert row["battery_wh"] == "57.5"
    assert row["gpu"] == ""


def test_comma_separated_processor_and_gpu_options_are_not_selected(tmp_path):
    pdf = make_pdf(tmp_path / "alternatives.pdf", model_lines(
        Processor="Intel Core Ultra 5 125H, Intel Core Ultra 7 155H",
        Graphics="NVIDIA RTX 500, NVIDIA RTX 1000",
    ))
    output = tmp_path / "candidate.csv"
    result = parse(pdf, "--output", output)
    assert result.exit_code == 0
    with output.open(newline="", encoding="utf-8") as source:
        row = next(csv.DictReader(source))
    assert row["cpu"] == ""
    assert row["gpu"] == ""


def test_unrecognized_label_is_reported_without_guessing_a_field(tmp_path):
    lines = [
        f"ThinkPad Test Model {SKU}", "PERFORMANCE", "Processor",
        "Intel Core Ultra 7 155H", "RAM Capacity", "64GB",
        "Storage", "512GB SSD", "MODEL", "PSREF", "Product Specifications Reference",
    ]
    pdf = make_pdf(tmp_path / "unknown-label.pdf", lines)
    output = tmp_path / "candidate.csv"
    result = parse(pdf, "--output", output)
    assert result.exit_code == 0
    assert "unsupported | RAM Capacity -> not mapped" in result.output
    with output.open(newline="", encoding="utf-8") as source:
        row = next(csv.DictReader(source))
    assert row["ram_gb"] == ""


@pytest.mark.parametrize("option, value, expected", [
    ("--source-url", "https://psref.lenovo.com.evil.test/x.pdf", "psref.lenovo.com"),
    ("--source-url", "http://psref.lenovo.com/syspool/TempFile/cache/x/ThinkPad_Test_Model_21AB0001US.pdf", "HTTPS"),
    ("--source-url", "https://psref.lenovo.com/syspool/Sys/PDF/ThinkPad/Test_Spec.PDF", "model-detail"),
    ("--sku", "BAD", "10-character"),
    ("--device-type", "tablet", "Only --device-type laptop or desktop"),
])
def test_invalid_options_are_rejected(tmp_path, option, value, expected):
    pdf = make_pdf(tmp_path / "source.pdf", model_lines())
    arguments = ["source", "parse", str(pdf), "--source-url", URL,
                 "--sku", SKU, "--device-type", "laptop"]
    arguments[arguments.index(option) + 1] = value
    result = runner.invoke(app, arguments)
    assert result.exit_code == 1
    assert expected in result.output


def test_missing_required_option_and_missing_pdf(tmp_path):
    missing_option = runner.invoke(app, ["source", "parse", str(tmp_path / "x.pdf")])
    assert missing_option.exit_code == 2
    missing_pdf = parse(tmp_path / "missing.pdf")
    assert missing_pdf.exit_code == 1
    assert "PDF file not found" in missing_pdf.output


def test_unsupported_malformed_scanned_and_encrypted_pdfs(tmp_path):
    platform = make_pdf(tmp_path / "platform.pdf", [
        "ThinkPad Test Model Platform Specifications", "PSREF",
        "Product Specifications Reference", "Processor", "Intel Core options",
    ])
    assert "Platform specification PDFs" in parse(platform).output
    not_pdf = tmp_path / "notes.txt"
    not_pdf.write_text("not a PDF", encoding="utf-8")
    assert "Unsupported document type" in parse(not_pdf).output
    malformed = tmp_path / "bad.pdf"
    malformed.write_bytes(b"%PDF-not-a-real-document")
    assert "Malformed or unreadable PDF" in parse(malformed).output
    scanned = make_pdf(tmp_path / "scanned.pdf", [])
    assert "may be scanned" in parse(scanned).output
    encrypted = make_pdf(tmp_path / "encrypted.pdf", model_lines(), encrypted=True)
    assert "Password-protected PDFs" in parse(encrypted).output


def test_unconfirmed_sku_cannot_write_candidate(tmp_path):
    pdf = make_pdf(tmp_path / "wrong.pdf", [
        "ThinkPad Test Model 21AB0002US", "PERFORMANCE",
        "Processor", "Intel Core Ultra 7 155H", "Memory", "16GB Soldered",
        "MODEL", "PSREF", "Product Specifications Reference",
    ])
    output = tmp_path / "candidate.csv"
    result = parse(pdf, "--output", output)
    assert result.exit_code == 1
    assert "Resolution: unresolved" in result.output
    assert "no import-ready CSV saved" in result.output
    assert not output.exists()


def test_official_export_endpoint_url_is_accepted_and_bound_to_sku(tmp_path):
    pdf = make_pdf(tmp_path / "source.pdf", model_lines())
    endpoint = (
        "https://psref.lenovo.com/api/model/pdfexport/singleModel"
        "?model_code=21AB0001US&country_code=US"
    )
    result = runner.invoke(app, [
        "source", "parse", str(pdf), "--source-url", endpoint,
        "--sku", SKU, "--device-type", "laptop",
    ])
    assert result.exit_code == 0
    assert "Resolution: confirmed" in result.output
    wrong = runner.invoke(app, [
        "source", "parse", str(pdf), "--source-url", endpoint.replace(SKU, "21AB0002US"),
        "--sku", SKU, "--device-type", "laptop",
    ])
    assert wrong.exit_code == 1
    assert "model_code does not match" in wrong.output


def test_existing_candidate_is_protected_until_explicit_overwrite(tmp_path):
    pdf = make_pdf(tmp_path / "source.pdf", model_lines())
    output = tmp_path / "candidate.csv"
    output.write_text("keep this", encoding="utf-8")
    result = parse(pdf, "--output", output)
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert output.read_text(encoding="utf-8") == "keep this"
    overwritten = parse(pdf, "--output", output, "--overwrite")
    assert overwritten.exit_code == 0
    assert output.read_text(encoding="utf-8").startswith("manufacturer,")


def test_candidate_output_never_replaces_a_sqlite_catalog(tmp_path):
    pdf = make_pdf(tmp_path / "source.pdf", model_lines())
    catalog = tmp_path / "catalog.csv"
    init_database(catalog)
    before = catalog.read_bytes()
    result = parse(pdf, "--output", catalog, "--overwrite")
    assert result.exit_code == 1
    assert "cannot replace a SQLite catalog" in result.output
    assert catalog.read_bytes() == before


def test_parse_never_mutates_existing_catalog(tmp_path):
    pdf = make_pdf(tmp_path / "source.pdf", model_lines())
    database = tmp_path / "catalog.sqlite3"
    init_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO manufacturers (name) VALUES ('Existing Co')")
    before = database.read_bytes()
    assert parse(pdf, "--output", tmp_path / "candidate.csv").exit_code == 0
    assert database.read_bytes() == before
