"""Tests for the public package API."""

from __future__ import annotations

from pathlib import Path

import pytest

import pyqld
from pyqld import Dataset


def test_read_is_public() -> None:
    assert "read" in pyqld.__all__
    assert callable(pyqld.read)


def test_read_real_fixture() -> None:
    pytest.importorskip("olefile")
    fixture = Path(__file__).parents[2] / "data" / "UMP 20260508 PS.qld"
    if not fixture.is_file():
        pytest.skip("repository fixture is not available")

    dataset = pyqld.read(fixture)

    assert isinstance(dataset, Dataset)
    assert len(dataset.samples) == 13
    assert len(dataset.compounds) == 146
    assert len(dataset.peaks) == 7446
    assert dataset.results is dataset.peaks
    assert len(dataset.chromatograms) == 1898
    assert len(dataset.spectra) == 1333
    assert len(dataset.acquisitions) == 13
    assert len(dataset.unassigned_results) == 5522
    assert len(dataset.method.audit_events) == 523
    assert len(dataset.spectra_for(dataset.samples[0])) == 112
    assert len(dataset.spectra_for(dataset.samples[1])) == 18
    assert dataset.acquisitions[0].sample_id == "UMP B"
    assert dataset.method.audit_events[0].timestamp is not None
    assert set(dataset.qld.stream_names()) == {
        "QD1",
        "QD2",
        "QD3",
        "QD4",
        "QD5",
        "QD6",
        "QD7",
        "QD8",
        "qld",
    }
