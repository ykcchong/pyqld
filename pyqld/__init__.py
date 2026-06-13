"""pyqld — read Waters TargetLynx ``.qld`` quantitation files.

A pure-Python library (no Windows / MFC dependency) for decoding and
navigating Waters TargetLynx ``.qld`` files on any platform.

Quick start
-----------

.. code-block:: python

    from pyqld import Dataset

    ds = Dataset.from_path("UMP 20260508 PS.qld")
    print(ds)  # <Dataset samples=13 compounds=146 peaks=...>

    pair = ds.pair("UMP B", "5-HIAA")
    print(pair.area, pair.retention_time)

    for ch in pair.chromatograms:
        print(ch.trace, len(ch.times))

Public API
----------

High-level
~~~~~~~~~~
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
* :class:`Peak`             — one row of the peaks table (``QD2``).
* :class:`Chromatogram`     — one ``[Chromatogram]`` section from ``QD5``.
* :class:`Spectrum`         — one ``[Spectrum]`` section from ``QD7``.
* :class:`SampleCompound`   — join view for a ``(sample, compound)`` pair.
* :class:`MethodCompound`   — one row of the method-compounds table (``QD4``).
* :class:`Method`           — analytical method view.

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

from .codec import XOR_KEY, decode_quan_stream, decrypt, lzss_decode
from .dbf import DBFTable, parse_dbf
from .ini import IniBlob, IniSection, parse_ini
from .model import (
    Chromatogram,
    Compound,
    Dataset,
    Method,
    MethodCompound,
    Peak,
    Sample,
    SampleCompound,
    Spectrum,
)
from .qld import QLDFile, export_dir, export_json, open_qld, to_json

__all__ = [
    # Version
    "__version__",
    # Object model
    "Dataset",
    "Sample",
    "Compound",
    "MethodCompound",
    "Peak",
    "Chromatogram",
    "Spectrum",
    "SampleCompound",
    "Method",
    # High-level I/O
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

__version__ = "0.1.0"
