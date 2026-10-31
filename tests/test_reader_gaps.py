"""Regression coverage for lossless recovery and opt-in strict QLD reading."""

import base64
import io
import json
import struct
from pathlib import Path

import numpy as np
import pytest

from pyqld import Dataset, QLDFile, Sample, codec, dbf, open_qld, read
from pyqld.cli import main
from pyqld.ini import IniBlob, parse_ini
from pyqld.writer import encode_quan_stream
from tests.test_codec import _block
from tests.test_dbf import _build_dbf


@pytest.mark.parametrize("eol", [b"\r", b"\n", b"\r\n"])
def test_ini_line_endings_always_make_progress(eol):
    payload = eol.join([b"[A]", b"{", b"key\tvalue", b"[B]", b"{", b"x\ty", b"}", b"}", b""])
    section = parse_ini(payload, strict=True)[0]
    assert section.to_dict() == {"key": "value", "B": {"x": "y"}}


def test_mixed_ini_line_endings_and_invalid_eol():
    assert parse_ini(b"\r[A]\r\n{\nK\tV\r}\r", strict=True)[0].to_dict() == {"K": "V"}
    for eol in (b"", b"x"):
        with pytest.raises(ValueError, match="eol"):
            parse_ini(b"\r", eol=eol)


def _blob(section, payload):
    return (
        f"[{section}]\n{{\n".encode() + struct.pack("<II", 1, len(payload))
        + payload + b"\n}\n"
    )


def test_unknown_binary_is_not_guessed_to_be_float32():
    payload = b"\xff" * 4
    blob = parse_ini(_blob("FutureVendorData", payload), strict=True)[0].items[0][1]
    assert isinstance(blob, IniBlob)
    assert blob.payload_kind == "bytes"
    assert base64.b64decode(blob.to_dict()["data"]) == payload


def test_misaligned_known_float_blob_remains_bytes_and_strictly_fails():
    payload = _blob("Times", b"abc")
    blob = parse_ini(payload)[0].items[0][1]
    assert blob.payload_kind == "bytes"
    assert blob.payload == b"abc"
    with pytest.raises(ValueError, match="misaligned"):
        parse_ini(payload, strict=True)


def test_section_header_cannot_consume_brackets_from_later_lines():
    payload = b"[broken\n[Later]\n{\nK\tV\n}\n"
    with pytest.raises(ValueError, match="invalid section"):
        parse_ini(payload, strict=True)
    assert parse_ini(payload) == []


def test_duplicate_suffixes_preserve_every_dbf_column():
    payload = _build_dbf(
        [("X", "C", 2, 0), ("X", "C", 2, 0), ("X_2", "C", 2, 0)],
        [["a", "b", "c"]],
    )
    table = dbf.parse_dbf(payload, strict=True)
    assert [f.name for f in table.fields] == ["X", "X", "X_2"]
    assert table.records == [{"X": "a", "X_3": "b", "X_2": "c"}]


def test_reserved_deletion_marker_cannot_be_overwritten_by_vendor_column():
    payload = bytearray(_build_dbf([("__deleted__", "C", 2, 0)], [["no"]]))
    payload[65] = ord("*")
    table = dbf.parse_dbf(bytes(payload), strict=True)
    assert table.records[0]["__deleted__"] is True
    assert table.records[0][table.fields[0].key] == "no"
    assert table.fields[0].key != "__deleted__"


def test_snake_case_collisions_preserve_all_record_values():
    row = Sample({"A-B": 1, "A_B": 2, "A_B_2": 3, "a_b": 4}, 0)
    assert len(row) == 4
    assert set(row.to_dict().values()) == {1, 2, 3, 4}
    assert {row[key] for key in row} == {1, 2, 3, 4}
    assert row["A-B"] == 1
    assert row["a_b"] == 4


@pytest.mark.parametrize("native", [False, True])
def test_unsupported_field_does_not_hide_later_columns(native, monkeypatch):
    if not native:
        monkeypatch.setattr(dbf, "_native_parse_records", None)
    payload = _build_dbf(
        [("A", "C", 2, 0), ("DATE", "D", 8, 0), ("B", "C", 2, 0)],
        [["a", "20261031", "b"]],
    )
    table = dbf.parse_dbf(payload)
    assert table.records == [{"A": "a", "DATE": "20261031", "B": "b"}]
    assert "unsupported field type" in table.issues[0]
    assert table.to_dict()["issues"]
    with pytest.raises(ValueError, match="unsupported"):
        dbf.parse_dbf(payload, strict=True)
    with pytest.raises(ValueError, match="unsupported"):
        table.fields[1].encode("20261101")
    dataset = Dataset(QLDFile("test", tables={"QD1": table}))
    assert "dbf_structure" in {i.code for i in dataset.validate().errors}


@pytest.mark.parametrize("size", [4, 8, 12, 31])
def test_short_dbf_header_has_an_explicit_error(size):
    with pytest.raises(ValueError, match="truncated dBase header"):
        dbf.parse_dbf(b"\x03\x7e\x04\x0b" + bytes(size - 4))


@pytest.mark.parametrize("defect", ["offset", "stride", "descriptor", "records", "terminator"])
def test_strict_dbf_rejects_recovered_structural_damage(defect):
    payload = bytearray(_build_dbf([("A", "C", 4, 0)], [["abcd"], ["efgh"]]))
    if defect == "offset":
        struct.pack_into("<H", payload, 8, 0)
    elif defect == "stride":
        struct.pack_into("<H", payload, 10, 4)
    elif defect == "descriptor":
        struct.pack_into("<H", payload, 8, 48)
    elif defect == "records":
        del payload[-4:]
    else:
        payload[64] = 0
    tolerant = dbf.parse_dbf(bytes(payload))
    assert tolerant.issues
    with pytest.raises(ValueError):
        dbf.parse_dbf(bytes(payload), strict=True)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("payload", [
    b"\x01", _block(b"\x04", b"A\x00\x02"), _block(b"\x04", b"A\x02\x03"),
    _block(b"\x04", b"A\x01"), _block(b"\x00", b"ABCDE"),
])
def test_strict_codec_corruption_parity(native, payload, monkeypatch):
    if not native:
        monkeypatch.setattr(codec, "_native_decode", None)
    codec.decode_quan_stream(payload)
    for decoder in (codec.lzss_decode, codec.decode_quan_stream):
        with pytest.raises(ValueError):
            decoder(payload, strict=True)


@pytest.fixture
def container(tmp_path, monkeypatch):
    import olefile

    path = tmp_path / "test.qld"
    path.write_bytes(b"synthetic container")
    entries = {}

    class Ole:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def listdir(self, **kwargs):
            return [list(key) for key in entries]

        def openstream(self, key):
            return io.BytesIO(entries[tuple(key)])

    monkeypatch.setattr(olefile, "OleFileIO", lambda _: Ole())
    return path, entries


def test_container_stream_names_are_canonical_and_duplicates_are_rejected(container):
    path, entries = container
    payload = _build_dbf([("ID", "C", 4, 0)], [["one"]])
    entries[("QuanData", "DataStream.qd1")] = encode_quan_stream(payload)
    entries[("QuanData", "DataStream.QLD")] = encode_quan_stream(b"")
    assert open_qld(path, strict=True).stream_names() == ["QD1", "qld"]
    assert read(path, strict=True).samples[0].id == "one"
    entries[("QuanData", "Nested", "DataStream.QD1")] = encode_quan_stream(payload)
    with pytest.raises(ValueError, match="duplicate QLD stream QD1"):
        open_qld(path)


def test_unrelated_ole_container_does_not_look_like_empty_dataset(container):
    path, entries = container
    entries[("OtherQuanData", "DataStream.QD1")] = b"ignored"
    with pytest.raises(ValueError, match="no recognized"):
        open_qld(path)


def test_strict_loading_reports_stream_context_and_cli_flag(container, capsys):
    path, entries = container
    entries[("QuanData", "DataStream.QD1")] = encode_quan_stream(
        _build_dbf([("ID", "C", 4, 0)], [["one"]]),
    ) + b"\x01"
    assert len(read(path).samples) == 1
    with pytest.raises(ValueError, match="QD1: truncated block"):
        read(path, strict=True)
    with pytest.raises(ValueError, match="QD1: truncated block"):
        main([str(path), "--strict", "--list"])
    assert not capsys.readouterr().out


def test_known_stream_type_mismatch_is_not_silently_accepted(container):
    path, entries = container
    entries[("QuanData", "DataStream.QD1")] = encode_quan_stream(b"[Wrong]\n{\n}\n")
    with pytest.raises(ValueError, match="QD1: expected a dBase"):
        read(path, strict=True)
    assert "stream_type" in {i.code for i in read(path).validate().errors}


def test_unknown_opaque_stream_is_preserved_in_all_exports(container, tmp_path):
    from pyqld import export_dir, to_json

    path, entries = container
    raw = b"\xff\x01\x00\x80"
    entries[("QuanData", "DataStream.QD9")] = encode_quan_stream(raw)
    qld = open_qld(path, strict=True)
    assert qld.tables["QD9"] is None
    entry = json.loads(to_json(qld))["streams"]["QD9"]
    assert entry["kind"] == "raw"
    assert base64.b64decode(entry["raw_b64"]) == raw
    out = export_dir(path, tmp_path / "out", strict=True)
    manifest = json.loads((out / "manifest.json").read_text())
    assert (out / manifest["streams"]["QD9"]["raw_file"]).read_bytes() == raw
    assert "unknown_stream" in {i.code for i in Dataset(qld).validate().warnings}


@pytest.mark.parametrize("fixture_index", [0, 1])
def test_both_real_fixtures_read_strictly_without_changing_content(fixture_index):
    fixtures = sorted((Path(__file__).parents[1] / "data").glob("*.qld"))
    if len(fixtures) < 2:
        pytest.skip("local fixtures unavailable")
    tolerant = read(fixtures[fixture_index])
    strict = read(fixtures[fixture_index], strict=True)
    assert strict.qld.to_bytes() == fixtures[fixture_index].read_bytes()
    assert strict.qld.to_dict() == tolerant.qld.to_dict()
    assert strict.validate().is_valid
    for before, after in zip(tolerant.chromatograms, strict.chromatograms, strict=True):
        for expected, actual in zip(before.to_numpy(), after.to_numpy(), strict=True):
            np.testing.assert_array_equal(expected, actual)
