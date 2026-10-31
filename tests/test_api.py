"""Tests for the public package API."""

from __future__ import annotations

from pathlib import Path

import pytest

import pyqld
from pyqld import Dataset, MRMTransition


def test_read_is_public() -> None:
    assert "read" in pyqld.__all__
    assert callable(pyqld.read)


def test_read_chromatograms_with_inconsistent_transition_spacing() -> None:
    fixture = Path(__file__).parents[1] / "data" / "UOS 20261013 BEH Amide PS.qld"
    if not fixture.is_file():
        pytest.skip("repository fixture is not available")

    dataset = pyqld.read(fixture)

    assert len(dataset.samples) == 21
    assert len(dataset.compounds) == 18
    assert len(dataset.chromatograms) == 567
    for acquisition in dataset.acquisitions:
        assert len(acquisition.functions) == acquisition.number_of_functions == 18
        assert len(acquisition.mrm_channels) == 27
        function = acquisition.function(8)
        assert float(function.start_time) == 9.2
        assert float(function.end_time) == 11.7
        channel = function.channel(2)
        assert channel.compound_name == "HexNAc-OS"
        assert float(channel.dwell_time) == 0.036
        assert float(channel.cone_voltage) == 90
        assert channel.delay == "Auto"
        assert acquisition.channels_for(function=8, transition="771.3>568.4") == (channel,)
        for name, function, transition in [
            ("Sialyl-OS", 18, "1200.4>1099.4"),
            ("HexNAc-OS", 8, "771.3>568.4"),
        ]:
            channel = next(
                channel for channel in acquisition.mrm_channels
                if channel.function == function
                and channel.transition == MRMTransition.parse(transition)
            )
            assert channel.number == 2
            assert dataset[acquisition.index, name].quantifier_chromatogram is not None
    for sample_index, acquisition in enumerate(dataset.acquisitions):
        acquired = {
            (channel.function, channel.transition) for channel in acquisition.mrm_channels
        }
        for trace in dataset.chromatograms[sample_index * 27 : (sample_index + 1) * 27]:
            assert (trace.function, MRMTransition.parse(trace.trace)) in acquired
    for pair in dataset.pairs():
        assert len(pair.chromatograms) == len(pair.compound.traces)
        assert pair.quantifier_chromatogram is not None
        for transition, chromatogram in zip(
            pair.compound.traces, pair.chromatograms, strict=True
        ):
            assert pair.chromatogram(transition) is chromatogram
            assert chromatogram.function == pair.compound.function
            assert pair.sample.index * 27 <= chromatogram.index < (pair.sample.index + 1) * 27
            assert (
                len(chromatogram.times) == len(chromatogram.intensities) == chromatogram.scans
            )
            assert chromatogram.scans > 0
            assert dataset.results_for(
                pair.sample, compound=pair.compound, transition=transition
            )
        if pair.compound.has_qualifier:
            assert pair.qualifier_chromatogram is not None
    assert dataset.qld.to_bytes() == fixture.read_bytes()


def test_read_real_fixture() -> None:
    pytest.importorskip("olefile")
    fixture = Path(__file__).parents[1] / "data" / "UMP 20260508 PS.qld"
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
