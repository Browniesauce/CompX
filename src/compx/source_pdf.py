"""Read text from a locally supplied PDF without network access or OCR."""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class SourcePdfError(ValueError):
    """The local file is not a readable, text-based PDF."""


def read_pdf_text(path: Path) -> str:
    path = Path(path).expanduser()
    if path.suffix.lower() != ".pdf":
        raise SourcePdfError("Unsupported document type; provide a local .pdf file")
    if not path.is_file():
        raise SourcePdfError(f"PDF file not found: {path}")
    if path.stat().st_size > 25 * 1024 * 1024:
        raise SourcePdfError("PDF is too large for this single-document adapter (25 MB limit)")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise SourcePdfError("Malformed PDF; the file does not have a PDF header")
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise SourcePdfError("Password-protected PDFs are not supported")
        if not reader.pages:
            raise SourcePdfError("PDF has no pages")
        if len(reader.pages) > 10:
            raise SourcePdfError("PDF has too many pages for a model-detail document (10-page limit)")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, IndexError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, SourcePdfError):
            raise
        raise SourcePdfError(f"Malformed or unreadable PDF: {exc}") from exc
    if len(text.strip()) < 80:
        raise SourcePdfError(
            "No usable text found; this PDF may be scanned. OCR is not supported"
        )
    return text
