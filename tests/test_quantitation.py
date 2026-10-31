"""Numerical regression tests with analytically known peaks and standards."""

from pathlib import Path

import pytest

from pyqld import (
    CalibrationCurve,
    CalibrationDiagnostics,
    CalibrationPoint,
    Dataset,
    QLDFile,
    read,
)
from pyqld.dbf import DBFTable, parse_dbf
from pyqld.ini import IniBlob, IniSection
from pyqld.quantitation import LinearCalibration, integrate_peak
from pyqld.writer import edit_table_rows

from .test_dbf import _build_dbf


def test_linear_baseline_triangle() -> None:
    peak = integrate_peak([0, 1, 2], [2, 7, 4], 0, 2)
    assert peak.area == pytest.approx(4)
    assert peak.height == pytest.approx(4)
    assert peak.apex_time == 1
    assert (peak.start_height, peak.end_height) == (2, 4)


def test_interpolated_boundaries_and_area_units() -> None:
    peak = integrate_peak([0, 1, 2], [0, 4, 0], 0.5, 1.5, baseline="zero", area_scale=60)
    assert peak.area == pytest.approx(180)
    assert peak.height == 4


def test_zero_crossings_are_integrated_exactly() -> None:
    peak = integrate_peak([0, 1], [-2, 2], 0, 1, baseline="zero")
    assert peak.area == pytest.approx(0.5)


@pytest.mark.parametrize(
    "times,values,start,end",
    [
        ([0, 0], [1, 2], 0, 1),
        ([0, 1], [1], 0, 1),
        ([0, 1], [0, 0], 0, 1),
        ([0, 1], [1, float("nan")], 0, 1),
        ([0, 1], [1, 2], -1, 1),
        ([0, 1], [1, 2], 1, 0),
    ],
)
def test_invalid_traces_rejected(times, values, start, end) -> None:
    with pytest.raises(ValueError):
        integrate_peak(times, values, start, end)


@pytest.mark.parametrize("weighting", ["none", "1/x", "1/x^2"])
def test_fit_and_invert_linear_calibration(weighting) -> None:
    calibration = LinearCalibration.fit([1, 2, 3], [12, 22, 32], weighting=weighting)
    assert calibration.slope == pytest.approx(10)
    assert calibration.intercept == pytest.approx(2)
    assert calibration.concentration(42) == pytest.approx(4)
    assert LinearCalibration(10, 2, 5).concentration(42) == 20
    assert calibration.concentration(0) == pytest.approx(-0.2)


def test_invalid_calibrations_rejected() -> None:
    for slope in (0, -1, float("nan")):
        with pytest.raises(ValueError):
            LinearCalibration(slope)
    with pytest.raises(ValueError):
        LinearCalibration.fit([0, 1], [0, 2], weighting="1/x")
    with pytest.raises(ValueError):
        LinearCalibration.fit([1, 1], [2, 3])


def test_fit_diagnostics_match_analytical_residuals() -> None:
    calibration = LinearCalibration.fit([0, 1, 2], [1, 2, 5], concentration_factor=5)
    diagnostics = calibration.diagnostics
    assert isinstance(diagnostics, CalibrationDiagnostics)
    assert diagnostics.number_of_points == 3
    assert diagnostics.r_squared == pytest.approx(12 / 13)
    assert diagnostics.rmse == pytest.approx(2 ** 0.5 / 3)
    assert diagnostics.residuals == pytest.approx([1 / 3, -2 / 3, 1 / 3])
    assert diagnostics.back_calculated_concentrations == pytest.approx([1 / 6, 2 / 3, 13 / 6])
    assert diagnostics.relative_errors[0] is None
    assert diagnostics.relative_errors[1:] == pytest.approx([-100 / 3, 100 / 12])
    assert diagnostics.concentration_range == (0, 2)
    assert calibration.predict_response(3) == pytest.approx(6 + 2 / 3)
    assert calibration.concentration(calibration.predict_response(3)) == pytest.approx(15)
    assert LinearCalibration(2).diagnostics is None
    with pytest.raises(ValueError, match="nonnegative"):
        calibration.predict_response(-1)


@pytest.mark.parametrize("weighting", ["none", "1/x", "1/x^2"])
def test_stored_curve_refit_requires_explicit_point_selection(weighting) -> None:
    curve = CalibrationCurve("A", "vendor-unknown", "unknown", "unknown", False, (99,), (
        CalibrationPoint(1, 12, 100, 7), CalibrationPoint(2, 9999, 100, 9),
        CalibrationPoint(3, 32, 100, 7),
    ))
    calibration = LinearCalibration.from_curve(curve, point_indexes=[0, 2], weighting=weighting)
    assert calibration.slope == pytest.approx(10)
    assert calibration.intercept == pytest.approx(2)
    assert calibration.diagnostics.point_indexes == (0, 2)
    assert calibration.diagnostics.weighting == weighting
    assert calibration.diagnostics.rmse == pytest.approx(0, abs=1e-12)
    for indexes in ([], [0], [0, 0], [-1, 2], [0, 99], [True, 2], [[0], 2]):
        with pytest.raises(ValueError):
            LinearCalibration.from_curve(curve, point_indexes=indexes)


def test_requantitation_rejects_ambiguous_default_selection() -> None:
    from .test_model import _repeated_samples

    qld = _repeated_samples().qld
    qld.tables["QD2"].records.append(dict(qld.tables["QD2"].records[0]))
    with pytest.raises(ValueError, match="ambiguous primary"):
        Dataset(qld).requantitate(LinearCalibration(1))
    unresolved = _repeated_samples(raw_names=False)
    with pytest.raises(ValueError, match="unresolved result ownership"):
        unresolved.requantitate(LinearCalibration(1))


def test_batch_edit_is_atomic() -> None:
    data = _build_dbf([("AREA", "N", 8, 2)], [[1], [2]])
    with pytest.raises(ValueError):
        edit_table_rows(data, {0: {"area": 10}, 1: {"area": "invalid"}})
    from pyqld.dbf import parse_dbf

    assert parse_dbf(data).records[0]["AREA"] == 1


@pytest.mark.parametrize("transition", ["100>50", "100 > 50", "100.00>50.000", "1e2 > 5e1"])
def test_edit_peak_matches_transition_spacing(transition: str) -> None:
    fields = [
        ("SAMPLE_ID", "C", 10, 0), ("NAME", "C", 10, 0),
        ("RAW_FUNC", "N", 3, 0), ("QUAN_TRACE", "C", 20, 0),
        ("R_CALC", "C", 2, 0),
    ]
    numeric = [
        "START_RT", "END_RT", "FOUND_RT", "ST_HEIGHT", "EN_HEIGHT", "AREA",
        "HEIGHT", "ST_SCAN", "EN_SCAN", "FOUND_SCAN", "RESPONSE", "ABS_RESP",
        "ANAL_CONC",
    ]
    fields += [(name, "N", 12, 4) for name in numeric]
    fields += [("VALID_CONC", "L", 1, 0), ("R_AH", "C", 1, 0), ("MOD_TEXT", "C", 80, 0)]
    data = _build_dbf(
        fields,
        [["sample", "Analyte", 3, transition, "ES"] + [0] * len(numeric) + [False, "", ""]],
    )
    trace = IniSection("Chromatogram", [("Function", "3"), ("Trace", "100 > 50")])
    for name, values in [("Times", [0.0, 1.0, 2.0]), ("Intensities", [0.0, 4.0, 0.0])]:
        trace.add(name, IniSection(name, [("__blob__", IniBlob(12, values, "float32"))]))
    qld = QLDFile(path="test.qld", streams={"QD2": data}, _source=b"test")
    qld.tables = {
        "QD1": DBFTable(1, [], [{"ID": "sample"}]),
        "QD2": parse_dbf(data),
        "QD3": DBFTable(1, [], [{
            "NAME": "Analyte", "RAW_FUNC": 3, "QUAN_TRACE": "100>50",
        }]),
        "QD5": [trace],
    }
    original = Dataset(qld)

    edited = original.edit_peak(original.results[0], 0, 2, LinearCalibration(2))

    assert edited.results[0].area == pytest.approx(4)
    assert edited.results[0].concentration == pytest.approx(2)
    assert original.results[0].area == 0
    assert edited.results[0].transition == transition
    assert edited.chromatograms[0].to_dict() == original.chromatograms[0].to_dict()


def test_real_peak_quantitation_export_and_reopen(tmp_path) -> None:
    import csv
    import json

    fixture = Path(__file__).parents[1] / "data" / "UMP 20260508 PS.qld"
    if not fixture.exists():
        pytest.skip("repository fixture unavailable")
    original = read(fixture)
    result = original[0, 0].result
    calibration = LinearCalibration(1000)
    edited = original.edit_peak(
        result, result.get("start_rt"), result.get("end_rt"), calibration
    )
    updated = edited.results[result.index]
    assert updated.area > 0
    assert updated.response == updated.area
    assert updated.concentration == pytest.approx(updated.area / 1000)
    assert updated.get("found_scan") is None
    assert original.results[result.index].get("found_scan") is not None
    assert edited.qld.streams["QD5"] == original.qld.streams["QD5"]
    with pytest.raises(ValueError, match="different dataset"):
        edited.edit_peak(result, 1, 2, calibration)
    batch = edited.requantitate(LinearCalibration(2000), compound=0)
    assert batch.results[result.index].concentration == pytest.approx(updated.area / 2000)
    assert batch[0, 1].result.concentration == original[0, 1].result.concentration
    destination = batch.save(tmp_path / "processed.qld")
    reopened = read(destination)
    assert reopened.results[result.index].area == updated.area
    assert (
        reopened.results[result.index].concentration
        == batch.results[result.index].concentration
    )
    exported = batch.export_results(tmp_path / "results.csv", compound=0)
    with exported.open() as handle:
        rows = list(csv.DictReader(handle))
    assert float(rows[0]["concentration"]) == batch.results[result.index].concentration
    exported = batch.export_results(tmp_path / "results.json", format="json", compound=0)
    assert json.loads(exported.read_text())[0]["result_index"] == result.index
    with pytest.raises(FileExistsError):
        batch.export_results(exported, format="json")
    unsupported = original.with_changes(result, r_calc="IS")
    with pytest.raises(ValueError, match="external-standard"):
        unsupported.requantitate(calibration, compound=0)
    assert unsupported[1, 0].result.concentration == original[1, 0].result.concentration


def test_repeated_injection_processing_and_reopen_preserve_ownership(tmp_path) -> None:
    fixture = Path(__file__).parents[1] / "data" / "UOS 20261013 BEH Amide PS.qld"
    if not fixture.exists():
        pytest.skip("repository fixture unavailable")
    original = read(fixture)
    result = original[18, 0].result
    first_area = original[1, 0].area
    edited = original.edit_peak(
        result, result.get("start_rt"), result.get("end_rt"), LinearCalibration(1000),
        area_scale=60,
    )
    assert edited[1, 0].area == first_area
    assert edited[18, 0].area > 0
    assert edited.result_sample(edited.results[result.index]).index == 18
    updated = edited.requantitate(LinearCalibration(2000), sample=18, compound=0)
    assert updated[18, 0].result.concentration == pytest.approx(updated[18, 0].area / 2000)
    assert updated[1, 0].result.concentration == original[1, 0].result.concentration
    rows = updated.compound_results(sample=18, compound=0)
    assert {row["sample_index"] for row in rows} == {18}
    assert {row["file_name"] for row in rows} == {"MS26101319"}
    reopened = read(updated.save(tmp_path / "repeated-injection.qld"))
    assert reopened[18, 0].result.index == 3244
    assert reopened[18, 0].area == updated[18, 0].area
    assert reopened[1, 0].area == first_area
    assert reopened.qld.streams["QD5"] == original.qld.streams["QD5"]
    assert reopened.validate().is_valid
