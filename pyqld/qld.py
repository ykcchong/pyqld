"""High-level ``.qld`` file reader and JSON exporter.

A ``.qld`` file is an OLE2 Compound Document containing named substreams
under ``QuanData/DataStream.QDx``.  Each substream is LZSS-compressed and
XOR-encrypted.  After decoding, the plaintext is either a dBase-III variant
table or an INI-style nested section document.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
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
    "QD8": "qd8",
    "qld": "audit",
}

_QD_STREAM_RE = re.compile(
    r"(?i)(?:^|[\\/])QuanData[\\/](?:.*[\\/])?DataStream\.(QD\d+|qld)$"
)


def _canonical_stream(name: str) -> str:
    return "qld" if name.lower() == "qld" else name.upper()


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
    _source: bytes | None = field(default=None, repr=False, compare=False)
    _modified: frozenset[str] = field(default_factory=frozenset, repr=False)

    def with_changes(self, table: str, index: int, **values: Any) -> QLDFile:
        """Return an edited copy of one QD1-QD4 row using field names."""
        return self.with_rows(table, {index: values})

    def with_rows(self, table: str, changes: dict[int, dict[str, Any]]) -> QLDFile:
        """Return an atomic edited copy of several rows in one table."""
        from .writer import edit_table_rows

        if table not in {"QD1", "QD2", "QD3", "QD4"}:
            raise ValueError("only existing QD1-QD4 table fields can be edited")
        if self._source is None:
            raise ValueError("editing requires a file loaded with open_qld() or read()")
        if not isinstance(self.tables.get(table), DBFTable):
            raise ValueError(f"{table} is not an available DBF table")
        payload = edit_table_rows(self.streams[table], changes)
        return replace(
            self,
            streams={**self.streams, table: payload},
            tables={**self.tables, table: parse_dbf(payload)},
            _modified=self._modified | {table},
        )

    def to_bytes(self) -> bytes:
        """Serialize an edited copy; preserve the original bytes for a no-op save."""
        import olefile

        from .writer import encode_quan_stream, replace_streams

        if self._source is None:
            raise ValueError("saving requires an original QLD container")
        if not self._modified:
            return self._source
        replacements = {}
        with olefile.OleFileIO(io.BytesIO(self._source)) as ole:
            for path in ole.listdir():
                name = "/".join(path)
                match = _QD_STREAM_RE.search(name)
                if match:
                    stream = _canonical_stream(match.group(1))
                    if stream in self._modified:
                        replacements[name] = encode_quan_stream(self.streams[stream])
        return replace_streams(self._source, replacements)

    def save(self, path: str | Path, *, overwrite: bool = False) -> Path:
        """Atomically save a QLD copy, refusing existing targets by default."""
        destination = Path(path)
        payload = self.to_bytes()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(temporary, destination)
            else:
                os.link(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination

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
            elif isinstance(parsed, list):
                entry["kind"] = "ini"
                entry["sections"] = [
                    {"name": s.name, "body": s.to_dict()} for s in parsed
                ]
            else:
                entry["kind"] = "raw"
            if include_raw or not isinstance(parsed, (DBFTable, list)):
                import base64

                entry["raw_b64"] = base64.b64encode(
                    self.streams[stream]
                ).decode("ascii")
            out["streams"][stream] = entry
        return out


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _iter_ole_streams(ole_path: Path | bytes) -> Iterable[tuple[str, bytes]]:
    """Yield ``(basename, raw_bytes)`` for each TargetLynx data sub-stream."""
    try:
        import olefile
    except ImportError as e:
        raise RuntimeError(
            "pyqld requires the 'olefile' package (pip install olefile)"
        ) from e

    source = io.BytesIO(ole_path) if isinstance(ole_path, bytes) else str(ole_path)
    with olefile.OleFileIO(source) as ole:
        seen: dict[str, str] = {}
        for entry in ole.listdir(streams=True, storages=False):
            joined = "/".join(entry)
            m = _QD_STREAM_RE.search(joined)
            if not m:
                continue
            basename = _canonical_stream(m.group(1))
            if basename in seen:
                raise ValueError(
                    f"duplicate QLD stream {basename}: {seen[basename]!r} and {joined!r}"
                )
            seen[basename] = joined
            with ole.openstream(entry) as fh:
                yield basename, fh.read()


def open_qld(path: str | Path, *, strict: bool = False) -> QLDFile:
    """Open a ``.qld`` file, decode every sub-stream, and parse it.

    Parameters
    ----------
    path : str | Path
        Path to the ``.qld`` file.
    strict : bool
        Reject malformed compression, table layouts, and INI structure, and
        wrong known-stream payload types. Does not certify scientific metadata.

    Returns
    -------
    QLDFile
        Decoded and parsed file.

    Parsing rules
    --------------
    * If the decoded payload starts with the dBase magic ``0x037E`` the
      stream is parsed by :func:`pyqld.dbf.parse_dbf`.
    * Otherwise it is parsed by :func:`pyqld.ini.parse_ini`.
    * Unrecognized nonempty payloads remain in ``streams`` with ``None`` in
      ``tables`` and are exported as raw bytes. Strict mode rejects these for
      known streams, but preserves future opaque streams.
    * Stream keys are canonicalized; duplicate identities and containers without
      QLD streams are rejected in both modes.
    """
    p = Path(path)
    source_bytes = p.read_bytes()
    qld = QLDFile(path=str(p.resolve()), _source=source_bytes)

    for name, raw in _iter_ole_streams(source_bytes):
        try:
            plaintext = decode_quan_stream(raw, strict=strict)
            qld.streams[name] = plaintext
            if strict and name in {"QD1", "QD2", "QD3", "QD4"} and not is_dbf(plaintext):
                raise ValueError("expected a dBase table")
            if is_dbf(plaintext):
                if strict and name in {"QD5", "QD6", "QD7", "QD8", "qld"}:
                    raise ValueError("expected INI sections, found a dBase table")
                qld.tables[name] = parse_dbf(plaintext, strict=strict)
            else:
                known = name in STREAM_LABELS
                sections = parse_ini(plaintext, strict=strict and known)
                qld.tables[name] = sections if sections or not plaintext else None
                if strict and not known and sections:
                    parse_ini(plaintext, strict=True)
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from error

    if not qld.streams:
        raise ValueError("container has no recognized QuanData/DataStream QLD streams")

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
    strict: bool = False,
) -> Path:
    """Read ``qld_path`` and write its JSON form to ``json_path``."""
    qld = open_qld(qld_path, strict=strict)
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
    return path.stat().st_size


def export_dir(
    qld_path: str | Path,
    out_dir: str | Path,
    *,
    indent: int | None = None,
    include_raw: bool = False,
    split_sections: bool = False,
    split_threshold: int = 50,
    strict: bool = False,
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
    qld = open_qld(qld_path, **({"strict": True} if strict else {}))
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
            if split_sections and n_sections >= split_threshold:
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
            entry.update({"kind": "raw"})

        if include_raw or not isinstance(parsed, (DBFTable, list)):
            raw_dir = out / "raw"
            raw_dir.mkdir(exist_ok=True)
            raw_path = raw_dir / f"{stream}.bin"
            raw_path.write_bytes(qld.streams[stream])
            entry["raw_file"] = f"raw/{stream}.bin"

        manifest["streams"][stream] = entry

    _write_json(out / "manifest.json", manifest, indent)
    return out
