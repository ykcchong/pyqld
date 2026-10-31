"""dBase-III variant parser for ``QD1``–``QD4`` substreams.

Layout (after decoding by :mod:`pyqld.codec`)::

    file header (32 bytes):
        u32  magic = 0x0B047E03
        u32  nrecs
        u16  header length (record-data offset)
        u16  record size (including deletion flag)
        20   reserved zeros

    field records (32 bytes each, repeated):
        char[11]   name        (null-padded ASCII)
        char       type        ('C' char, 'N' numeric, 'L' logical)
        u8[4]      reserved
        u8         width
        u8         decimals    (for type 'N')
        u8[14]     padding

    terminator:
        u8 = 0x0D                 (classic dBase-III header sentinel)

    records (nrecs × (1 + sum(widths)) bytes):
        u8         deletion flag (0x00 = live, 0x2A = '*' = deleted)
        ASCII field values, space- or zero-padded to each field's width

    trailer:
        u8[2]      file-end marker (typically 0x1A 0x00, ignored)

Numeric fields are stored as right-aligned ASCII digits.
Logical fields hold ``'T'``/``'F'`` (or ``'1'``/``'0'``).
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

_native_parse_records: Callable[
    [bytes, int, int, int, list[tuple[str, int, int, str, int]]],
    list[dict[str, Any]],
] | None
try:
    from ._dbf_native import parse_records as _native_parse_records
except ImportError:
    _native_parse_records = None

#: Two-byte magic prefix that distinguishes dBase from INI substreams.
MAGIC_PREFIX: bytes = b"\x03\x7e"

_FIELD_TYPES: frozenset[str] = frozenset("CNL")
_HEADER_TERMINATOR: int = 0x0D


def is_dbf(data: bytes) -> bool:
    """Return ``True`` if ``data`` looks like a TargetLynx dBase table.

    Checks the first two bytes against :data:`MAGIC_PREFIX`.
    """
    return len(data) >= 4 and data[:2] == MAGIC_PREFIX


@dataclass(frozen=True)
class DBFField:
    """Metadata for one column in a dBase table."""

    name: str
    type: str  # 'C', 'N', or 'L'
    width: int
    decimals: int
    occurrence: int = 1
    record_key: str | None = None

    @property
    def key(self) -> str:
        """Unique record key, with a suffix for repeated vendor names."""
        if self.record_key is not None:
            return self.record_key
        return self.name if self.occurrence == 1 else f"{self.name}_{self.occurrence}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "key": self.key,
            "type": self.type,
            "width": self.width,
            "decimals": self.decimals,
        }

    def encode(self, value: Any) -> bytes:
        """Encode an edited value without truncation or numeric rounding."""
        if self.type not in _FIELD_TYPES:
            raise ValueError(f"{self.key}: editing unsupported field type {self.type!r}")
        if self.type == "L":
            if not isinstance(value, bool):
                raise ValueError(f"{self.key} requires a boolean")
            text = "T" if value else "F"
        elif self.type == "N":
            if value is None or value == "":
                text = ""
            else:
                try:
                    number = Decimal(str(value))
                except InvalidOperation as error:
                    raise ValueError(f"{self.key} requires a number") from error
                if not number.is_finite():
                    raise ValueError(f"{self.key} requires a finite number")
                text = format(number, "f")
        else:
            if not isinstance(value, str):
                raise ValueError(f"{self.key} requires text")
            if any(ord(character) < 32 or ord(character) > 126 for character in value):
                raise ValueError(f"{self.key} requires printable ASCII text")
            text = value
        encoded = text.encode("ascii")
        if len(encoded) > self.width:
            raise ValueError(f"{self.key} exceeds its {self.width}-byte field width")
        return encoded.rjust(self.width) if self.type == "N" else encoded.ljust(self.width)


@dataclass
class DBFTable:
    """A parsed dBase table with fields and records."""

    nrecs: int
    fields: list[DBFField]
    records: list[dict[str, Any]] = field(default_factory=list)
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "kind": "dbf",
            "nrecs": self.nrecs,
            "fields": [f.to_dict() for f in self.fields],
            "records": self.records,
        }
        if self.issues:
            result["issues"] = list(self.issues)
        return result


# ---------------------------------------------------------------------------
# Value decoding
# ---------------------------------------------------------------------------


def _decode_value(raw: bytes, ftype: str, decimals: int) -> Any:
    """Decode a single field value from raw bytes."""
    return _decode_text_value(raw.decode("ascii", "replace"), ftype, decimals)


def _decode_text_value(text: str, ftype: str, decimals: int) -> Any:
    """Decode a field from an already decoded record."""
    s = text.strip(" \x00")

    if ftype == "N":
        if not s:
            return None
        try:
            return float(s) if decimals else int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return s

    if ftype == "L":
        return s.upper() in ("T", "Y", "1")

    return s


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_dbf(data: bytes, *, strict: bool = False) -> DBFTable:
    """Parse a TargetLynx dBase-III variant table from ``data``.

    Parameters
    ----------
    data : bytes
        The decoded (plaintext) stream bytes.
    strict : bool
        Reject structural issues or unsupported field types instead of recovering
        records and recording diagnostics in ``DBFTable.issues``.

    Returns
    -------
    DBFTable
        Parsed table with fields and records.

    Raises
    ------
    ValueError
        For invalid/short headers, or reported structural issues in strict mode.
    """
    if not is_dbf(data):
        raise ValueError(f"not a dBase table: magic={data[:4].hex()}")
    if len(data) < 32:
        raise ValueError("truncated dBase header: expected at least 32 bytes")

    nrecs = struct.unpack_from("<I", data, 4)[0]

    # Use authoritative header values for record offset and size.
    header_len = struct.unpack_from("<H", data, 8)[0]
    rec_size_hdr = struct.unpack_from("<H", data, 10)[0]

    # Parse field descriptors
    fields: list[DBFField] = []
    issues: list[str] = []
    field_occurrences: dict[str, int] = {}
    off = 32
    n = len(data)

    limit = header_len if 32 < header_len <= n else n
    while off < limit:
        if data[off] in (0x00, _HEADER_TERMINATOR):
            break
        if off + 32 > limit:
            issues.append(f"truncated field descriptor at byte {off}")
            break
        chunk = data[off : off + 32]
        ftype = chr(chunk[11])
        if ftype not in _FIELD_TYPES:
            issues.append(f"unsupported field type {ftype!r} at byte {off}; retained as text")
        name = chunk[:11].split(b"\x00", 1)[0].decode("ascii", "replace")
        occurrence = field_occurrences.get(name, 0) + 1
        field_occurrences[name] = occurrence
        width = chunk[16]
        decimals = chunk[17]
        fields.append(
            DBFField(
                name=name,
                type=ftype,
                width=width,
                decimals=decimals,
                occurrence=occurrence,
            )
        )
        off += 32

    if off >= n or data[off] != _HEADER_TERMINATOR:
        issues.append(f"missing dBase header terminator at byte {off}")

    # Reserve literal vendor names so generated duplicate suffixes cannot hide them.
    reserved = {f.name for f in fields} | {"__deleted__"}
    allocated = {"__deleted__"}
    resolved_fields = []
    for descriptor in fields:
        key = descriptor.key
        suffix = descriptor.occurrence
        while key in allocated or (key != descriptor.name and key in reserved):
            suffix += 1
            key = f"{descriptor.name}_{suffix}"
        allocated.add(key)
        resolved_fields.append(DBFField(
            descriptor.name, descriptor.type, descriptor.width, descriptor.decimals,
            descriptor.occurrence, key if key != descriptor.key else None,
        ))
    fields = resolved_fields

    sum_widths = sum(f.width for f in fields)

    # Determine record offset
    if off < header_len <= n:
        rec_off = header_len
    else:
        issues.append(f"invalid record-data offset {header_len}")
        if off < n and data[off] == _HEADER_TERMINATOR:
            off += 1
        rec_off = off

    minimum_size = 1 + sum_widths
    if rec_size_hdr < minimum_size:
        issues.append(f"record size {rec_size_hdr} is smaller than required {minimum_size}")
    rec_size = max(rec_size_hdr, minimum_size)
    count = min(nrecs, max(0, (n - rec_off) // rec_size))
    if count != nrecs:
        issues.append(f"truncated records: declared {nrecs}, complete {count}")
    if strict and issues:
        raise ValueError("; ".join(issues))

    # Resolve duplicate-name keys and field boundaries once per table.
    layout = []
    pos = 1
    for f in fields:
        layout.append((f.key, pos, pos + f.width, f.type, f.decimals))
        pos += f.width

    if (_native_parse_records is not None and isinstance(data, bytes)
            and all(f.type in _FIELD_TYPES for f in fields)):
        native_records = _native_parse_records(data, rec_off, rec_size, count, layout)
        return DBFTable(nrecs, fields, native_records, tuple(issues))

    # Parse records
    records: list[dict[str, Any]] = []
    for i in range(count):
        start = rec_off + i * rec_size
        end = start + rec_size
        if end > n:
            break
        # ASCII replacement maps each byte to one character, preserving field
        # offsets even for corrupt/non-ASCII bytes. Avoid decoding every cell.
        rec = data[start:end].decode("ascii", "replace")
        deletion_flag = rec[0]

        out: dict[str, Any] = {}
        if deletion_flag == "*":
            out["__deleted__"] = True

        for key, field_start, field_end, ftype, decimals in layout:
            out[key] = _decode_text_value(rec[field_start:field_end], ftype, decimals)
        records.append(out)

    return DBFTable(nrecs, fields, records, tuple(issues))
