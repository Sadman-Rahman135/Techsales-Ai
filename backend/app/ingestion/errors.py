"""Ingestion error codes (§7.1 "user-friendly error messages", §7.4 retryable vs non-retryable).

The code is stored on the job and returned by the API; the message is admin-facing copy
(design-system/content-guide.md). Retryable errors are transient: the worker requeues the job with backoff
until `max_attempts`. Non-retryable errors fail the job at once, because retrying cannot change the result.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    # upload validation (synchronous, returned by POST /documents)
    FILE_EMPTY = "FILE_EMPTY"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_TYPE_NOT_ALLOWED = "FILE_TYPE_NOT_ALLOWED"
    FILE_TYPE_MISMATCH = "FILE_TYPE_MISMATCH"
    FILE_ENCRYPTED = "FILE_ENCRYPTED"
    FILE_CORRUPT = "FILE_CORRUPT"
    FILE_TOO_MANY_PAGES = "FILE_TOO_MANY_PAGES"
    FILE_MALWARE_DETECTED = "FILE_MALWARE_DETECTED"
    SCANNER_UNAVAILABLE = "SCANNER_UNAVAILABLE"
    DUPLICATE_FILE = "DUPLICATE_FILE"
    TOO_MANY_ACTIVE_INGESTIONS = "TOO_MANY_ACTIVE_INGESTIONS"
    # processing (asynchronous, stored on the job)
    FILE_NO_TEXT = "FILE_NO_TEXT"
    FILE_MISSING = "FILE_MISSING"
    DOCUMENT_GONE = "DOCUMENT_GONE"
    EMBEDDING_FAILED = "EMBEDDING_FAILED"
    EMBEDDING_CONFIG_INVALID = "EMBEDDING_CONFIG_INVALID"
    INDEX_UNAVAILABLE = "INDEX_UNAVAILABLE"
    INDEX_VERIFY_FAILED = "INDEX_VERIFY_FAILED"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.FILE_EMPTY: "This file is empty. Choose a file that contains text.",
    ErrorCode.FILE_TOO_LARGE: "This file is larger than the upload limit. Split it or upload a smaller file.",
    ErrorCode.FILE_TYPE_NOT_ALLOWED: (
        "This file type isn't supported. Upload a PDF, Word (.docx) or text (.txt) file."
    ),
    ErrorCode.FILE_TYPE_MISMATCH: (
        "This file's contents don't match its extension. Save it again in the right format."
    ),
    ErrorCode.FILE_ENCRYPTED: "This file is password-protected. Remove the password and upload it again.",
    ErrorCode.FILE_CORRUPT: "We couldn't open this file. It may be damaged. Save it again and retry.",
    ErrorCode.FILE_TOO_MANY_PAGES: "This PDF has more pages than the limit. Split it into smaller documents.",
    ErrorCode.FILE_MALWARE_DETECTED: "This file was blocked by the malware scan and was not stored.",
    ErrorCode.SCANNER_UNAVAILABLE: (
        "We couldn't scan this file right now, so it wasn't uploaded. Try again shortly."
    ),
    ErrorCode.DUPLICATE_FILE: "This exact file is already uploaded.",
    ErrorCode.TOO_MANY_ACTIVE_INGESTIONS: (
        "Several documents are still processing. Wait for one to finish, then upload again."
    ),
    ErrorCode.FILE_NO_TEXT: (
        "We couldn't read this PDF. It may be a scanned image, and we can't read scanned PDFs yet. "
        "Upload a text-based PDF instead."
    ),
    ErrorCode.FILE_MISSING: "The stored file is missing. Upload the document again.",
    ErrorCode.DOCUMENT_GONE: "This document was deleted or archived while it was processing.",
    ErrorCode.EMBEDDING_FAILED: "The embedding service didn't respond. We'll retry automatically.",
    ErrorCode.EMBEDDING_CONFIG_INVALID: (
        "The embedding service rejected our settings. An administrator must fix them."
    ),
    ErrorCode.INDEX_UNAVAILABLE: "The search index didn't respond. We'll retry automatically.",
    ErrorCode.INDEX_VERIFY_FAILED: "Not every section reached the search index. We'll retry automatically.",
    ErrorCode.LEASE_EXPIRED: "Processing stopped unexpectedly. We'll retry automatically.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong while processing. We'll retry automatically.",
}


def message_for(code: str | None) -> str | None:
    if code is None:
        return None
    try:
        return MESSAGES[ErrorCode(code)]
    except ValueError:
        return MESSAGES[ErrorCode.INTERNAL_ERROR]


class IngestionError(Exception):
    def __init__(self, code: ErrorCode, detail: str = "", *, retryable: bool = False) -> None:
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail  # for logs only; may name internals, never shown to users
        self.retryable = retryable
