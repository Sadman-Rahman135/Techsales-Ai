"""Malware scanning at upload (blueprint §7.1).

Infected files are quarantined in the sense that they are blocked before storage: nothing is written, the
version is never created, and the attempt is recorded in the audit log with the checksum and signature name.
A scanner that cannot be reached fails closed (the upload is refused), never open.

- `builtin` (development/test): the EICAR test signature only, so the blocking path can be exercised.
- `clamd` (required in staging/production by app/config.py): ClamAV's daemon over its INSTREAM protocol.
"""

import socket
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from app.config import MalwareScanner, get_settings

# Built from two halves so this source file itself never matches an antivirus signature.
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    signature: str | None = None


class ScannerUnavailable(Exception):
    pass


class Scanner(Protocol):
    def scan(self, data: bytes) -> ScanResult: ...


class BuiltinScanner:
    def scan(self, data: bytes) -> ScanResult:
        if EICAR in data:
            return ScanResult(clean=False, signature="Eicar-Test-Signature")
        return ScanResult(clean=True)


class ClamdScanner:
    CHUNK = 64 * 1024

    def __init__(self, host: str, port: int, timeout: float = 30.0) -> None:
        self._address = (host, port)
        self._timeout = timeout

    def scan(self, data: bytes) -> ScanResult:
        try:
            with socket.create_connection(self._address, timeout=self._timeout) as conn:
                conn.sendall(b"zINSTREAM\0")
                for start in range(0, len(data), self.CHUNK):
                    piece = data[start : start + self.CHUNK]
                    conn.sendall(struct.pack(">I", len(piece)) + piece)
                conn.sendall(struct.pack(">I", 0))
                reply = b""
                while not reply.endswith(b"\0"):
                    received = conn.recv(4096)
                    if not received:
                        break
                    reply += received
        except OSError as exc:
            raise ScannerUnavailable(type(exc).__name__) from exc
        answer = reply.rstrip(b"\0").decode("utf-8", "replace").strip()
        # "stream: OK" | "stream: <Signature> FOUND" | "INSTREAM size limit exceeded. ERROR"
        if answer.endswith("OK"):
            return ScanResult(clean=True)
        if answer.endswith("FOUND"):
            return ScanResult(
                clean=False, signature=answer.removeprefix("stream:").removesuffix("FOUND").strip()
            )
        raise ScannerUnavailable(answer[:200] or "empty reply")


@lru_cache
def get_scanner() -> Scanner:
    settings = get_settings()
    if settings.malware_scanner is MalwareScanner.CLAMD:
        return ClamdScanner(settings.clamd_host, settings.clamd_port)
    return BuiltinScanner()
