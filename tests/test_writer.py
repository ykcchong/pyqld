"""Roundtrip and preservation checks for QLD writing."""

from __future__ import annotations

import io
from pathlib import Path

import olefile
import pytest

from pyqld import read
from pyqld.codec import decode_quan_stream
from pyqld.dbf import parse_dbf
from pyqld.writer import edit_table, encode_quan_stream, replace_streams

from .test_dbf import _build_dbf


@pytest.mark.parametrize("data", [b"", b"a", bytes(range(256)) * 300])
def test_codec_roundtrip(data: bytes) -> None:
    assert decode_quan_stream(encode_quan_stream(data)) == data


def test_table_patch_preserves_other_bytes() -> None:
    data = _build_dbf([("ID", "C", 5, 0), ("AREA", "N", 8, 2)], [["one", 12.5]])
    edited = edit_table(data, 0, {"area": 15.75})
    assert parse_dbf(edited).records[0]["AREA"] == 15.75
    assert edited[:103] == data[:103]
    assert edited[-2:] == data[-2:]
    with pytest.raises(ValueError, match="protected"):
        edit_table(data, 0, {"id": "two"})


def test_container_preserves_all_streams_and_metadata() -> None:
    fixture = Path(__file__).parents[2] / "data" / "UMP 20260508 PS.qld"
    if not fixture.exists():
        pytest.skip("repository fixture unavailable")
    source = fixture.read_bytes()
    for payload in (b"small", b"large" * 2000):
        rebuilt = replace_streams(source, {"QuanData/DataStream.QD1": payload})
        with olefile.OleFileIO(io.BytesIO(source)) as before, \
                olefile.OleFileIO(io.BytesIO(rebuilt)) as after:
            assert before.listdir() == after.listdir()
            assert before.root.clsid == after.root.clsid
            for path in before.listdir():
                expected = (
                    payload if path[-1] == "DataStream.QD1" else before.openstream(path).read()
                )
                assert after.openstream(path).read() == expected


def test_public_edit_save_reopen(tmp_path: Path) -> None:
    fixture = Path(__file__).parents[2] / "data" / "UMP 20260508 PS.qld"
    if not fixture.exists():
        pytest.skip("repository fixture unavailable")
    original = read(fixture)
    assert original.qld.to_bytes() == fixture.read_bytes()
    edited = original.with_changes(original.samples[0], file_text="Edited sample")
    assert original.samples[0].description != "Edited sample"
    assert edited.samples[0].description == "Edited sample"
    destination = edited.save(tmp_path / "edited.qld")
    reopened = read(destination)
    assert reopened.samples[0].description == "Edited sample"
    assert reopened.qld.streams["QD7"] == original.qld.streams["QD7"]
    with pytest.raises(FileExistsError):
        edited.save(destination)
    with pytest.raises(ValueError, match="different dataset"):
        edited.with_changes(original.samples[0], file_text="invalid")
    with pytest.raises(ValueError, match="width"):
        original.with_changes(original.samples[0], file_text="x" * 10000)

