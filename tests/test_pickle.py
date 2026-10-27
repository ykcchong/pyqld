"""Serialization regressions for Python pickle and Spyder's cloudpickle."""

import pickle
from pathlib import Path

import cloudpickle
import pytest

from pyqld import Dataset, QLDFile, read
from pyqld.dbf import DBFTable
from pyqld.domain import AcquisitionReport
from pyqld.ini import IniBlob, IniSection
from pyqld.model import Compound, MethodCompound, QuantitationResult, Sample


@pytest.fixture(params=[pickle, cloudpickle], ids=["pickle", "cloudpickle"])
def serializer(request):
    return request.param


@pytest.mark.parametrize("record_type", [Sample, Compound, QuantitationResult, MethodCompound])
def test_uninitialized_record_attribute_lookup(record_type) -> None:
    record = record_type.__new__(record_type)
    for name in ("_data", "_keys", "missing", "__missing_special__"):
        with pytest.raises(AttributeError):
            getattr(record, name)
    record._data = {}
    with pytest.raises(AttributeError):
        _ = record.missing


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
@pytest.mark.parametrize("record_type", [Sample, Compound, QuantitationResult, MethodCompound])
def test_record_roundtrip(serializer, protocol, record_type) -> None:
    record = record_type({"ID": "sample", "NAME": "Analyte", "AREA": 12.5}, 3)
    record.note = "preserved instance state"
    if isinstance(record, QuantitationResult):
        _ = record.integration, record.quality
    elif isinstance(record, MethodCompound):
        _ = record.calibration

    restored = serializer.loads(serializer.dumps(record, protocol=protocol))

    assert type(restored) is record_type
    assert restored.index == 3
    assert restored.to_dict() == record.to_dict()
    assert restored.area == 12.5
    assert restored["AREA"] == restored["area"]
    assert restored.__dict__ == record.__dict__
    assert restored._keys is record._keys
    with pytest.raises(TypeError):
        restored._keys["new"] = "NEW"
    with pytest.raises(TypeError):
        restored.raw_fields["AREA"] = 2
    with pytest.raises(AttributeError):
        _ = restored.missing
    with pytest.raises(KeyError):
        restored["missing"]


def _dataset() -> Dataset:
    trace = IniSection("Chromatogram", [("Function", "3"), ("Trace", "100 > 50")])
    for name, values in [("Times", [0.0, 1.0, 2.0]), ("Intensities", [0.0, 4.0, 0.0])]:
        trace.add(name, IniSection(name, [("__blob__", IniBlob(12, values, "float32"))]))
    text = "-- Header --\nSample ID: sample\nInstrument: MS\n"
    info = IniSection("SampleInfo", [
        ("NumFunctions", "3"),
        ("Experiment", IniSection("Experiment", [
            ("__blob__", IniBlob(len(text), text, "text")),
        ])),
    ])
    qld = QLDFile(path="test.qld")
    qld.tables = {
        "QD1": DBFTable(1, [], [{"ID": "sample", "FILE_NAME": "sample.raw"}]),
        "QD2": DBFTable(1, [], [{
            "SAMPLE_ID": "sample", "NAME": "Analyte", "RAW_FUNC": 3,
            "QUAN_TRACE": "100 > 50", "AREA": 4.0, "METH_PEAK": 0,
        }]),
        "QD3": DBFTable(1, [], [{
            "NAME": "Analyte", "RAW_FUNC": 3, "QUAN_TRACE": "100>50",
        }]),
        "QD4": DBFTable(1, [], [{"NAME": "Analyte"}]),
        "QD5": [trace],
        "QD6": [info],
    }
    return Dataset(qld)


@pytest.mark.parametrize("warm_caches", [False, True])
def test_dataset_and_pair_roundtrip(serializer, warm_caches) -> None:
    original = _dataset()
    pair = original[0, 0]
    if warm_caches:
        _ = pair.result.integration, pair.result.quality
        _ = original.method.calibration_curves, original.method.audit_events

    dataset, restored = serializer.loads(serializer.dumps((original, pair)))

    assert restored.dataset is dataset
    assert restored.sample is dataset.samples[0]
    assert restored.compound is dataset.compounds[0]
    assert restored.result is dataset.results[0]
    assert dataset.sample("sample") is restored.sample
    assert dataset.compound("Analyte") is restored.compound
    assert dataset.method._ds is dataset
    assert dataset.results is dataset.peaks
    assert dataset.samples[0]._data is dataset.qld.tables["QD1"].records[0]
    assert restored.area == pair.area
    assert restored.result.__dict__ == pair.result.__dict__
    assert dataset.method[0].__dict__ == original.method[0].__dict__
    assert restored.quantifier_chromatogram is dataset.chromatograms[0]
    assert list(restored.quantifier_chromatogram) == [(0, 0), (1, 4), (2, 0)]
    assert dataset.acquisitions[0].sample_id == "sample"
    assert dataset.acquisitions[0].instrument == "MS"
    assert dataset.acquisitions[0].report.text == original.acquisitions[0].report.text
    with pytest.raises(TypeError):
        dataset.acquisitions[0].report["header"]["sample_id"] = "changed"
    with pytest.raises(ValueError, match="different dataset"):
        dataset.pair(original.samples[0], 0)


def test_chromatogram_roundtrip(serializer) -> None:
    trace = _dataset().chromatograms[0]
    restored = serializer.loads(serializer.dumps(trace))
    assert restored.index == trace.index
    assert restored.to_dict() == trace.to_dict()
    assert list(restored) == list(trace)


def test_acquisition_report_roundtrip(serializer) -> None:
    report = AcquisitionReport("-- Header --\nSample ID: first\nSample ID: second\n")
    restored = serializer.loads(serializer.dumps(report))
    assert restored.text == report.text
    assert restored["header"]["sample_id"] == ("first", "second")
    with pytest.raises(TypeError):
        restored._sections["new"] = {}
    with pytest.raises(TypeError):
        restored["header"]["sample_id"] = "changed"


@pytest.mark.parametrize("warm_caches", [False, True])
def test_acquisition_function_roundtrip(serializer, warm_caches) -> None:
    report = AcquisitionReport(
        "Function 5\nStart and End Time(mins): 1 to 2\n"
        "Ionization mode: ES-\nFunction type: MRM of 1 channel\n"
        "Chan Reaction Dwell(secs) Cone Volt. Col.Energy Delay(secs) Compound\n"
        "1 : 100 > 50 0.036 75 60 Auto Analyte\n"
    )
    function = report.function(5)
    if warm_caches:
        _ = function.start_time, function.end_time, function.mrm_channels, report.mrm_channels
    restored = serializer.loads(serializer.dumps(report))
    restored_function = serializer.loads(serializer.dumps(function))
    assert restored.function(5) == restored_function == function
    assert restored_function.start_time == function.start_time
    assert restored_function.end_time == function.end_time
    assert restored_function.channel(1) == function.channel(1)
    assert restored.channels_for(function=5, transition="100>50") == function.mrm_channels


def test_real_dataset_roundtrip(serializer) -> None:
    fixture = Path(__file__).parents[1] / "data" / "UOS 20261013 BEH Amide PS.qld"
    if not fixture.is_file():
        pytest.skip("repository fixture is not available")
    original = read(fixture)
    _ = original[1, 0].result.integration, original[1, 0].result.quality
    _ = original.method.calibration_curves, original.method.audit_events

    restored = serializer.loads(serializer.dumps(original))

    assert restored.qld.to_bytes() == fixture.read_bytes()
    assert len(restored.samples) == 21
    assert len(restored.compounds) == 18
    assert len(restored.chromatograms) == 567
    for before, after in zip(original.pairs(), restored.pairs(), strict=True):
        assert after.selection_status == before.selection_status
        expected_row = before.select_result(policy="first", include_deleted=True)
        actual_row = after.select_result(policy="first", include_deleted=True)
        assert (actual_row.to_dict() if actual_row is not None else None) == (
            expected_row.to_dict() if expected_row is not None else None
        )
        assert len(after.chromatograms) == len(before.chromatograms)
        for expected, actual in zip(before.chromatograms, after.chromatograms, strict=True):
            assert actual.to_dict() == expected.to_dict()
    edited = restored.with_changes(restored.samples[0], file_text="After unpickling")
    assert edited.samples[0].description == "After unpickling"
    assert edited.qld.streams["QD5"] == original.qld.streams["QD5"]
