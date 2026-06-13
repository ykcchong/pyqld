"""TargetLynx INI-style section parser for ``QD5``, ``QD6``, ``QD7``, and ``qld``.

Grammar
-------

.. code-block::text

    file        := section*
    section     := "[" name "]" LE "{" LE  body  "}" LE
    body        := (kv | section | blob)*
    kv          := key "\\t" value LE
    blob        := <u32 marker = 1> <u32 size> <size bytes payload> LE

A *blob* contains either:

* a packed ``float32`` array (sections ``Times``, ``Masses``, ``Intensities``,
  ``Points``, …), or
* an ASCII text blob (e.g. the ``Experiment`` and ``Header`` sections in
  the QD6 audit stream).

The parser auto-detects the payload type.
"""

from __future__ import annotations

import base64
import struct
from dataclasses import dataclass, field
from typing import Any

#: Section names whose blob payload is always a packed ``float32`` array.
FLOAT_BLOB_SECTIONS: frozenset[str] = frozenset(
    {"Times", "Masses", "Intensities", "Points", "RT", "Heights", "Areas"}
)

#: Section names whose blob payload is always ASCII text.
TEXT_BLOB_SECTIONS: frozenset[str] = frozenset({"Experiment", "Header"})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class IniBlob:
    """A raw binary blob found inside an INI section."""

    size: int
    payload: Any  # str | list[float] | bytes
    payload_kind: str  # 'text' | 'float32' | 'bytes'

    def to_dict(self) -> dict[str, Any]:
        if self.payload_kind == "bytes":
            return {
                "kind": "blob",
                "size": self.size,
                "encoding": "base64",
                "data": base64.b64encode(self.payload).decode("ascii"),
            }
        return {
            "kind": "blob",
            "size": self.size,
            "encoding": self.payload_kind,
            "data": self.payload,
        }


@dataclass
class IniSection:
    """A parsed INI section.

    ``items`` preserves document order as a list of ``(key, value)`` tuples.
    ``value`` may be a string scalar, a nested :class:`IniSection`, or an
    :class:`IniBlob`.
    """

    name: str
    items: list[tuple[str, Any]] = field(default_factory=list)

    def add(self, key: str, value: Any) -> None:
        """Append a ``(key, value)`` pair."""
        self.items.append((key, value))

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dictionary (duplicates promoted to lists)."""
        out: dict[str, Any] = {}
        for k, v in self.items:
            if isinstance(v, IniSection):
                child = v.to_dict()
            elif isinstance(v, IniBlob):
                child = v.to_dict()
            else:
                child = v
            if k in out:
                existing = out[k]
                if not isinstance(existing, list):
                    out[k] = [existing]
                out[k].append(child)
            else:
                out[k] = child
        return out


# ---------------------------------------------------------------------------
# Internal byte-cursor parser
# ---------------------------------------------------------------------------


class _Cursor:
    """Position-aware byte cursor with EOL handling."""

    __slots__ = ("data", "pos", "eol", "eol_len")

    def __init__(self, data: bytes, eol: bytes) -> None:
        self.data = data
        self.pos = 0
        self.eol = eol
        self.eol_len = len(eol)

    def at_end(self) -> bool:
        return self.pos >= len(self.data)

    def peek(self, n: int = 1) -> bytes:
        return self.data[self.pos : self.pos + n]

    def read_eol(self) -> bool:
        if self.data[self.pos : self.pos + self.eol_len] == self.eol:
            self.pos += self.eol_len
            return True
        if self.data[self.pos : self.pos + 1] == b"\n":
            self.pos += 1
            return True
        return False

    def read_line(self) -> bytes:
        """Read up to but not including the next EOL; consume the EOL."""
        idx = self.data.find(b"\n", self.pos)
        if idx < 0:
            line = self.data[self.pos :]
            self.pos = len(self.data)
            return line
        line = self.data[self.pos : idx]
        if line.endswith(b"\r"):
            line = line[:-1]
        self.pos = idx + 1
        return line


# ---------------------------------------------------------------------------
# Blob payload conversion
# ---------------------------------------------------------------------------


def _payload_to_value(name: str, payload: bytes) -> tuple[Any, str]:
    """Convert a blob payload to ``(value, kind)`` based on section name."""
    if name in FLOAT_BLOB_SECTIONS and len(payload) % 4 == 0:
        floats = list(struct.unpack(f"<{len(payload) // 4}f", payload))
        return floats, "float32"

    if name in TEXT_BLOB_SECTIONS:
        try:
            return payload.decode("ascii"), "text"
        except UnicodeDecodeError:
            return payload.decode("latin-1"), "text"

    # Auto-detect
    try:
        text = payload.decode("ascii")
        if all(
            c == "\t" or c == "\r" or c == "\n" or 0x20 <= ord(c) < 0x7F
            for c in text
        ):
            return text, "text"
    except UnicodeDecodeError:
        pass

    if len(payload) % 4 == 0:
        return list(struct.unpack(f"<{len(payload) // 4}f", payload)), "float32"

    return payload, "bytes"


# ---------------------------------------------------------------------------
# Section body parser
# ---------------------------------------------------------------------------


def _parse_section_body(cur: _Cursor, section: IniSection) -> None:
    """Parse the body of one section up to (and consuming) its closing ``}``."""
    # Detect blob: marker u32 = 1, first byte non-printable
    if cur.pos + 8 <= len(cur.data):
        marker, size = struct.unpack_from("<II", cur.data, cur.pos)
        first_byte = cur.data[cur.pos]
        if (
            marker == 1
            and first_byte < 0x20
            and first_byte not in (0x09, 0x0A, 0x0D)
        ):
            cur.pos += 8
            payload = cur.data[cur.pos : cur.pos + size]
            cur.pos += size
            cur.read_eol()
            value, kind = _payload_to_value(section.name, payload)
            section.add(
                "__blob__",
                IniBlob(size=size, payload=value, payload_kind=kind),
            )
            _consume_close(cur)
            return

    while not cur.at_end():
        b = cur.peek(1)
        if b == b"}":
            cur.pos += 1
            cur.read_eol()
            return
        if b in (b"\r", b"\n"):
            cur.read_eol()
            continue
        if b == b"[":
            child = _parse_section(cur)
            if child is not None:
                section.add(child.name, child)
            continue
        line = cur.read_line()
        if not line:
            continue
        if b"\t" in line:
            key, _, value = line.partition(b"\t")
            section.add(key.decode("latin-1"), value.decode("latin-1"))
        else:
            section.add(line.decode("latin-1"), "")


def _consume_close(cur: _Cursor) -> None:
    """Consume a closing ``}`` line."""
    while not cur.at_end():
        b = cur.peek(1)
        if b == b"}":
            cur.pos += 1
            cur.read_eol()
            return
        if b in (b"\r", b"\n"):
            cur.read_eol()
            continue
        return


def _parse_section(cur: _Cursor) -> IniSection | None:
    """Parse ``[Name]<eol>{<eol> ... }<eol>`` starting at ``cur.pos``."""
    if cur.peek(1) != b"[":
        return None
    end_bracket = cur.data.find(b"]", cur.pos)
    if end_bracket < 0:
        return None
    name = cur.data[cur.pos + 1 : end_bracket].decode("latin-1")
    cur.pos = end_bracket + 1
    cur.read_eol()
    if cur.peek(1) != b"{":
        return None
    cur.pos += 1
    cur.read_eol()
    section = IniSection(name=name)
    _parse_section_body(cur, section)
    return section


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_ini(data: bytes, eol: bytes | None = None) -> list[IniSection]:
    """Parse an INI-style TargetLynx stream and return the top-level sections.

    Parameters
    ----------
    data : bytes
        The decoded (plaintext) stream bytes.
    eol : bytes, optional
        Line ending to use. Defaults to ``b"\\r\\n"`` if CRLF is found in
        the first kilobyte, otherwise ``b"\\n"``.

    Returns
    -------
    list[IniSection]
        Top-level sections in document order.
    """
    if eol is None:
        head = data[:1024]
        eol = b"\r\n" if b"\r\n" in head else b"\n"

    cur = _Cursor(data, eol)
    sections: list[IniSection] = []

    while not cur.at_end():
        b = cur.peek(1)
        if b in (b"\r", b"\n"):
            cur.read_eol()
            continue
        if b != b"[":
            break
        sec = _parse_section(cur)
        if sec is None:
            break
        sections.append(sec)

    return sections
