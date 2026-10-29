# Copyright 2020 Alibaba Group Holding Limited.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Probe Windows CRT binary I/O without building NeuG or installing dependencies.

Compare the current omitted translation flag with explicit _O_BINARY for:
storage-file copying, copying and reopening a SQLite FTS5 database, and WAL-like
offset writes. The WAL payload is synthetic, not NeuG's serialization format.
This diagnoses byte preservation, not full NeuG recovery/checkpoint behavior.
"""

import ctypes
import json
import os
import platform
import sqlite3
import tempfile
from pathlib import Path


class CRT:
    def __init__(self):
        self.dll = ctypes.CDLL("ucrtbase.dll", use_errno=True)
        signatures = {
            "_wopen": ([ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
            "_read": ([ctypes.c_int, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
            "_write": ([ctypes.c_int, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
            "_lseeki64": (
                [ctypes.c_int, ctypes.c_longlong, ctypes.c_int],
                ctypes.c_longlong,
            ),
            "_chsize_s": ([ctypes.c_int, ctypes.c_longlong], ctypes.c_int),
            "_commit": ([ctypes.c_int], ctypes.c_int),
            "_close": ([ctypes.c_int], ctypes.c_int),
            "_get_fmode": ([ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = argtypes, restype

    def call(self, name, *args):
        result = getattr(self.dll, name)(*args)
        if result < 0 or (name in ("_chsize_s", "_get_fmode") and result != 0):
            raise OSError(
                "{} failed: result={}, errno={}".format(
                    name, result, ctypes.get_errno()
                )
            )
        return result

    def open(self, path, flags, binary):
        return self.call("_wopen", str(path), flags | (0x8000 if binary else 0), 0x0180)

    def read_at(self, fd, count, offset):
        old = self.call("_lseeki64", fd, 0, os.SEEK_CUR)
        self.call("_lseeki64", fd, offset, os.SEEK_SET)
        buffer = ctypes.create_string_buffer(count)
        try:
            length = self.call("_read", fd, buffer, count)
            return buffer.raw[:length]
        finally:
            self.call("_lseeki64", fd, old, os.SEEK_SET)

    def write_at(self, fd, data, offset):
        # Same logical-byte offsets as LocalWalWriter::write_all_at / pwrite_all.
        total = 0
        while total < len(data):
            self.call("_lseeki64", fd, offset + total, os.SEEK_SET)
            buffer = ctypes.create_string_buffer(data[total:])
            written = self.call("_write", fd, buffer, len(data) - total)
            if written == 0:
                raise OSError("Short write")
            total += written


def copy_file(crt, source, destination, binary):
    source_fd = crt.open(source, 0, binary)  # O_RDONLY
    try:
        dest_fd = crt.open(destination, 0x0001 | 0x0100 | 0x0200, binary)
        try:
            size = source.stat().st_size
            crt.call("_chsize_s", dest_fd, size)
            offset = 0
            while offset < size:
                data = crt.read_at(source_fd, min(size - offset, 4096), offset)
                if not data:
                    raise OSError(
                        "Unexpected EOF at {} of {} bytes".format(offset, size)
                    )
                if any(data):
                    crt.write_at(dest_fd, data, offset)
                offset += len(data)
        finally:
            crt.call("_close", dest_fd)
    finally:
        crt.call("_close", source_fd)


def first_difference(expected, actual):
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            return index
    return None if len(expected) == len(actual) else min(len(expected), len(actual))


def copied_bytes(crt, source, destination, binary):
    error = None
    try:
        copy_file(crt, source, destination, binary)
    except OSError as exc:
        error = str(exc)
    expected = source.read_bytes()
    actual = destination.read_bytes() if destination.exists() else b""
    return {
        "copy_error": error,
        "source_bytes": len(expected),
        "destination_bytes": len(actual),
        "bytes_equal": expected == actual,
        "first_difference": first_difference(expected, actual),
    }


def sqlite_rows(path):
    connection = sqlite3.connect(str(path))
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        rows = connection.execute(
            "SELECT rowid FROM docs WHERE docs MATCH 'fox'"
        ).fetchall()
        return {"integrity": integrity, "fox_matches": rows}
    finally:
        connection.close()


def wal_bytes(crt, path, binary):
    # Deliberately include LF, CRLF, CTRL+Z, NUL, and non-ASCII bytes.
    records = [b"graph\nwal_graph\r\n" + bytes([0x1A, 0, 0xFF]), b"drop\nwal_graph"]
    terminator = bytes(16)
    fd = crt.open(path, 0x0002 | 0x0100 | 0x0200, binary)
    try:
        # Small allocation: the previous 1 GiB timing experiment is unnecessary.
        crt.call("_chsize_s", fd, 65536)
        offset = 0
        for record in records:
            crt.write_at(fd, record, offset)
            crt.write_at(fd, terminator, offset + len(record))
            crt.call("_commit", fd)
            offset += len(record)
    finally:
        crt.call("_close", fd)
    expected = b"".join(records) + terminator
    actual = path.read_bytes()[: len(expected)]
    return {
        "bytes_equal": actual == expected,
        "first_difference": first_difference(expected, actual),
    }


def main():
    if os.name != "nt":
        raise SystemExit("Run this diagnostic on Windows")
    crt = CRT()
    fmode = ctypes.c_int()
    crt.call("_get_fmode", ctypes.byref(fmode))
    print(
        json.dumps(
            {
                "os": platform.platform(),
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "crt_default_fmode": hex(fmode.value),
            }
        ),
        flush=True,
    )
    results = []
    with tempfile.TemporaryDirectory(prefix="neug-binary-io-") as directory:
        root = Path(directory)
        source = root / "storage.bin"
        source.write_bytes(
            (b"prefix\r\nline\n" + bytes([0x1A, 0, 0xFF]) + b"suffix") * 500
        )
        sqlite_source = root / "fts.db"
        connection = sqlite3.connect(str(sqlite_source))
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE VIRTUAL TABLE docs USING fts5(body)")
            connection.executemany(
                "INSERT INTO docs VALUES (?)",
                [("current\r\nfox",), ("alpha\nbeta",), ("before\x1aafter",)],
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            connection.close()
        expected_sqlite = sqlite_rows(sqlite_source)
        for binary in (False, True):
            mode = "binary" if binary else "default"
            storage = copied_bytes(crt, source, root / (mode + ".bin"), binary)
            sqlite_copy = root / (mode + ".db")
            fts = copied_bytes(crt, sqlite_source, sqlite_copy, binary)
            try:
                fts["reopened"] = sqlite_rows(sqlite_copy)
                fts["reopen_matches_source"] = fts["reopened"] == expected_sqlite
            except sqlite3.Error as exc:
                fts["reopen_error"] = str(exc)
                fts["reopen_matches_source"] = False
            result = {
                "mode": mode,
                "storage_copy": storage,
                "sqlite_fts_copy": fts,
                "wal_offset_writes": wal_bytes(crt, root / (mode + ".wal"), binary),
            }
            results.append(result)
            print(json.dumps(result), flush=True)
        # Diagnostic success requires byte-preserving binary control results.
        control = results[-1]
        assert control["storage_copy"]["copy_error"] is None
        assert control["storage_copy"]["bytes_equal"]
        assert control["sqlite_fts_copy"]["copy_error"] is None
        assert control["sqlite_fts_copy"]["bytes_equal"]
        assert control["sqlite_fts_copy"]["reopen_matches_source"]
        assert control["wal_offset_writes"]["bytes_equal"]


if __name__ == "__main__":
    main()
