"""Diagnostics distinguish absent data, invalid data and explicit fallbacks."""

import json
from pathlib import Path

import pytest

from pyqld import Dataset, QLDFile, ValidationReport, read
from pyqld.dbf import DBFTable
from pyqld.ini import IniBlob, IniSection, parse_ini
from pyqld.validation import _compression_error

from .test_codec import _block
from .test_model import _repeated_samples


def test_validation_reports_unresolved_ownership_without_guessing() -> None:
    dataset = _repeated_samples(raw_names=False)
    report = dataset.validate()
    assert isinstance(report, ValidationReport)
    assert not report.is_valid
    assert [issue.index for issue in report.errors if issue.code == "result_ownership"] == [
        0,
        1,
    ]
    assert any(issue.code == "ambiguous_sample_key" for issue in report.warnings)
    assert json.loads(json.dumps(report.to_dict()))["is_valid"] is False
    with pytest.raises(ValueError, match="result_ownership"):
        report.raise_for_errors()


def test_validation_checks_row_counts_and_selection() -> None:
    qld = _repeated_samples().qld
    qld.tables["QD2"].nrecs = 3
    qld.tables["QD2"].records.append(dict(qld.tables["QD2"].records[0]))
    qld.tables["QD2"].nrecs = 4
    report = Dataset(qld).validate()
    assert {issue.code for issue in report.errors} >= {"table_row_count", "ambiguous_primary"}


def test_validation_checks_signals_and_malformed_acquisition() -> None:
    trace = IniSection("Chromatogram", [("Scans", "4")])
    for key, values in [("Times", [1.0, 1.0, 3.0]), ("Intensities", [1.0, float("nan")])]:
        trace.add(
            key, IniSection(key, [("__blob__", IniBlob(4 * len(values), values, "float32"))])
        )
    qld = QLDFile(
        path="test",
        tables={
            "QD1": DBFTable(1, [], [{"ID": "sample"}]),
            "QD5": [trace],
        },
    )
    from .test_transitions import REPORT, _sample_info

    qld.tables["QD6"] = [_sample_info(REPORT.replace("MRM of 2 channels", "MRM unknown"))]
    report = Dataset(qld).validate()
    assert {issue.code for issue in report.errors} >= {
        "signal_length",
        "nonfinite_signal",
        "signal_time_order",
        "signal_count",
        "acquisition_report",
    }


@pytest.mark.parametrize("data", [b"[A]\n{\n", b"[A]\n{\n[bad\n", b"[A]\nno brace\n"])
def test_strict_ini_reports_incomplete_structure(data) -> None:
    parse_ini(data)  # tolerant reading remains available, and never loops forever
    with pytest.raises(ValueError):
        parse_ini(data, strict=True)
    qld = QLDFile(path="test", streams={"QD6": data}, tables={"QD6": parse_ini(data)})
    assert "ini_structure" in {issue.code for issue in Dataset(qld).validate().errors}


def test_strict_ini_reports_truncated_blob() -> None:
    import struct

    data = b"[Experiment]\n{\n" + struct.pack("<II", 1, 100) + b"short"
    with pytest.raises(ValueError, match="truncated blob"):
        parse_ini(data, strict=True)


@pytest.mark.parametrize(
    "data,message",
    [
        (b"\x01", "header"),
        (_block(b"\x00", b"AB")[:-1], "token buffer"),
        (_block(b"\x04", b"A\x01"), "truncated token"),
        (_block(b"\x04", b"A\x02\x03"), "back-reference"),
        (_block(b"\x00", b"ABCDE"), "without flags"),
    ],
)
def test_compression_diagnostics_reject_tolerated_corruption(data, message) -> None:
    assert message in _compression_error(data)


def test_compression_diagnostics_accept_partial_final_flag_byte() -> None:
    assert _compression_error(_block(b"\x04", b"A\x01\x03")) is None


def test_media_ownership_failure_is_reported_instead_of_looking_absent() -> None:
    qld = _repeated_samples().qld
    qld.tables["QD5"] = [IniSection("Chromatogram")]
    report = Dataset(qld).validate()
    assert any(issue.code == "media_ownership" for issue in report.errors)


def test_invalid_audited_counts_are_reported_even_when_identity_is_resolved() -> None:
    qld = _repeated_samples(audit=True).qld
    qld.tables["qld"][0].items[0][1].items[-1] = ("TableSize", "10")
    dataset = Dataset(qld)
    assert not dataset.unresolved_results  # independent unique raw-file identity remains usable
    assert any(issue.code == "result_layout" for issue in dataset.validate().errors)


def test_real_fixture_validation_is_valid_with_actionable_warnings() -> None:
    fixture = Path(__file__).parents[1] / "data" / "UOS 20261013 BEH Amide PS.qld"
    if not fixture.exists():
        pytest.skip("repository fixture unavailable")
    dataset = read(fixture)
    original = dataset.qld.to_bytes()
    report = dataset.validate()
    assert report.is_valid, report.errors
    assert {issue.code for issue in report.warnings} == {"ambiguous_sample_key", "no_primary"}
    report.raise_for_errors()
    assert dataset.qld.to_bytes() == original
