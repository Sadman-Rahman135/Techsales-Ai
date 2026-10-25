"""Blueprint §7.1 malware scanning: builtin scanner, and the clamd INSTREAM client against a fake daemon."""

import socket
import struct
import threading
from collections.abc import Iterator

import pytest

from app.ingestion.scanning import EICAR, BuiltinScanner, ClamdScanner, ScannerUnavailable


def test_builtin_scanner_flags_the_eicar_test_signature() -> None:
    assert BuiltinScanner().scan(b"%PDF-1.7 " + EICAR).clean is False
    assert BuiltinScanner().scan(b"a normal policy document").clean is True


@pytest.fixture
def fake_clamd() -> Iterator[tuple[int, list[bytes]]]:
    """A one-connection-at-a-time clamd stand-in: replies FOUND when the stream contains EICAR."""
    server = socket.create_server(("127.0.0.1", 0))
    received: list[bytes] = []

    def serve() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                assert conn.recv(10) == b"zINSTREAM\0"
                data = b""
                while True:
                    (size,) = struct.unpack(">I", conn.recv(4, socket.MSG_WAITALL))
                    if size == 0:
                        break
                    data += conn.recv(size, socket.MSG_WAITALL)
                received.append(data)
                conn.sendall(b"stream: Eicar-Signature FOUND\0" if EICAR in data else b"stream: OK\0")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1], received
    server.close()


def test_clamd_scanner_streams_the_file_in_chunks(fake_clamd: tuple[int, list[bytes]]) -> None:
    port, received = fake_clamd
    scanner = ClamdScanner("127.0.0.1", port)
    data = b"x" * (ClamdScanner.CHUNK * 2 + 10)
    assert scanner.scan(data).clean is True
    assert received == [data]
    result = scanner.scan(b"prefix " + EICAR)
    assert (result.clean, result.signature) == (False, "Eicar-Signature")


def test_unreachable_clamd_fails_closed() -> None:
    with socket.create_server(("127.0.0.1", 0)) as probe:
        port = probe.getsockname()[1]  # bound then closed: nothing listens there
    with pytest.raises(ScannerUnavailable):
        ClamdScanner("127.0.0.1", port, timeout=1).scan(b"data")
