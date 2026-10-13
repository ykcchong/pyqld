"""Tests for the dBase parser."""

from __future__ import annotations

import struct

import pytest

from pyqld.dbf import is_dbf, parse_dbf


def _build_dbf(
    fields: list[tuple[str, str, int, int]],
    records: list[list],
) -> bytes:
    """Build a minimal dBase file for testing.

    fields: list of (name, type, width, decimals)
    records: list of field values per record
    """
    # Calculate sizes first
    field_desc_size = 32 * len(fields)
    header_len = 32 + field_desc_size + 1  # +1 for terminator (0x0D)
    rec_size = 1 + sum(w for _, _, w, _ in fields)  # +1 for deletion flag

    # Header (32 bytes) — real TargetLynx dBase layout:
    # offset 0-1: magic (0x037E)
    # offset 2-3: format version
    # offset 4-7: nrecs (u32 LE)
    # offset 8-9: header_len (u16 LE) — byte offset to record data
    # offset 10-11: rec_size (u16 LE) — bytes per record
    # offset 12-31: reserved zeros
    buf = bytearray()
    buf += b"\x03\x7e\x04\x0b"  # magic + version
    buf += struct.pack("<I", len(records))  # nrecs
    buf += struct.pack("<H", header_len)  # header length (offset 8)
    buf += struct.pack("<H", rec_size)  # record size (offset 10)
    buf += b"\x00" * 20  # reserved zeros (offset 12-31)

    # Field descriptors
    for name, ftype, width, decimals in fields:
        fd = bytearray(32)
        fd[: len(name.encode())] = name.encode("ascii")
        fd[11] = ord(ftype)
        fd[16] = width
        fd[17] = decimals
        buf += fd

    # Terminator
    buf += b"\r"  # 0x0D

    # Records
    for rec in records:
        buf += b"\x00"  # deletion flag
        for i, (_name, ftype, width, _decimals) in enumerate(fields):
            val = str(rec[i]) if rec[i] is not None else ""
            if ftype == "N":
                val = val.rjust(width)
            else:
                val = val.ljust(width)
            buf += val.encode("ascii")[:width]

    # Trailer
    buf += b"\x1a\x00"
    return bytes(buf)


class TestIsDbf:
    def test_valid_magic(self) -> None:
        assert is_dbf(b"\x03\x7e\x04\x0b" + b"\x00" * 100)

    def test_ini_not_dbf(self) -> None:
        assert not is_dbf(b"[Section]\n")

    def test_short_data(self) -> None:
        assert not is_dbf(b"\x03\x7e")


class TestParseDbf:
    def test_simple_table(self) -> None:
        data = _build_dbf(
            fields=[("NAME", "C", 10, 0), ("VALUE", "N", 8, 2)],
            records=[("alpha", "123.45"), ("beta", "678.90")],
        )
        table = parse_dbf(data)
        assert table.nrecs == 2
        assert len(table.fields) == 2
        assert len(table.records) == 2
        assert table.records[0]["NAME"] == "alpha"
        assert table.records[0]["VALUE"] == 123.45
        assert table.records[1]["NAME"] == "beta"

    def test_logical_field(self) -> None:
        data = _build_dbf(
            fields=[("FLAG", "L", 1, 0)],
            records=[["T"], ["F"]],
        )
        table = parse_dbf(data)
        assert table.records[0]["FLAG"] is True
        assert table.records[1]["FLAG"] is False

    def test_empty_numeric(self) -> None:
        data = _build_dbf(
            fields=[("NUM", "N", 4, 0)],
            records=[[""], ["42"]],
        )
        table = parse_dbf(data)
        assert table.records[0]["NUM"] is None
        assert table.records[1]["NUM"] == 42

    def test_deleted_record(self) -> None:
        # Manually set deletion flag to 0x2A
        data = _build_dbf(
            fields=[("X", "N", 2, 0)],
            records=[["1"]],
        )
        # Find and replace first record deletion flag
        data = bytearray(data)
        # Header is 32 + 32 + 1 = 65 bytes
        data[65] = 0x2A
        table = parse_dbf(bytes(data))
        assert table.records[0].get("__deleted__") is True

    def test_invalid_magic(self) -> None:
        with pytest.raises(ValueError, match="not a dBase"):
            parse_dbf(b"[Section]\n")

    def test_to_dict(self) -> None:
        data = _build_dbf(
            fields=[("A", "C", 5, 0)],
            records=[["hello"]],
        )
        table = parse_dbf(data)
        d = table.to_dict()
        assert d["kind"] == "dbf"
        assert d["nrecs"] == 1
        assert len(d["fields"]) == 1
        assert d["fields"][0]["name"] == "A"

    def test_duplicate_field_names_are_preserved(self) -> None:
        data = _build_dbf(
            fields=[("VALUE", "N", 3, 0), ("VALUE", "N", 3, 0)],
            records=[["1", "2"]],
        )

        table = parse_dbf(data)

        assert [field.key for field in table.fields] == ["VALUE", "VALUE_2"]
        assert table.records[0] == {"VALUE": 1, "VALUE_2": 2}


def test_record_decoding_preserves_byte_offsets_and_numeric_fallbacks() -> None:
    data = bytearray(_build_dbf(
        fields=[("TEXT", "C", 4, 0), ("NUM", "N", 5, 0),
                ("NUM", "N", 5, 2), ("FLAG", "L", 2, 0)],
        records=[["abcd", "1.5", "bad", "y"], ["x", "", "2.25", "F"]],
    ))
    header_len = struct.unpack_from("<H", data, 8)[0]
    data[header_len + 1:header_len + 5] = b"\x00\xffA "
    table = parse_dbf(bytes(data))
    assert table.records == [
        {"TEXT": "\ufffdA", "NUM": 1.5, "NUM_2": "bad", "FLAG": True},
        {"TEXT": "x", "NUM": None, "NUM_2": 2.25, "FLAG": False},
    ]


def test_incomplete_record_is_not_decoded() -> None:
    data = _build_dbf([("TEXT", "C", 4, 0)], [["one"], ["two"]])
    table = parse_dbf(data[:-4])  # Remove trailer and part of second record.
    assert table.nrecs == 2
    assert table.records == [{"TEXT": "one"}]


class TestToDict:
    def test_encode_edits(self) -> None:
        from pyqld.dbf import DBFField

        assert DBFField("TEXT", "C", 6, 0).encode("edit") == b"edit  "
        assert DBFField("AREA", "N", 8, 2).encode(12.345) == b"  12.345"
        assert DBFField("AREA", "N", 8, 2).encode(None) == b"        "
        assert DBFField("FLAG", "L", 1, 0).encode(False) == b"F"

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad", True])
    def test_reject_invalid_number(self, value) -> None:
        from pyqld.dbf import DBFField

        with pytest.raises(ValueError):
            DBFField("AREA", "N", 8, 2).encode(value)

    def test_reject_truncation(self) -> None:
        from pyqld.dbf import DBFField

        with pytest.raises(ValueError, match="width"):
            DBFField("TEXT", "C", 3, 0).encode("long")

    def test_field_to_dict(self) -> None:
        from pyqld.dbf import DBFField

        f = DBFField(name="TEST", type="C", width=10, decimals=0)
        d = f.to_dict()
        assert d == {
            "name": "TEST",
            "key": "TEST",
            "type": "C",
            "width": 10,
            "decimals": 0,
        }


def test_native_dbf_matches_python_for_random_cells_and_truncation(monkeypatch) -> None:
    import math
    import random

    from pyqld import dbf

    native = dbf._native_parse_records
    if native is None:
        pytest.skip('native DBF extension is not built')
    rng = random.Random(42)
    fields = [('VALUE', 'C', 24, 0), ('VALUE', 'N', 24, 0),
              ('FLOAT', 'N', 24, 3), ('FLAG', 'L', 24, 0), ('EMPTY', 'C', 0, 0)]
    cells = [b'', b'\x00 ', b'\xff\x80', b'1.5', b'1_000', b'-42', b'1e99',
             b'nan', b'-inf', b'999999999999999999999999', b'\t12\r', b'bad',
             b'T', b'y', b'1', b'F', b'\x00hello\x00']
    data = bytearray(_build_dbf(fields, [[''] * len(fields) for _ in range(60)]))
    offset, stride = struct.unpack_from('<HH', data, 8)
    for index in range(60):
        start = offset + index * stride
        if index % 3 == 0:
            data[start] = ord('*')
        for col in range(4):
            raw = rng.choice(cells) if index < 40 else rng.randbytes(24)
            data[start + 1 + col * 24:start + 1 + (col + 1) * 24] = raw.ljust(24)
    variants = [bytes(data), bytes(data[:-10])]
    # Header record width equal to field widths clips the last cell; preserve it.
    clipped = bytearray(data)
    struct.pack_into('<H', clipped, 10, stride - 1)
    variants.append(bytes(clipped))
    for payload in variants:
        monkeypatch.setattr(dbf, '_native_parse_records', None)
        expected = dbf.parse_dbf(payload)
        monkeypatch.setattr(dbf, '_native_parse_records', native)
        actual = dbf.parse_dbf(payload)
        assert actual.fields == expected.fields
        assert actual.nrecs == expected.nrecs
        assert len(actual.records) == len(expected.records)
        for got, want in zip(actual.records, expected.records, strict=True):
            assert list(got) == list(want)
            for key in got:
                assert type(got[key]) is type(want[key])
                if isinstance(want[key], float) and math.isnan(want[key]):
                    assert math.isnan(got[key])
                else:
                    assert got[key] == want[key]


def test_native_dbf_rejects_invalid_bounds() -> None:
    from pyqld import dbf

    native = dbf._native_parse_records
    if native is None:
        pytest.skip('native DBF extension is not built')
    for offset, stride, count in [(-1, 1, 1), (2, 1, 1), (0, 0, 1),
                                  (0, 1, -1), (0, 2, 1)]:
        with pytest.raises(ValueError):
            native(b'X', offset, stride, count, [])
    for layout in [[('X', -1, 2, 'C', 0)], [('X', 1, 0, 'C', 0)],
                   [('X', 0, 1, 'bad', 0)], [('X', 0, 1, 'Z', 0)]]:
        with pytest.raises(ValueError):
            native(b'X', 0, 1, 1, layout)
    assert native(b'', 0, 1, 0, []) == []
