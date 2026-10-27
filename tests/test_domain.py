"""Tests for Pythonic TargetLynx domain values."""

from __future__ import annotations

from pyqld import (
    AcquisitionInfo,
    AuditEvent,
    CalibrationCurve,
    Integration,
    Quality,
)
from pyqld.ini import IniBlob, IniSection


def test_integration_and_quality_group_result_fields() -> None:
    values = {
        "start_rt": 1.0,
        "found_rt": 1.2,
        "end_rt": 1.4,
        "area": 100.0,
        "height": 25.0,
        "found_scan": 12,
        "pk_flags": 4,
        "sig_noise": 8.5,
        "actual_ir": 92.0,
        "ion_ratio": 90.0,
        "lod_flag": True,
    }

    integration = Integration.from_result(values)
    quality = Quality.from_result(values)

    assert integration.apex_time == 1.2
    assert integration.area == 100.0
    assert integration.apex_scan == 12
    assert quality.flags == 4
    assert quality.signal_to_noise == 8.5
    assert quality.below_detection_limit is True


def test_calibration_curve_parses_only_declared_points() -> None:
    curve = CalibrationCurve.from_record(
        {
            "name": "Analyte",
            "c_calc": "RF",
            "weight_fnc": "1/X",
            "origin": "I",
            "cc_valid": "T",
            "num_pts": 2,
            "cali_pt_1": "1.0 10.0 1.1 1",
            "cali_pt_2": "2.0 20.0 2.1 1",
            "cali_pt_3": "3.0 30.0 3.1 1",
            "curv_coefs": "0.5 2.0",
        }
    )

    assert curve.compound_name == "Analyte"
    assert curve.valid is True
    assert curve.coefficients == (0.5, 2.0)
    assert len(curve.points) == 2
    assert curve.points[1].calculated_concentration == 2.1


def test_acquisition_info_parses_report_and_preserves_header_bytes() -> None:
    sample_info = IniSection("SampleInfo")
    sample_info.add("ID", "1")
    sample_info.add("NumFunctions", "2")

    experiment = IniSection("Experiment")
    report = "-- HEADER --\r\nSampleID: S1\r\nInstrument: TQ\r\n-- END HEADER --\r\n"
    experiment.add("__blob__", IniBlob(len(report), report, "text"))
    sample_info.add("Experiment", experiment)

    header = IniSection("Header")
    header.add("__blob__", IniBlob(3, "\x01\x00A", "text"))
    sample_info.add("Header", header)

    info = AcquisitionInfo.from_section(sample_info, 0)

    assert info.identifier == 1
    assert info.number_of_functions == 2
    assert info.report["header"]["sample_id"] == "S1"
    assert info.report["header"]["instrument"] == "TQ"
    assert info.header_bytes == b"\x01\x00A"
    assert repr(info) == "<AcquisitionInfo index=0 sample_id='S1' instrument='TQ' functions=2>"
    assert "header_bytes" not in repr(info)
    assert repr(info.report).startswith("<AcquisitionReport sections=")


def test_audit_event_converts_filetime() -> None:
    section = IniSection("Audit")
    section.add("ID", "3")
    section.add("Time", "134226957005880000")
    section.add("Event", "Process Calibrate")
    section.add("SampleID", "-1")
    section.add("PeakID", "42")

    event = AuditEvent.from_section(section)

    assert event.id == 3
    assert event.timestamp is not None
    assert event.timestamp.year == 2026
    assert event.event == "Process Calibrate"
    assert event.sample_index is None
    assert event.peak_index == 42


def test_report_header_guard_preserves_sections_and_duplicate_values() -> None:
    from pyqld import AcquisitionReport

    report = AcquisitionReport(
        "ordinary line\nGeneral: first\n  -- Header --  \n"
        "SampleID: A\nSampleID: B\nSampleID: C\n"
        "--not a header: still a value\n -- End Header -- \nGeneral: last\n"
    )
    assert report['header']['sample_id'] == ('A', 'B', 'C')
    assert report['header']['not_a_header'] == 'still a value'
    assert report['general']['general'] == ('first', 'last')
