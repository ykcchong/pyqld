"""Compare codec implementations on a local QLD file (no I/O in timed loops).

Run: python examples/benchmark_codec.py path/to/file.qld --repeat 5
"""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable

import olefile

from pyqld import codec


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file")
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    with olefile.OleFileIO(args.file) as ole:
        streams = [
            ole.openstream(path).read()
            for path in ole.listdir()
            if path[-1].lower().startswith("datastream.")
        ]
    if not streams:
        parser.error("no DataStream payloads found")
    reference = [codec._lzss_decode_python(data) for data in streams]
    plaintext = [codec.decrypt(data) for data in reference]
    table = bytes(i ^ codec.XOR_KEY for i in range(256))
    methods: list[tuple[str, Callable[[bytes], bytes], list[bytes]]] = [
        ("Python LZSS", codec._lzss_decode_python, reference),
        (
            "Python LZSS + XOR",
            lambda data: codec.decrypt(codec._lzss_decode_python(data)),
            plaintext,
        ),
        (
            "Python LZSS + translate",
            lambda data: codec._lzss_decode_python(data).translate(table),
            plaintext,
        ),
    ]
    if codec._native_decode is not None:
        methods.extend(
            [
                ("Native LZSS", codec.lzss_decode, reference),
                ("Native LZSS + fused XOR", codec.decode_quan_stream, plaintext),
            ]
        )
    print(
        f"{len(streams)} streams; {sum(map(len, streams)):,} compressed bytes; "
        f"{sum(map(len, reference)):,} decoded bytes"
    )
    for name, decode, expected in methods:
        assert [decode(data) for data in streams] == expected, name
        timings = []
        for _ in range(args.repeat):
            start = time.perf_counter()
            for data in streams:
                decode(data)
            timings.append(time.perf_counter() - start)
        print(f"{name:28s} {statistics.median(timings) * 1000:10.3f} ms (median)")


if __name__ == "__main__":
    main()
