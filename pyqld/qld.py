"""High-level ``.qld`` file reader and JSON exporter.

A ``.qld`` file is an OLE2 Compound Document containing named substreams
under ``QuanData/DataStream.QDx``.  Each substream is LZSS-compressed and
XOR-encrypted.  After decoding, the plaintext is either a dBase-III variant
table or an INI-style nested section document.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codec import decode_quan_stream
from .dbf import DBFTable, is_dbf, parse_dbf
from .ini import IniSection, parse_ini

#: Mapping from OLE2 stream basename to human-readable label.
STREAM_LABELS: dict[str, str] = {
    "QD1": "samples",
    "QD2": "peaks",
    "QD3": "compounds",
    "QD4": "method_compounds",
    "QD5": "chromatograms",
    "QD6": "sample_info",
    "QD7": "spectra",
    "qld": "audit",
}

_QD_STREAM_RE = re.compile(r"(?i)QuanData[\\/].*DataStream\.(QD\d+|qld)$")


# ---------------------------------------------------------------------------
# QLDFile
# ---------------------------------------------------------------------------


@dataclass
class QLDFile:
    """In-memory representation of one ``.qld`` file.

    ``streams`` maps raw OLE2 stream basenames (e.g. ``"QD1"``) to decoded
    plaintext bytes.  ``tables`` holds the parsed structured form for each
    stream (a :class:`DBFTable` or a list of :class:`IniSection`).
    """

    path: str
    streams: dict[str, bytes] = field(default_factory=dict)
    tables: dict[str, Any] = field(default_factory=dict)

    def stream_names(self) -> list[str]:
        """Return the list of decoded stream basenames."""
        return list(self.streams.keys())

    def label_for(self, stream: str) -> str:
        """Return the human-readable label for a stream basename."""
        return STREAM_LABELS.get(stream, stream)

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        """Convert to a JSON-ready dictionary.

        Parameters
        ----------
        include_raw : bool
            If ``True``, include base64-encoded plaintext for each stream.
        """
        out: dict[str, Any] = {
            "format": "pyqld/v1",
            "source": self.path,
            "streams": {},
        }
        for stream, parsed in self.tables.items():
            label = self.label_for(stream)
            entry: dict[str, Any] = {"label": label}
            if isinstance(parsed, DBFTable):
                entry.update(parsed.to_dict())
            elif (
                isinstance(parsed, list)
                and parsed
                and isinstance(parsed[0], IniSection)
            ):
                entry["kind"] = "ini"
                entry["sections"] = [
                    {"name": s.name, "body": s.to_dict()} for s in parsed
                ]
            else:
                entry["kind"] = "raw"
            if include_raw:
                import base64

                entry["raw_b64"] = base64.b64encode(
                    self.streams[stream]
                ).decode("ascii")
            out["streams"][stream] = entry
        return out


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _iter_ole_streams(ole_path: Path) -> Iterable[tuple[str, bytes]]:
    """Yield ``(basename, raw_bytes)`` for each TargetLynx data sub-stream."""
    try:
        import olefile
    except ImportError as e:
        raise RuntimeError(
            "pyqld requires the 'olefile' package (pip install olefile)"
        ) from e

    with olefile.OleFileIO(str(ole_path)) as ole:
        for entry in ole.listdir(streams=True, storages=False):
            joined = "/".join(entry)
            m = _QD_STREAM_RE.search(joined)
            if not m:
                continue
            basename = m.group(1)
            with ole.openstream(entry) as fh:
                yield basename, fh.read()


def open_qld(path: str | Path) -> QLDFile:
    """Open a ``.qld`` file, decode every sub-stream, and parse it.

    Parameters
    ----------
    path : str | Path
        Path to the ``.qld`` file.

    Returns
    -------
    QLDFile
        Decoded and parsed file.

    Parsing rules
    --------------
    * If the decoded payload starts with the dBase magic ``0x037E`` the
      stream is parsed by :func:`pyqld.dbf.parse_dbf`.
    * Otherwise it is parsed by :func:`pyqld.ini.parse_ini`.
    """
    p = Path(path)
    qld = QLDFile(path=str(p.resolve()))

    for name, raw in _iter_ole_streams(p):
        plaintext = decode_quan_stream(raw)
        qld.streams[name] = plaintext
        if is_dbf(plaintext):
            qld.tables[name] = parse_dbf(plaintext)
        else:
            qld.tables[name] = parse_ini(plaintext)

    return qld


# ---------------------------------------------------------------------------
# JSON serialization
# ---------------------------------------------------------------------------


def to_json(
    qld: QLDFile,
    *,
    indent: int | None = None,
    include_raw: bool = False,
) -> str:
    """Serialize a :class:`QLDFile` to a JSON string."""
    return json.dumps(
        qld.to_dict(include_raw=include_raw),
        indent=indent,
        ensure_ascii=False,
        default=str,
    )


def export_json(
    qld_path: str | Path,
    json_path: str | Path,
    *,
    indent: int | None = None,
    include_raw: bool = False,
) -> Path:
    """Read ``qld_path`` and write its JSON form to ``json_path``."""
    qld = open_qld(qld_path)
    out = Path(json_path)
    out.write_text(
        to_json(qld, indent=indent, include_raw=include_raw), encoding="utf-8"
    )
    return out


# ---------------------------------------------------------------------------
# Directory exporter
# ---------------------------------------------------------------------------


def _dbf_stream_dict(table: DBFTable, label: str) -> dict[str, Any]:
    d = table.to_dict()
    d["label"] = label
    return d


def _ini_stream_dict(
    sections: list[IniSection], label: str
) -> dict[str, Any]:
    return {
        "kind": "ini",
        "label": label,
        "sections": [
            {"name": s.name, "body": s.to_dict()} for s in sections
        ],
    }


def _write_json(path: Path, obj: Any, indent: int | None) -> int:
    text = json.dumps(obj, indent=indent, ensure_ascii=False, default=str)
    path.write_text(text, encoding="utf-8")
    return len(text)


def export_dir(
    qld_path: str | Path,
    out_dir: str | Path,
    *,
    indent: int | None = None,
    include_raw: bool = False,
    split_sections: bool = False,
    split_threshold: int = 50,
) -> Path:
    """Write the ``.qld`` contents to a directory tree of JSON files.

    Layout::

        out_dir/
            manifest.json
            samples.json
            peaks.json
            compounds.json
            method_compounds.json
            chromatograms.json      (or chromatograms/ if split_sections)
            sample_info.json
            spectra.json            (or spectra/ if split_sections)
            audit.json
            raw/<stream>.bin        (only if include_raw=True)
    """
    qld = open_qld(qld_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "format": "pyqld/v1",
        "source": qld.path,
        "streams": {},
    }

    for stream, parsed in qld.tables.items():
        label = qld.label_for(stream)
        entry: dict[str, Any] = {"label": label, "stream": stream}

        if isinstance(parsed, DBFTable):
            target = out / f"{label}.json"
            size = _write_json(
                target, _dbf_stream_dict(parsed, label), indent
            )
            entry.update(
                {
                    "kind": "dbf",
                    "file": target.name,
                    "nrecs": parsed.nrecs,
                    "n_fields": len(parsed.fields),
                    "bytes": size,
                }
            )

        elif isinstance(parsed, list):  # list[IniSection]
            n_sections = len(parsed)
            if split_sections and n_sections > split_threshold:
                sub = out / label
                sub.mkdir(exist_ok=True)
                singular = (
                    label[:-1] if label.endswith("s") else label
                )
                width = max(4, len(str(n_sections)))
                files = []
                for i, sec in enumerate(parsed):
                    fname = f"{singular}_{i + 1:0{width}d}.json"
                    fpath = sub / fname
                    _write_json(
                        fpath,
                        {
                            "index": i,
                            "name": sec.name,
                            "body": sec.to_dict(),
                        },
                        indent,
                    )
                    files.append(
                        {"index": i, "name": sec.name, "file": fname}
                    )
                index_path = sub / "index.json"
                _write_json(
                    index_path,
                    {
                        "kind": "ini-split",
                        "label": label,
                        "stream": stream,
                        "n_sections": n_sections,
                        "sections": files,
                    },
                    indent,
                )
                entry.update(
                    {
                        "kind": "ini-split",
                        "dir": label,
                        "index_file": f"{label}/index.json",
                        "n_sections": n_sections,
                    }
                )
            else:
                target = out / f"{label}.json"
                size = _write_json(
                    target, _ini_stream_dict(parsed, label), indent
                )
                entry.update(
                    {
                        "kind": "ini",
                        "file": target.name,
                        "n_sections": n_sections,
                        "bytes": size,
                    }
                )
        else:
            entry.update({"kind": "unknown"})

        if include_raw:
            raw_dir = out / "raw"
            raw_dir.mkdir(exist_ok=True)
            raw_path = raw_dir / f"{stream}.bin"
            raw_path.write_bytes(qld.streams[stream])
            entry["raw_file"] = f"raw/{stream}.bin"

        manifest["streams"][stream] = entry

    _write_json(out / "manifest.json", manifest, indent or 2)
    return out
