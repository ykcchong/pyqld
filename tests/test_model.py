"""Tests for high-level TargetLynx relationships."""

from __future__ import annotations

import pytest

from pyqld import CompoundResult, Dataset, QLDFile, SampleCompound
from pyqld.dbf import DBFTable
from pyqld.ini import IniSection


def _section(name: str, **values: str) -> IniSection:
    section = IniSection(name)
    for key, value in values.items():
        section.add(key, value)
    return section


def test_pair_uses_sample_and_compound_peak_index() -> None:
    qld = QLDFile(path="test.qld")
    qld.tables = {
        "QD1": DBFTable(
            nrecs=1,
            fields=[],
            records=[{"ID": "sample-1", "FILE_NAME": "sample.raw"}],
        ),
        "QD2": DBFTable(
            nrecs=2,
            fields=[],
            records=[
                {
                    "SAMPLE_ID": "sample-1",
                    "NAME": "Analyte",
                    "RAW_FUNC": 3,
                    "QUAN_TRACE": "100 > 50",
                    "AREA": 12.5,
                    "FOUND_RT": 1.25,
                    "METH_PEAK": 0,
                },
                {
                    "SAMPLE_ID": "other-sample",
                    "NAME": "Analyte",
                    "AREA": 99.0,
                    "METH_PEAK": 0,
                },
            ],
        ),
        "QD3": DBFTable(
            nrecs=1,
            fields=[],
            records=[{"NAME": "Analyte"}],
        ),
    }

    result = Dataset(qld).pair("sample-1", "Analyte")

    assert len(result.peaks) == 1
    assert result.area == 12.5
    assert result.retention_time == 1.25
    assert result.primary_result is result.primary_peak
    assert result.result.integration is result.result.integration
    assert result.result.quality is result.result.quality

    dataset = result.dataset
    assert dataset["sample-1", "Analyte"].area == 12.5
    assert dataset.get_sample("sample-1").id == "sample-1"
    assert dataset.get_compound("Analyte").name == "Analyte"
    assert dataset.results_for(
        "sample-1", function=3, transition="100 > 50"
    ) == result.results


def test_records_are_snake_case_mappings() -> None:
    qld = QLDFile(path="test.qld")
    qld.tables = {
        "QD1": DBFTable(
            nrecs=1,
            fields=[],
            records=[{"ID": "sample-1", "INJ_VOL": "10.0"}],
        )
    }

    sample = Dataset(qld).samples[0]

    assert sample["inj_vol"] == "10.0"
    assert sample["INJ_VOL"] == "10.0"
    assert sample.inj_vol == "10.0"
    assert dict(sample)["inj_vol"] == "10.0"
    assert sample.to_dict()["inj_vol"] == "10.0"
    assert sample.raw_fields["INJ_VOL"] == "10.0"


def test_spectra_are_scoped_to_their_sample() -> None:
    spectra = []
    for function, time in (("3", "1.0"), ("4", "2.0"), ("3", "3.0")):
        spectra.append(_section("Spectrum", Function=function, Time=time))

    audit_spectra = IniSection("Spectra")
    audit_spectra.add(
        "SampleSpectra",
        _section("SampleSpectra", TableID="1", TableSize="1"),
    )
    audit_spectra.add(
        "SampleSpectra",
        _section("SampleSpectra", TableID="2", TableSize="2"),
    )

    qld = QLDFile(path="test.qld")
    qld.tables = {
        "QD1": DBFTable(
            nrecs=2,
            fields=[],
            records=[{"ID": "first"}, {"ID": "second"}],
        ),
        "QD3": DBFTable(
            nrecs=1,
            fields=[],
            records=[{"NAME": "Analyte", "RAW_FUNC": 3}],
        ),
        "QD7": spectra,
        "qld": [audit_spectra],
    }

    dataset = Dataset(qld)

    assert [spectrum.time for spectrum in dataset.spectra_for("first")] == [1.0]
    assert [spectrum.time for spectrum in dataset.spectra_for("second")] == [
        2.0,
        3.0,
    ]
    assert [
        spectrum.time
        for spectrum in dataset.spectra_for("second", function=3)
    ] == [3.0]
    assert [
        spectrum.time for spectrum in dataset["first", "Analyte"].spectra()
    ] == [1.0]


def test_domain_objects_cannot_cross_datasets() -> None:
    first_qld = QLDFile(path="first.qld")
    first_qld.tables = {
        "QD1": DBFTable(1, [], [{"ID": "first"}]),
        "QD3": DBFTable(1, [], [{"NAME": "A"}]),
    }
    second_qld = QLDFile(path="second.qld")
    second_qld.tables = {
        "QD1": DBFTable(1, [], [{"ID": "second"}]),
        "QD3": DBFTable(1, [], [{"NAME": "B"}]),
    }
    first = Dataset(first_qld)
    second = Dataset(second_qld)

    with pytest.raises(ValueError, match="different dataset"):
        second.pair(first.samples[0], second.compounds[0])
    with pytest.raises(ValueError, match="different dataset"):
        second.pair(second.samples[0], first.compounds[0])

    assert second.get_sample(first.samples[0]) is None
    assert second.get_compound(first.compounds[0]) is None


def test_loaded_collections_are_immutable() -> None:
    qld = QLDFile(path="test.qld")
    qld.tables = {
        "QD1": DBFTable(1, [], [{"ID": "sample"}]),
        "QD3": DBFTable(1, [], [{"NAME": "Analyte"}]),
    }

    dataset = Dataset(qld)

    assert isinstance(dataset.samples, tuple)
    assert isinstance(dataset.compounds, tuple)
    assert isinstance(dataset.results, tuple)
    assert isinstance(dataset.method.compounds, tuple)


def test_sample_compound_is_a_compatibility_alias() -> None:
    assert SampleCompound is CompoundResult
    assert CompoundResult.__name__ == "CompoundResult"


def test_method_domain_values_are_cached() -> None:
    audit = IniSection("Audit")
    audit.add("Audit", _section("Audit", ID="1", Event="Created"))
    qld = QLDFile(path="test.qld")
    qld.tables = {
        "QD4": DBFTable(1, [], [{"NAME": "Analyte"}]),
        "qld": [audit],
    }

    method = Dataset(qld).method

    assert method[0].calibration is method[0].calibration
    assert method.calibration_curves is method.calibration_curves
    assert method.audit_events is method.audit_events
