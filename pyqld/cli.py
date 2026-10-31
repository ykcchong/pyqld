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
import json
import sys
from pathlib import Path
from typing import Any

from .qld import export_dir, export_json, open_qld, to_json


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyqld",
        description=(
            "Decode a Waters TargetLynx .qld file and export JSON, "
            "retained MS signals, or explicitly described targeted assays."
        ),
    )
    p.add_argument("input", type=Path, help="Path to the .qld file")
    p.add_argument(
        "--strict", action="store_true",
        help="Reject malformed compression, table layouts, and INI structure while reading.",
    )
    p.add_argument(
        "output",
        type=Path,
        nargs="?",
        help=(
            "Output path (default JSON: <input>.json; MS: <input>.sample-N.<format>). "
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
    p.add_argument(
        "--format", choices=("json", "mzml", "quantitation", "skyline", "traml"),
        default="json", help="Export format (default: existing full JSON export).",
    )
    p.add_argument(
        "--sample-index", type=int,
        help="Zero-based injection index, required for MS/quantitation exports.",
    )
    p.add_argument(
        "--allow-inferred-ownership", action="store_true",
        help="Explicitly accept equal-group signal ownership; recorded in provenance.",
    )
    p.add_argument(
        "--assays", type=Path,
        help="JSON array of explicit AssayTransition metadata for Skyline/TraML.",
    )
    p.add_argument(
        "--spectrum-metadata", type=Path,
        help="JSON object of QD7 index to verified SpectrumMetadata for mzML.",
    )
    p.add_argument(
        "--allow-nonconformant-traml", action="store_true",
        help=(
            "Explicitly allow TraML failing OpenMS semantic validation; "
            "warns and records errors."
        ),
    )
    return p


def _cmd_list(args: argparse.Namespace) -> int:
    qld = open_qld(args.input, **({"strict": True} if args.strict else {}))
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
        **({"strict": True} if args.strict else {}),
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
        qld = open_qld(args.input, **({"strict": True} if args.strict else {}))
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
        **({"strict": True} if args.strict else {}),
    )
    print(f"wrote {written} ({written.stat().st_size:,} bytes)")
    return 0


def _cmd_interop(args: argparse.Namespace) -> int:
    from .assays import AssayTransition, export_skyline, export_traml
    from .interop import SpectrumMetadata, export_mzml, export_quantitation
    from .model import Dataset

    dataset = Dataset.from_path(args.input, **({"strict": True} if args.strict else {}))
    if not 0 <= args.sample_index < len(dataset.samples):
        raise ValueError("--sample-index is outside the dataset")
    suffix = {"mzml": ".mzML", "quantitation": ".json", "skyline": ".csv", "traml": ".TraML"}
    destination = args.output or args.input.with_name(
        f"{args.input.stem}.sample-{args.sample_index}{suffix[args.format]}"
    )
    if str(destination) == "-":
        raise ValueError("MS exports require a file path; stdout is not supported")
    options = {"allow_inferred_ownership": args.allow_inferred_ownership}
    if args.format == "mzml":
        metadata = None
        if args.spectrum_metadata is not None:
            data = _read_metadata(args.spectrum_metadata)
            if not isinstance(data, dict):
                raise ValueError("--spectrum-metadata must contain a JSON object")
            metadata = {}
            for key, value in data.items():
                if not key.isdecimal() or str(int(key)) != key or not isinstance(value, dict):
                    raise ValueError(
                        "spectrum metadata needs QD7 integer keys and object values"
                    )
                metadata[int(key)] = SpectrumMetadata(**value)
        written = export_mzml(
            dataset, args.sample_index, destination, spectrum_metadata=metadata, **options,
        )
    elif args.format == "quantitation":
        written = export_quantitation(dataset, args.sample_index, destination, **options)
    else:
        data = _read_metadata(args.assays)
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("--assays must contain a JSON array of metadata objects")
        assays = [AssayTransition(**row) for row in data]
        if args.format == "skyline":
            written = export_skyline(dataset, args.sample_index, assays, destination, **options)
        else:
            written = export_traml(
                dataset, args.sample_index, assays, destination,
                allow_nonconformant=args.allow_nonconformant_traml, **options,
            )
    print(f"wrote {written} ({written.stat().st_size:,} bytes)")
    if args.format != "quantitation":
        print(f"  provenance: {written}.json")
    return 0


def _read_metadata(path: Path) -> Any:
    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate metadata key: {key!r}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.format != "json":
        if args.sample_index is None:
            parser.error("--sample-index is required for MS/quantitation export")
        if (args.list or args.out_dir is not None or args.include_raw or args.split_sections
                or args.indent is not None or args.split_threshold != 50):
            parser.error("JSON/list options cannot be combined with MS/quantitation export")
        if args.format in {"skyline", "traml"} and args.assays is None:
            parser.error("--assays is required for Skyline/TraML export")
        if args.assays is not None and args.format not in {"skyline", "traml"}:
            parser.error("--assays applies only to Skyline/TraML export")
        if args.spectrum_metadata is not None and args.format != "mzml":
            parser.error("--spectrum-metadata applies only to mzML export")
        if args.allow_nonconformant_traml and args.format != "traml":
            parser.error("--allow-nonconformant-traml applies only to TraML export")
        try:
            return _cmd_interop(args)
        except (ValueError, TypeError, ImportError, OSError) as error:
            parser.error(f"export failed: {error}")
    elif (args.sample_index is not None or args.allow_inferred_ownership
          or args.assays is not None or args.spectrum_metadata is not None
          or args.allow_nonconformant_traml):
        parser.error("MS export options require a non-JSON --format")
    if args.list:
        return _cmd_list(args)
    if args.out_dir is not None:
        return _cmd_dir(args)
    return _cmd_single(args)


if __name__ == "__main__":
    raise SystemExit(main())
