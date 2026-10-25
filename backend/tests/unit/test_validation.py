"""Blueprint §7.1: allowlist, content signatures, size/page limits, encrypted/corrupt/empty files, names."""

import hashlib
import io
import zipfile

import pytest

from app.config import get_settings
from app.ingestion.errors import ErrorCode, IngestionError
from app.ingestion.validation import DOCX_MIME, OLE_MAGIC, sanitize_filename, validate_upload
from tests.builders import docx_bytes, pdf_bytes

SETTINGS = get_settings()
PDF = pdf_bytes([[("h1", "Returns"), ("p", "Return within 30 days.")]])
DOCX = docx_bytes([("h1", "Returns"), ("p", "Return within 30 days.")])


def rejected(filename: str, data: bytes, mime: str | None = None, **settings: int) -> ErrorCode:
    with pytest.raises(IngestionError) as info:
        validate_upload(filename, mime, data, SETTINGS.model_copy(update=settings))
    return info.value.code


@pytest.mark.parametrize(
    ("filename", "data", "source_type", "mime"),
    [
        ("policy.pdf", PDF, "pdf", "application/pdf"),
        ("Policy.DOCX", DOCX, "docx", DOCX_MIME),
        ("notes.txt", b"Return within 30 days.", "txt", "text/plain"),
        ("utf16.txt", "Retour sous 30 jours.".encode("utf-16"), "txt", "text/plain"),
    ],
    ids=["pdf", "docx", "txt", "txt-utf16"],
)
def test_allowed_files_pass_with_canonical_type_and_checksum(
    filename: str, data: bytes, source_type: str, mime: str
) -> None:
    result = validate_upload(filename, None, data, SETTINGS)
    assert (result.source_type, result.mime_type) == (source_type, mime)
    assert result.checksum == hashlib.sha256(data).hexdigest()
    assert result.size_bytes == len(data)


def test_pdf_page_count_is_recorded() -> None:
    assert validate_upload("p.pdf", "application/pdf", PDF, SETTINGS).page_count == 1


@pytest.mark.parametrize(
    "name", ["setup.exe", "macro.docm", "sheet.xlsx", "image.png", "noextension", "script.sh"]
)
def test_extensions_outside_the_allowlist_are_rejected(name: str) -> None:
    assert rejected(name, b"anything") is ErrorCode.FILE_TYPE_NOT_ALLOWED


@pytest.mark.parametrize("magic", [b"MZ\x90\x00", b"\x7fELF\x02", b"#!/bin/sh\n", b"\xcf\xfa\xed\xfe"])
def test_executables_renamed_to_an_allowed_extension_are_rejected(magic: bytes) -> None:
    assert rejected("manual.pdf", magic + b"\x00" * 64) is ErrorCode.FILE_TYPE_NOT_ALLOWED
    assert rejected("manual.txt", magic + b"rest") is ErrorCode.FILE_TYPE_NOT_ALLOWED


def test_contents_must_match_the_extension() -> None:
    assert rejected("manual.pdf", b"just some text") is ErrorCode.FILE_TYPE_MISMATCH
    assert rejected("manual.docx", PDF) is ErrorCode.FILE_TYPE_MISMATCH
    assert rejected("manual.txt", b"text\x00with nul bytes") is ErrorCode.FILE_TYPE_MISMATCH


def test_declared_mime_type_is_cross_checked_but_unknown_is_tolerated() -> None:
    assert rejected("manual.pdf", PDF, "image/png") is ErrorCode.FILE_TYPE_MISMATCH
    assert validate_upload("manual.pdf", "application/octet-stream", PDF, SETTINGS).source_type == "pdf"
    assert validate_upload("manual.pdf", "", PDF, SETTINGS).source_type == "pdf"


def test_empty_and_whitespace_only_files_are_rejected() -> None:
    assert rejected("empty.pdf", b"") is ErrorCode.FILE_EMPTY
    assert rejected("blank.txt", b" \n\t \n") is ErrorCode.FILE_EMPTY


def test_size_and_page_limits_are_configurable() -> None:
    assert rejected("big.pdf", PDF, max_upload_bytes=len(PDF) - 1) is ErrorCode.FILE_TOO_LARGE
    three_pages = pdf_bytes([[("p", "one")], [("p", "two")], [("p", "three")]])
    assert rejected("long.pdf", three_pages, max_pdf_pages=2) is ErrorCode.FILE_TOO_MANY_PAGES


def test_password_protected_files_are_rejected() -> None:
    locked_pdf = pdf_bytes([[("p", "secret")]], password="hunter2-hunter2")
    assert rejected("locked.pdf", locked_pdf) is ErrorCode.FILE_ENCRYPTED
    # Office wraps an encrypted .docx in an OLE compound file.
    assert rejected("locked.docx", OLE_MAGIC + b"\x00" * 512) is ErrorCode.FILE_ENCRYPTED


def test_corrupt_files_are_rejected() -> None:
    assert rejected("broken.pdf", b"%PDF-1.7\n" + b"\x00garbage" * 50) is ErrorCode.FILE_CORRUPT
    assert rejected("broken.docx", b"PK\x03\x04" + b"\x00" * 100) is ErrorCode.FILE_CORRUPT
    assert rejected("latin1.txt", "Garantie \u00fcber 12 Monate".encode("latin-1")) is ErrorCode.FILE_CORRUPT


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        for name, content in files.items():
            package.writestr(name, content)
    return buffer.getvalue()


def test_docx_must_be_a_word_package_without_macros() -> None:
    assert rejected("x.docx", _zip({"hello.txt": b"hi"})) is ErrorCode.FILE_TYPE_MISMATCH
    with zipfile.ZipFile(io.BytesIO(DOCX)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files["word/vbaProject.bin"] = b"macro"
    assert rejected("macro.docx", _zip(files)) is ErrorCode.FILE_TYPE_NOT_ALLOWED


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("../../etc/passwd.pdf", "passwd.pdf"),
        ("C:\\Users\\morgan\\Desktop\\Return policy.pdf", "Return policy.pdf"),
        ("bad<name>:|?*.pdf", "bad_name_____.pdf"),
        ("tab\tand\x00null\u202e.pdf", "tabandnull.pdf"),
        ("   ", "document"),
        ("..", "document"),
        (None, "document"),
    ],
)
def test_filenames_are_sanitized_for_display(raw: str | None, clean: str) -> None:
    assert sanitize_filename(raw) == clean


def test_long_filenames_keep_their_extension() -> None:
    name = sanitize_filename("a" * 300 + ".pdf")
    assert len(name) <= 120 and name.endswith(".pdf")


def test_word_packages_that_inflate_past_the_limit_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Zip-bomb guard (§11.2 "unsafe archive behavior"): the declared size is checked before reading.
    from app.ingestion import validation

    monkeypatch.setattr(validation, "MAX_DOCX_UNCOMPRESSED", 1024)
    with zipfile.ZipFile(io.BytesIO(DOCX)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    files["word/media/padding.bin"] = b"\0" * 4096  # compresses to almost nothing, inflates past the limit
    assert rejected("bomb.docx", _zip(files)) is ErrorCode.FILE_CORRUPT
