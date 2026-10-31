"""pyqld — read Waters TargetLynx ``.qld`` quantitation files.

A pure-Python library (no Windows / MFC dependency) for decoding and
navigating Waters TargetLynx ``.qld`` files on any platform.

Quick start
-----------

.. code-block:: python

    import pyqld

    ds = pyqld.read("UMP 20260508 PS.qld")
    print(ds)  # <Dataset samples=13 compounds=146 peaks=...>

    pair = ds.pair("UMP B", "5-HIAA")
    print(pair.area, pair.retention_time)

    for ch in pair.chromatograms:
        print(ch.trace, len(ch.times))

Public API
----------

High-level
~~~~~~~~~~
* :func:`read`              — read a ``.qld`` file into a navigable dataset.
* :func:`open_qld`          — read a ``.qld`` file into a :class:`QLDFile`.
* :class:`QLDFile`          — in-memory representation of one ``.qld``.
* :func:`to_json`           — serialize a :class:`QLDFile` to JSON.
* :func:`export_json`       — convenience: ``.qld`` → JSON file on disk.
* :func:`export_dir`        — export to a directory tree of JSON files.

Object model
~~~~~~~~~~~~
* :class:`Dataset`          — high-level wrapper with attribute access.
* :class:`Sample`           — one row of the samples table (``QD1``).
* :class:`Compound`         — one row of the compounds table (``QD3``).
* :class:`QuantitationResult` — one row of the results table (``QD2``).
* :class:`Chromatogram`     — one ``[Chromatogram]`` section from ``QD5``.
* :class:`Spectrum`         — one ``[Spectrum]`` section from ``QD7``.
* :class:`CompoundResult`   — join view for a ``(sample, compound)`` pair.
* :class:`MethodCompound`   — one row of the method-compounds table (``QD4``).
* :class:`Method`           — analytical method view.

``Peak`` and ``SampleCompound`` remain compatibility aliases.

Mid-level parsers
~~~~~~~~~~~~~~~~~
* :class:`DBFTable`         — parsed dBase-III table.
* :func:`parse_dbf`         — parse dBase bytes.
* :class:`IniSection`       — parsed INI section.
* :class:`IniBlob`          — raw binary blob inside an INI section.
* :func:`parse_ini`         — parse INI bytes.

Low-level codec
~~~~~~~~~~~~~~~
* :func:`decode_quan_stream` — full decode (LZSS + XOR) of one substream.
* :func:`lzss_decode`        — LZSS decompression only.
* :func:`decrypt`            — XOR decryption only.
* :data:`XOR_KEY`            — the XOR key constant (``0x65``).
"""

from __future__ import annotations

from pathlib import Path

from .assays import (
    AssayTransition,
    export_skyline,
    export_traml,
    to_targeted_experiment,
)
from .codec import XOR_KEY, decode_quan_stream, decrypt, lzss_decode
from .dbf import DBFTable, parse_dbf
from .domain import (
    AcquisitionChannel,
    AcquisitionFunction,
    AcquisitionInfo,
    AcquisitionReport,
    AuditEvent,
    CalibrationCurve,
    CalibrationPoint,
    Integration,
    MRMTransition,
    Quality,
)
from .ini import IniBlob, IniSection, parse_ini
from .interop import (
    OpenMSConversion,
    SpectrumMetadata,
    export_mzml,
    export_quantitation,
    quantitation_report,
    to_matchms,
    to_openms,
)
from .model import (
    Chromatogram,
    Compound,
    CompoundResult,
    Dataset,
    Method,
    MethodCompound,
    Peak,
    QuantitationResult,
    Sample,
    SampleCompound,
    Spectrum,
)
from .qld import QLDFile, export_dir, export_json, open_qld, to_json
from .quantitation import (
    CalibrationDiagnostics,
    LinearCalibration,
    PeakIntegration,
    integrate_peak,
)
from .validation import ValidationIssue, ValidationReport


def read(path: str | Path, *, strict: bool = False) -> Dataset:
    """Read a QLD; opt into strict compression and structural parsing with ``strict``."""
    return Dataset.from_path(path, strict=strict)


__all__ = [
    # Version
    "__version__",
    # Object model
    "Dataset",
    "Sample",
    "Compound",
    "MethodCompound",
    "QuantitationResult",
    "Peak",
    "Chromatogram",
    "Spectrum",
    "SampleCompound",
    "CompoundResult",
    "Method",
    "Integration",
    "Quality",
    "CalibrationPoint",
    "CalibrationCurve",
    "AcquisitionReport",
    "AcquisitionInfo",
    "AcquisitionChannel",
    "AcquisitionFunction",
    "MRMTransition",
    "AuditEvent",
    "LinearCalibration",
    "CalibrationDiagnostics",
    "PeakIntegration",
    "integrate_peak",
    "ValidationIssue",
    "ValidationReport",
    # Optional MS interoperability (engines are imported on demand)
    "OpenMSConversion",
    "SpectrumMetadata",
    "AssayTransition",
    "to_openms",
    "export_mzml",
    "to_matchms",
    "quantitation_report",
    "export_quantitation",
    "to_targeted_experiment",
    "export_traml",
    "export_skyline",
    # High-level I/O
    "read",
    "QLDFile",
    "open_qld",
    "to_json",
    "export_json",
    "export_dir",
    # Mid-level parsers
    "DBFTable",
    "parse_dbf",
    "IniSection",
    "IniBlob",
    "parse_ini",
    # Low-level codec
    "XOR_KEY",
    "decode_quan_stream",
    "lzss_decode",
    "decrypt",
]

__version__ = "0.2.0"
