#!/usr/bin/env python3
"""Quick start example for pyqld.

Usage:
    python examples/quickstart.py path/to/file.qld
"""

from __future__ import annotations

import sys
from pathlib import Path

import pyqld


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <file.qld>", file=sys.stderr)
        sys.exit(1)

    qld_path = Path(sys.argv[1])
    if not qld_path.exists():
        print(f"File not found: {qld_path}", file=sys.stderr)
        sys.exit(1)

    ds = pyqld.read(qld_path)
    print(f"Dataset: {ds}")
    print()

    # Show method info
    print(f"Method compounds: {len(ds.method)}")
    print(f"Samples: {len(ds.samples)}")
    print(f"Compounds: {len(ds.compounds)}")
    print(f"Peaks: {len(ds.peaks)}")
    print(f"Chromatograms: {len(ds.chromatograms)}")
    print(f"Spectra: {len(ds.spectra)}")
    print()

    # Show first few samples
    print("Samples:")
    for s in ds.samples[:5]:
        print(f"  [{s.index}] {s.file_name!r}  (id={s.id!r})")
    print()

    # Show first few compounds
    print("Compounds:")
    for c in ds.compounds[:5]:
        print(
            f"  [{c.index}] {c.name!r}  "
            f"fn={c.function} trace={c.quan_trace!r}"
        )
    print()

    # Show integrations for first sample
    if ds.samples and ds.compounds:
        sample = ds.samples[0]
        print(f"Integrations for sample {sample.file_name!r}:")
        detected = 0
        for c in ds.compounds[:10]:
            pair = ds.pair(sample, c)
            area = pair.area
            rt = pair.retention_time
            status = "detected" if area and area > 0 else "not detected"
            if area and area > 0:
                detected += 1
            print(
                f"  {c.name:20s}  area={area:>12}  "
                f"rt={rt:>8}  {status}"
            )
        print(f"  ... ({detected} detected in first 10 compounds)")


if __name__ == "__main__":
    main()
