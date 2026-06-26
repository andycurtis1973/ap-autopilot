"""Render invoice PDF pages to PNG bytes for the vision extractor.

Uses PyMuPDF (no system poppler dependency). 150 DPI is the sweet spot: small
enough to keep the image token count (and cost) down, sharp enough that Claude
reads the line-item table reliably.
"""

from __future__ import annotations

from pathlib import Path

DPI = 150


def pdf_to_pngs(path: str | Path, dpi: int = DPI, max_pages: int = 3) -> list[bytes]:
    """Return one PNG byte string per page (capped — invoices are 1-2 pages)."""
    import fitz  # PyMuPDF

    out: list[bytes] = []
    with fitz.open(str(path)) as doc:
        for page in doc[:max_pages]:
            pix = page.get_pixmap(dpi=dpi)
            out.append(pix.tobytes("png"))
    return out
