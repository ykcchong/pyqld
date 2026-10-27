"""Exact MRM identities and independently reported acquisition channels."""

from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext

import pytest

from pyqld import (
    AcquisitionChannel,
    AcquisitionFunction,
    AcquisitionReport,
    Dataset,
    MRMTransition,
    QLDFile,
)
from pyqld.dbf import DBFTable
from pyqld.ini import IniBlob, IniSection

REPORT = """Function 3
Start and End Time(mins): 1.000 to 2.000
Ionization mode: ES-
Function type: MRM of 2 channels
Chan Reaction Dwell(secs) Cone Volt. Col.Energy Delay(secs) Compound
1 : 100.00 > 25.00 0.036 75.0 60.0 Auto Analyte
2 : 100.00 > 50.00 0.036 75.0 50.0 Auto Analyte

-- END FUNCTION --
"""


def _sample_info(report: str) -> IniSection:
    return IniSection("SampleInfo", [
        ("Experiment", IniSection("Experiment", [
            ("__blob__", IniBlob(len(report), report, "text")),
        ])),
    ])


def _dataset(report: str | None) -> Dataset:
    qld = QLDFile(path="test.qld")
    qld.tables = {
        "QD1": DBFTable(1, [], [{"ID": "sample"}]),
        "QD3": DBFTable(1, [], [{
            "NAME": "Analyte", "RAW_FUNC": 3, "QUAN_TRACE": "100>50",
            "SEC_TRACE": "100>25", "SEC_EXIST": True,
        }]),
        "QD5": [
            IniSection("Chromatogram", [("Function", "3"), ("Trace", trace)])
            for trace in ("100.00 > 25", "100.0 > 50.00")
        ],
    }
    if report is not None:
        qld.tables["QD6"] = [_sample_info(report)]
    return Dataset(qld)


@pytest.mark.parametrize(
    "label",
    ["568.20 > 347.20", "568.2>347.2", " +5.682e2\t>3.472E+2 "],
)
def test_transition_numeric_identity(label) -> None:
    parsed = MRMTransition.parse(label)
    expected = MRMTransition(Decimal("568.2"), Decimal("347.2"))
    assert parsed == expected
    assert hash(parsed) == hash(expected)
    assert {parsed: "channel"}[expected] == "channel"


def test_transition_parsing_does_not_round_to_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        first = MRMTransition.parse("100.000000000000001>50")
        second = MRMTransition.parse("100.000000000000002>50")
        assert first != second
        assert first.precursor_mz == Decimal("100.000000000000001")


@pytest.mark.parametrize("label", ["TIC", "BPI", "A B", "100", "100 > 50 extra", "a > b"])
def test_non_mrm_labels_are_not_parsed_as_mass_pairs(label) -> None:
    assert MRMTransition.parse(label) is None


@pytest.mark.parametrize("mass", [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Inf")])
def test_invalid_mrm_masses_are_rejected(mass) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        MRMTransition(mass, Decimal("50"))


def test_mrm_construction_rejects_binary_floats() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        MRMTransition(100.0, 50.0)


def test_acquisition_report_parses_channels_without_assigning_roles() -> None:
    report = AcquisitionReport(REPORT.replace("\n", "\r\n"))
    channels = report.mrm_channels
    assert channels is report.mrm_channels
    assert [channel.number for channel in channels] == [1, 2]
    assert [channel.function for channel in channels] == [3, 3]
    assert channels[1].transition == MRMTransition.parse("100>50")
    assert channels[1].collision_energy == Decimal("50")
    assert channels[1].ionization_mode == "ES-"

    dataset = _dataset(report.text)
    pair = dataset[0, 0]
    assert pair.quantifier_chromatogram is dataset.chromatograms[1]
    assert pair.qualifier_chromatogram is dataset.chromatograms[0]
    assert pair.chromatograms == [dataset.chromatograms[1], dataset.chromatograms[0]]
    assert dataset.acquisitions[0].mrm_channels == channels


def test_acquisition_function_navigation_preserves_function_settings() -> None:
    text = (REPORT + REPORT.replace("Function 3", "Function 8")).replace("\n", "\r\n")
    acquisition = _dataset(text).acquisition(0)
    functions = acquisition.functions
    assert functions is acquisition.functions
    assert [function.number for function in functions] == [3, 8]
    function = acquisition.function(3)
    assert isinstance(function, AcquisitionFunction)
    assert function is functions[0]
    assert function.function_type == "MRM of 2 channels"
    assert function.ionization_mode == "ES-"
    assert function.start_time == Decimal("1.000")
    assert function.end_time == Decimal("2.000")
    assert function.text.startswith("Function 3\r\n")
    assert "Function 8" not in function.text
    assert function.channel(2) is acquisition.channels_for(function=3)[1]
    assert function.mrm_channels == acquisition.mrm_channels[:2]
    assert "Reaction" not in repr(function)
    with pytest.raises(FrozenInstanceError):
        function.number = 4
    with pytest.raises(FrozenInstanceError):
        function.channel(1).dwell_time = Decimal("1")
    with pytest.raises(KeyError):
        acquisition.function(0)  # vendor function number, not tuple index
    with pytest.raises(KeyError):
        function.channel(0)


def test_acquisition_channels_expose_remaining_report_columns() -> None:
    report = AcquisitionReport(REPORT.replace("Auto Analyte", "0.005 Analyte with spaces"))
    channel = report.function(3).channel(1)
    assert channel.dwell_time == Decimal("0.036")
    assert channel.cone_voltage == Decimal("75.0")
    assert channel.collision_energy == Decimal("60.0")
    assert channel.delay == "0.005"
    assert channel.compound_name == "Analyte with spaces"
    assert AcquisitionReport(REPORT).function(3).channel(1).delay == "Auto"


def test_acquisition_channel_optional_columns_and_constructor_compatibility() -> None:
    report = AcquisitionReport(REPORT.replace(" Auto Analyte", ""))
    channel = report.function(3).channel(1)
    assert channel.delay == channel.compound_name == ""
    original = AcquisitionChannel(3, 1, channel.transition, "ES-", Decimal("60"))
    assert original.dwell_time is original.cone_voltage is None
    assert original.delay == original.compound_name == ""


def test_channel_filter_retains_duplicate_transitions_and_scopes_function() -> None:
    duplicate = REPORT.replace("100.00 > 25.00", "100.00 > 50.00")
    report = duplicate + REPORT.replace("Function 3", "Function 8")
    acquisition = _dataset(report).acquisition(0)
    assert len(acquisition.channels_for(transition=" 1e2 > 50.00 ")) == 3
    assert len(acquisition.channels_for(function=3, transition="100>50")) == 2
    transition = MRMTransition.parse("100>50")
    assert acquisition.channels_for(function=8, transition=transition) == (
        acquisition.function(8).channel(2),
    )
    assert acquisition.channels_for() == acquisition.mrm_channels
    assert acquisition.channels_for(function=7) == ()
    assert acquisition.channels_for(transition="100>51") == ()
    with pytest.raises(ValueError, match="not a numeric MRM transition"):
        acquisition.channels_for(transition="TIC")


def test_non_mrm_functions_keep_metadata_without_claiming_mrm_channels() -> None:
    text = "Function 9\nIonization mode: ES+\nFunction type: Scan\nMass range: 50 to 500\n"
    report = AcquisitionReport(text)
    function = report.function(9)
    assert function.function_type == "Scan"
    assert function.text == text
    assert function.ionization_mode == "ES+"
    assert function.start_time is function.end_time is None
    assert function.mrm_channels == report.mrm_channels == ()
    with pytest.raises(KeyError):
        function.channel(1)


def test_duplicate_non_mrm_function_numbers_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate function"):
        _ = AcquisitionReport("Function 1\nFunction type: Scan\n" * 2).functions


def test_function_channel_table_does_not_consume_later_report_sections() -> None:
    report = AcquisitionReport(REPORT + "-- Other section --\n1: unrelated data\n")
    assert len(report.mrm_channels) == 2
    assert "unrelated" not in report.function(3).text


def test_malformed_mrm_function_can_be_inspected_before_channel_parsing() -> None:
    report = AcquisitionReport(REPORT.replace("MRM of 2 channels", "MRM unknown"))
    assert report.function(3).function_type == "MRM unknown"
    with pytest.raises(ValueError, match="unsupported"):
        report.channels_for(function=3)
    with pytest.raises(ValueError, match="unsupported"):
        report.function(3).channel(1)


@pytest.mark.parametrize("report", [None, "", "-- HEADER --\nInstrument: MS\n"])
def test_missing_acquisition_channels_leave_numeric_matching_available(report) -> None:
    dataset = _dataset(report)
    assert len(dataset[0, 0].chromatograms) == 2
    if report is not None:
        assert dataset.acquisitions[0].mrm_channels == ()


def test_acquisition_channels_are_scoped_by_function() -> None:
    dataset = _dataset(REPORT + REPORT.replace("Function 3", "Function 4"))
    assert len(dataset.acquisitions[0].mrm_channels) == 4
    assert len(dataset[0, 0].chromatograms) == 2


def test_unreported_function_does_not_claim_acquisition_validation() -> None:
    dataset = _dataset(REPORT.replace("Function 3", "Function 4"))
    assert all(channel.function == 4 for channel in dataset.acquisitions[0].mrm_channels)
    assert len(dataset[0, 0].chromatograms) == 2


def test_acquisition_conflicts_are_scoped_to_the_sample() -> None:
    qld = _dataset(REPORT).qld
    qld.tables["QD1"] = DBFTable(2, [], [{"ID": "first"}, {"ID": "second"}])
    qld.tables["QD5"] *= 2
    qld.tables["QD6"].append(_sample_info(REPORT.replace("100.00 > 50.00", "100.00 > 51.00")))
    dataset = Dataset(qld)
    assert len(dataset[0, 0].chromatograms) == 2
    assert dataset[1, 0].qualifier_chromatogram is dataset.chromatograms[2]
    with pytest.raises(ValueError, match="acquisition transition mismatch.*second.*function 3"):
        _ = dataset[1, 0].quantifier_chromatogram


def test_duplicate_acquisition_mass_pairs_are_ambiguous() -> None:
    dataset = _dataset(REPORT.replace("100.00 > 25.00", "100.00 > 50.00"))
    with pytest.raises(ValueError, match="ambiguous acquisition channels.*channels \\[1, 2\\]"):
        _ = dataset[0, 0].quantifier_chromatogram


@pytest.mark.parametrize(
    "report,message",
    [
        (REPORT.replace("MRM of 2 channels", "MRM of 3 channels"), "incomplete"),
        (REPORT.replace("2 : ", "1 : "), "duplicate"),
        (REPORT.replace("100.00 > 50.00", "not a reaction"), "invalid MRM channel"),
        (REPORT.replace("Col.Energy", "UnknownColumn"), "unsupported"),
        (REPORT.replace("MRM of 2 channels", "MRM unknown"), "unsupported"),
        (REPORT + REPORT, "duplicate MRM acquisition table"),
    ],
)
def test_malformed_acquisition_tables_are_explicit_errors(report, message) -> None:
    with pytest.raises(ValueError, match=message):
        _ = AcquisitionReport(report).mrm_channels
    dataset = _dataset(report)
    assert len(dataset.chromatograms) == 2
    with pytest.raises(ValueError, match="invalid acquisition metadata.*sample"):
        _ = dataset[0, 0].quantifier_chromatogram


def test_absent_quantifier_does_not_change_qualifier_role() -> None:
    qld = _dataset(REPORT).qld
    qld.tables["QD5"] = qld.tables["QD5"][:1]
    dataset = Dataset(qld)
    pair = dataset[0, 0]
    assert pair.quantifier_chromatogram is None
    assert pair.quan_chromatogram is None
    assert pair.qualifier_chromatogram is dataset.chromatograms[0]
    assert pair.qual_chromatogram is dataset.chromatograms[0]
    assert pair.chromatograms == [pair.qualifier_chromatogram]


def test_absent_qualifier_does_not_change_quantifier_role() -> None:
    qld = _dataset(REPORT).qld
    qld.tables["QD5"] = qld.tables["QD5"][1:]
    dataset = Dataset(qld)
    pair = dataset[0, 0]
    assert pair.quantifier_chromatogram is dataset.chromatograms[0]
    assert pair.qualifier_chromatogram is None


def test_non_mrm_trace_lookup_does_not_require_mrm_acquisition_metadata() -> None:
    qld = _dataset(REPORT).qld
    qld.tables["QD3"].records[0].update(QUAN_TRACE="TIC", SEC_EXIST=False)
    qld.tables["QD5"] = [IniSection("Chromatogram", [("Function", "3"), ("Trace", " TIC ")])]
    dataset = Dataset(qld)
    assert dataset[0, 0].quantifier_chromatogram is dataset.chromatograms[0]
    assert dataset[0, 0].chromatogram("TIC") is dataset.chromatograms[0]
    assert dataset[0, 0].chromatogram("tic") is None
