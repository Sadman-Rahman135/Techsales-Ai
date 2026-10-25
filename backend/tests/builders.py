"""Test-side alias: the builders live in app/demo so the demo corpus and evaluation can use them too."""

from app.demo.builders import docx_bytes, pdf_bytes, scanned_pdf_bytes, warranty_pdf

__all__ = ["docx_bytes", "pdf_bytes", "scanned_pdf_bytes", "warranty_pdf"]
