"""Command-line entry point for pyqld.

Usage
-----

.. code-block:: bash

    # Single JSON file
    pyqld input.qld output.json

    # Directory export
    pyqld input.qld -d ./output

    # List streams
    pyqld input.qld --list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .qld import export_dir, export_json, open_qld, to_json


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyqld",
        description=(
            "Decode a Waters TargetLynx .qld file and export its full "
            "contents as JSON."
        ),
    )
    p.add_argument("input", type=Path, help="Path to the .qld file")
    p.add_argument(
        "output",
        type=Path,
        nargs="?",
        help=(
            "Output path for single-file mode (default: <input>.json). "
            "Use '-' for stdout. Ignored when --out-dir is given."
        ),
    )
    p.add_argument(
        "-d",
        "--out-dir",
        type=Path,
        default=None,
        help="Write one JSON file per sub-stream into this directory.",
    )
    p.add_argument(
        "--split-sections",
        action="store_true",
        help="Split large INI streams into one JSON per section.",
    )
    p.add_argument(
        "--split-threshold",
        type=int,
        default=50,
        help="Minimum number of sections to trigger splitting (default: 50).",
    )
    p.add_argument(
        "--indent",
        type=int,
        default=None,
        help="Pretty-print JSON with this indent (default: compact).",
    )
    p.add_argument(
        "--include-raw",
        action="store_true",
        help="Include decoded plaintext (base64 in single-file mode).",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="Only list sub-streams — do not export.",
    )
    return p


def _cmd_list(args: argparse.Namespace) -> int:
    qld = open_qld(args.input)
    for name in qld.stream_names():
        parsed = qld.tables[name]
        kind = type(parsed).__name__
        extra = ""
        if hasattr(parsed, "nrecs"):
            extra = f" nrecs={parsed.nrecs} fields={len(parsed.fields)}"
        elif isinstance(parsed, list):
            extra = f" sections={len(parsed)}"
        print(
            f"{name:6s} {qld.label_for(name):20s} "
            f"size={len(qld.streams[name]):>10d} {kind}{extra}"
        )
    return 0


def _cmd_dir(args: argparse.Namespace) -> int:
    out = export_dir(
        args.input,
        args.out_dir,
        indent=args.indent,
        include_raw=args.include_raw,
        split_sections=args.split_sections,
        split_threshold=args.split_threshold,
    )
    files = sorted(out.rglob("*.json"))
    total = sum(p.stat().st_size for p in files)
    print(
        f"wrote {len(files)} JSON files into {out} ({total:,} bytes total)"
    )
    print(f"  index: {out / 'manifest.json'}")
    return 0


def _cmd_single(args: argparse.Namespace) -> int:
    out_path = (
        args.output
        if args.output is not None
        else args.input.with_suffix(".json")
    )
    if str(out_path) == "-":
        qld = open_qld(args.input)
        sys.stdout.write(
            to_json(qld, indent=args.indent, include_raw=args.include_raw)
        )
        sys.stdout.write("\n")
        return 0
    written = export_json(
        args.input,
        out_path,
        indent=args.indent,
        include_raw=args.include_raw,
    )
    print(f"wrote {written} ({written.stat().st_size:,} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        return _cmd_list(args)
    if args.out_dir is not None:
        return _cmd_dir(args)
    return _cmd_single(args)


if __name__ == "__main__":
    raise SystemExit(main())
