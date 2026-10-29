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

"""Time Windows WAL CRT operations without building NeuG or installing packages.

Run with the Python installed by the Windows CI job:
    python scripts/benchmark_windows_wal.py

Use --directory to select the same disk as pytest's temporary databases.
Files are created in a unique temporary subdirectory and removed after each run.
This isolates file operations; it does not reproduce full database transactions
or the WAL record format. A zero-size case measures writing without preallocation.
"""

import argparse
import ctypes
import os
import platform
import tempfile
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=None)
    parser.add_argument("--sizes-mib", type=int, nargs="+", default=[0, 1, 16, 1024])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--methods", nargs="+", choices=["crt", "win32"], default=["crt", "win32"]
    )
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This benchmark requires Windows and ucrtbase.dll")
    if args.repeats < 1 or any(size < 0 for size in args.sizes_mib):
        parser.error("repeats must be positive and sizes must be non-negative")

    crt = ctypes.CDLL("ucrtbase.dll", use_errno=True)
    signatures = {
        "_wopen": ([ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int], ctypes.c_int),
        "_chsize_s": ([ctypes.c_int, ctypes.c_longlong], ctypes.c_int),
        "_write": ([ctypes.c_int, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
        "_commit": ([ctypes.c_int], ctypes.c_int),
        "_close": ([ctypes.c_int], ctypes.c_int),
        "_get_osfhandle": ([ctypes.c_int], ctypes.c_ssize_t),
    }
    for name, (argtypes, restype) in signatures.items():
        function = getattr(crt, name)
        function.argtypes = argtypes
        function.restype = restype

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    set_info.restype = ctypes.c_int

    def resize(fd, size, method):
        if method == "crt":
            checked("_chsize_s", fd, size)
        else:
            end_of_file = ctypes.c_longlong(size)
            # FileEndOfFileInfo = 6; does not move the file pointer.
            if not set_info(
                checked("_get_osfhandle", fd),
                6,
                ctypes.byref(end_of_file),
                ctypes.sizeof(end_of_file),
            ):
                raise ctypes.WinError(ctypes.get_last_error())

    def checked(name, *values):
        result = getattr(crt, name)(*values)
        if result < 0 or (name == "_chsize_s" and result != 0):
            error = result if name == "_chsize_s" else ctypes.get_errno()
            raise OSError(error, "{} failed".format(name))
        return result

    def timed(operation):
        start = time.perf_counter()
        operation()
        return (time.perf_counter() - start) * 1000

    payload = ctypes.create_string_buffer(b"x" * 4096)
    print("OS: {}; Python: {}".format(platform.platform(), platform.python_version()))
    print("CRT: ucrtbase.dll; payload: 4096 bytes; times: milliseconds")
    with tempfile.TemporaryDirectory(
        prefix="neug-wal-bench-", dir=args.directory
    ) as tmp:
        print("Directory: {}".format(tmp), flush=True)
        print(
            "method,mib,repeat,open_ms,resize_ms,write1_ms,commit1_ms,write2_ms,commit2_ms,close_ms",
            flush=True,
        )
        for method in args.methods:
            for size in args.sizes_mib:
                for repeat in range(1, args.repeats + 1):
                    path = Path(tmp) / "sample.wal"
                    print(
                        "Starting {}: {} MiB, repeat {}".format(method, size, repeat),
                        flush=True,
                    )
                    # Match NeuG's O_RDWR | O_CREAT | O_TRUNC and read/write mode.
                    start = time.perf_counter()
                    fd = checked("_wopen", str(path), 0x0002 | 0x0100 | 0x0200, 0x0180)
                    values = [(time.perf_counter() - start) * 1000]
                    try:
                        values.append(timed(lambda: resize(fd, size * 1024**2, method)))
                        # _chsize_s restores the original file position. Both writes
                        # occur near the start, as WAL appends do after preallocation.
                        for _ in range(2):

                            def write_payload():
                                if checked("_write", fd, payload, 4096) != 4096:
                                    raise OSError("Short WAL payload write")

                            values.append(timed(write_payload))
                            values.append(timed(lambda: checked("_commit", fd)))
                    finally:
                        values.append(timed(lambda: checked("_close", fd)))
                        path.unlink()
                    print(
                        "{},{},{},{}".format(
                            method,
                            size,
                            repeat,
                            ",".join("{:.3f}".format(v) for v in values),
                        ),
                        flush=True,
                    )


if __name__ == "__main__":
    main()
