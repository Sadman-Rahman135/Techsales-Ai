"""Upload validation (blueprint §7.1). Runs before anything is stored.

The file's *contents* decide its type. The extension must be on the allowlist and agree with the contents;
the browser-supplied MIME type is only cross-checked, never trusted. Encrypted, corrupt, empty, macro-enabled
and executable files are rejected with a stable error code.
"""

import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

import docx
import pymupdf

from app.config import Settings
from app.ingestion.errors import ErrorCode, IngestionError

SourceType = Literal["pdf", "docx", "txt"]

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ALLOWED: dict[str, tuple[SourceType, str]] = {
    ".pdf": ("pdf", "application/pdf"),
    ".docx": ("docx", DOCX_MIME),
    ".txt": ("txt", "text/plain"),
}
# What browsers and OSes actually send for each extension; anything else is a mismatch.
DECLARED_OK: dict[SourceType, frozenset[str]] = {
    "pdf": frozenset({"application/pdf", "application/x-pdf"}),
    "docx": frozenset({DOCX_MIME, "application/zip"}),
    "txt": frozenset({"text/plain", "text/markdown"}),
}
UNKNOWN_DECLARED = frozenset({"", "application/octet-stream"})

EXECUTABLE_MAGIC = (
    b"MZ",  # Windows PE
    b"\x7fELF",  # Linux
    b"\xfe\xed\xfa\xce",  # Mach-O variants
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # Mach-O fat binary / Java class
    b"#!",  # script with a shebang
)
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .doc, and every password-protected Office file
ZIP_MAGIC = b"PK\x03\x04"
MAX_DOCX_UNCOMPRESSED = 200 * 1024 * 1024  # zip-bomb guard
MAX_FILENAME_CHARS = 120
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    data: bytes
    source_type: SourceType
    mime_type: str  # canonical, from the allowlist, not from the browser
    extension: str
    display_filename: str
    checksum: str
    page_count: int | None  # PDFs only

    @property
    def size_bytes(self) -> int:
        return len(self.data)


def sanitize_filename(name: str | None) -> str:
    """A display name safe to show and log. Never used as a storage path (keys are generated)."""
    raw = re.split(r"[/\\]", name or "")[-1]  # drop any client-side directory
    cleaned = "".join(
        ch for ch in unicodedata.normalize("NFC", raw) if not unicodedata.category(ch).startswith("C")
    )
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "document"
    if len(cleaned) > MAX_FILENAME_CHARS:
        suffix = PurePosixPath(cleaned).suffix[:10]
        cleaned = cleaned[: MAX_FILENAME_CHARS - len(suffix)].rstrip(" .") + suffix
    return cleaned


def validate_upload(
    filename: str | None, declared_mime: str | None, data: bytes, settings: Settings
) -> ValidatedFile:
    if not data:
        raise IngestionError(ErrorCode.FILE_EMPTY)
    if len(data) > settings.max_upload_bytes:
        raise IngestionError(ErrorCode.FILE_TOO_LARGE, f"{len(data)} bytes")

    display = sanitize_filename(filename)
    extension = PurePosixPath(display.lower()).suffix
    if extension not in ALLOWED:
        raise IngestionError(ErrorCode.FILE_TYPE_NOT_ALLOWED, f"extension {extension!r}")
    if data.startswith(EXECUTABLE_MAGIC):
        raise IngestionError(ErrorCode.FILE_TYPE_NOT_ALLOWED, "executable signature")
    source_type, mime_type = ALLOWED[extension]

    declared = (declared_mime or "").split(";")[0].strip().lower()
    if declared not in UNKNOWN_DECLARED and declared not in DECLARED_OK[source_type]:
        raise IngestionError(ErrorCode.FILE_TYPE_MISMATCH, f"declared {declared!r} for {extension}")

    page_count = None
    if source_type == "pdf":
        page_count = _check_pdf(data, settings)
    elif source_type == "docx":
        _check_docx(data)
    else:
        _check_txt(data)

    return ValidatedFile(
        data=data,
        source_type=source_type,
        mime_type=mime_type,
        extension=extension,
        display_filename=display,
        checksum=hashlib.sha256(data).hexdigest(),
        page_count=page_count,
    )


def _check_pdf(data: bytes, settings: Settings) -> int:
    if b"%PDF-" not in data[:1024]:  # the header may follow a little junk, but must be near the start
        raise IngestionError(ErrorCode.FILE_TYPE_MISMATCH, "no %PDF- header")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise IngestionError(ErrorCode.FILE_ENCRYPTED)
            pages: int = pdf.page_count
    except IngestionError:
        raise
    except Exception as exc:  # MuPDF raises several types for damaged files
        raise IngestionError(ErrorCode.FILE_CORRUPT, type(exc).__name__) from exc
    if pages == 0:
        raise IngestionError(ErrorCode.FILE_CORRUPT, "zero pages")
    if pages > settings.max_pdf_pages:
        raise IngestionError(ErrorCode.FILE_TOO_MANY_PAGES, f"{pages} pages")
    return pages


def _check_docx(data: bytes) -> None:
    if data.startswith(OLE_MAGIC):
        # Office stores password-protected .docx files in an OLE container; a renamed .doc looks the same.
        raise IngestionError(ErrorCode.FILE_ENCRYPTED, "OLE container")
    if not data.startswith(ZIP_MAGIC):
        raise IngestionError(ErrorCode.FILE_TYPE_MISMATCH, "not a zip package")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            names = set(package.namelist())
            if sum(info.file_size for info in package.infolist()) > MAX_DOCX_UNCOMPRESSED:
                raise IngestionError(ErrorCode.FILE_CORRUPT, "uncompressed size over limit")
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise IngestionError(ErrorCode.FILE_TYPE_MISMATCH, "not a Word document package")
            content_types = package.read("[Content_Types].xml")
        if "word/vbaProject.bin" in names or b"macroEnabled" in content_types:
            raise IngestionError(ErrorCode.FILE_TYPE_NOT_ALLOWED, "macro-enabled document")
        docx.Document(io.BytesIO(data))
    except IngestionError:
        raise
    except Exception as exc:
        raise IngestionError(ErrorCode.FILE_CORRUPT, type(exc).__name__) from exc


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM) or BOM-marked UTF-16. Anything else must be re-saved as UTF-8."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if b"\x00" in data:
        raise IngestionError(ErrorCode.FILE_TYPE_MISMATCH, "binary content in a text file")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError(ErrorCode.FILE_CORRUPT, "not UTF-8") from exc


def _check_txt(data: bytes) -> None:
    try:
        text = decode_text(data)
    except UnicodeDecodeError as exc:
        raise IngestionError(ErrorCode.FILE_CORRUPT, "bad UTF-16") from exc
    if not text.strip():
        raise IngestionError(ErrorCode.FILE_EMPTY, "whitespace only")
