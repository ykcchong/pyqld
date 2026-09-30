"""Tests for JSON-ready QLD serialization."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pyqld.qld as qld_module
from pyqld import QLDFile
from pyqld.ini import IniSection


def test_qd8_has_a_stable_conservative_label() -> None:
    assert QLDFile(path="test.qld").label_for("QD8") == "qd8"


def test_empty_ini_stream_is_not_mislabeled_as_raw() -> None:
    qld = QLDFile(
        path="empty.qld",
        streams={"QD7": b""},
        tables={"QD7": []},
    )

    stream = qld.to_dict(include_raw=True)["streams"]["QD7"]

    assert stream == {
        "label": "spectra",
        "kind": "ini",
        "sections": [],
        "raw_b64": base64.b64encode(b"").decode("ascii"),
    }


def test_directory_export_metadata_matches_written_files(
    tmp_path: Path, monkeypatch,
) -> None:
    first = IniSection("SampleInfo")
    first.add("Description", "café")
    second = IniSection("SampleInfo")
    qld = QLDFile(path="source.qld", tables={"QD6": [first, second]})
    monkeypatch.setattr(qld_module, "open_qld", lambda _: qld)

    flat_output = qld_module.export_dir("source.qld", tmp_path / "flat")
    flat_manifest_path = flat_output / "manifest.json"
    flat_manifest_text = flat_manifest_path.read_text(encoding="utf-8")
    flat_manifest = json.loads(flat_manifest_text)
    flat_stream = flat_manifest["streams"]["QD6"]

    assert "\n" not in flat_manifest_text
    assert flat_stream["bytes"] == (
        flat_output / flat_stream["file"]
    ).stat().st_size

    output = qld_module.export_dir(
        "source.qld",
        tmp_path / "export",
        split_sections=True,
        split_threshold=2,
    )
    manifest_path = output / "manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    stream = manifest["streams"]["QD6"]

    assert stream["kind"] == "ini-split"
    index_path = output / stream["index_file"]
    assert index_path.is_file()
