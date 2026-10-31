"""Export guarantees, with real OpenMS and an independent mzML reader."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from pyqld import Dataset, QLDFile
from pyqld.dbf import DBFTable
from pyqld.ini import IniBlob, IniSection
from pyqld.interop import (
    SpectrumMetadata,
    export_mzml,
    export_quantitation,
    quantitation_report,
    to_matchms,
    to_openms,
)


@pytest.fixture
def oms():
    return pytest.importorskip("pyopenms")


def signal(trace="100>50", *, times=(1.0, 1.5, 2.0), values=(0.0, 12.5, 0.0)):
    section = IniSection("Chromatogram", [
        ("Function", "3"), ("Trace", trace), ("Scans", str(len(times))),
    ])
    for key, array in (("Times", times), ("Intensities", values)):
        section.add(key, IniSection(key, [
            ("__blob__", IniBlob(len(array) * 4, list(array), "float32")),
        ]))
    return section


@pytest.fixture
def dataset():
    report = """Function 3
Start and End Time(mins): 1.0 to 2.0
Ionization mode: ES-
Function type: MRM of 1 channels
Chan Reaction Dwell(secs) Cone Volt. Col.Energy Delay(secs) Compound
1 : 100 > 50 0.036 75.0 60.0 Auto Analyte
"""
    return Dataset(QLDFile(path="source.qld", tables={
        "QD1": DBFTable(1, [], [{"ID": "repeat", "FILE_NAME": "first.raw"}]),
        "QD2": DBFTable(1, [], [{
            "SAMPLE_ID": "repeat", "RAW_NAME": "first.raw", "NAME": "Analyte",
            "RAW_FUNC": 3, "QUAN_TRACE": "100>50", "METH_PEAK": 0,
            "AREA": 42.0, "ANAL_CONC": 1.25, "REP_UNITS": "ng/mL",
        }]),
        "QD3": DBFTable(1, [], [{
            "NAME": "Analyte", "RAW_FUNC": 3, "QUAN_TRACE": "100>50",
            "PRED_RT": 1.5,
        }]),
        "QD5": [signal()],
        "QD6": [IniSection("SampleInfo", [
            ("NumFunctions", "1"),
            ("Experiment", IniSection("Experiment", [
                ("__blob__", IniBlob(len(report), report, "text")),
            ])),
        ])],
    }))


def test_real_openms_conversion_preserves_arrays_and_units(dataset, oms):
    conversion = to_openms(dataset, 0)
    exp, report = conversion.experiment, conversion.report
    assert exp.getNrSpectra() == 0
    assert exp.getNrChromatograms() == 1
    chrom = exp.getChromatograms()[0]
    np.testing.assert_array_equal(chrom.get_peaks()[0], [60, 90, 120])
    np.testing.assert_array_equal(chrom.get_peaks()[1], [0, 12.5, 0])
    assert chrom.getPrecursor().getMZ() == 100
    assert chrom.getProduct().getMZ() == 50
    assert chrom.getInstrumentSettings().getPolarity() == oms.IonSource.Polarity.NEGATIVE
    assert chrom.getMetaValue("pyqld:dwell_time_seconds") == pytest.approx(0.036)
    assert report["chromatograms"][0]["collision_energy_unit"] == "unverified"
    assert report["ownership"]["QD5"] == "single_sample"
    assert "MRM of 1 channels" in report["acquisition"]["report_text"]
    assert report["acquisition"]["acquired_at_timezone"] == "unspecified"
    chrom.set_peaks(([1], [99]))
    np.testing.assert_array_equal(dataset.chromatograms[0].to_numpy()[1], [0, 12.5, 0])
    assert dataset.results[0].area == 42


def test_mzml_round_trip_and_independent_reader(dataset, oms, tmp_path):
    mzml = pytest.importorskip("pyteomics.mzml")
    path = export_mzml(dataset, 0, tmp_path / "run.mzML")
    valid, errors, _ = oms.MzMLFile().isSemanticallyValid(str(path))
    assert valid, errors
    exp = oms.MSExperiment()
    oms.MzMLFile().load(str(path), exp)
    assert exp.getNrChromatograms() == 1
    assert exp.getNrSpectra() == 0
    chrom = exp.getChromatograms()[0]
    np.testing.assert_array_equal(chrom.get_peaks()[0], [60, 90, 120])
    assert chrom.getNativeID() == "pyqld_sample_0_qd5_0"
    assert chrom.getPrecursor().getMZ() == 100
    assert chrom.getProduct().getMZ() == 50
    with mzml.MzML(str(path), use_index=False) as reader:
        rows = list(reader.iterfind("chromatogram"))
    assert len(rows) == 1
    np.testing.assert_array_equal(rows[0]["time array"], [60, 90, 120])
    np.testing.assert_array_equal(rows[0]["intensity array"], [0, 12.5, 0])
    report = json.loads(path.with_name(path.name + ".json").read_text())
    assert report["mzml"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["sample_index"] == 0
    assert report["units"]["export_rt"] == "second"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run.mzML", "run.mzML.json"]


@pytest.mark.parametrize("occupied", ["run.mzML", "run.mzML.json"])
def test_export_never_overwrites_either_destination(dataset, oms, tmp_path, occupied):
    existing = tmp_path / occupied
    existing.write_text("keep")
    with pytest.raises(FileExistsError):
        export_mzml(dataset, 0, tmp_path / "run.mzML")
    assert existing.read_text() == "keep"
    assert list(tmp_path.iterdir()) == [existing]


def test_publish_failure_rolls_back_only_our_files(dataset, oms, tmp_path, monkeypatch):
    import pyqld.interop as interop

    original = interop.os.link

    def fail_second(source, target):
        if target.name.endswith(".json"):
            raise OSError("simulated publication failure")
        original(source, target)

    monkeypatch.setattr(interop.os, "link", fail_second)
    with pytest.raises(OSError, match="publication failure"):
        export_mzml(dataset, 0, tmp_path / "run.mzML")
    assert not list(tmp_path.iterdir())


def test_repeated_injections_use_distinct_ids_and_audited_boundaries(dataset, oms):
    qld = dataset.qld
    qld.tables["QD1"] = DBFTable(2, [], [
        {"ID": "repeat", "FILE_NAME": "first.raw"},
        {"ID": "repeat", "FILE_NAME": "second.raw"},
    ])
    qld.tables["QD5"] = [signal(), signal(values=(2, 4, 6))]
    qld.tables["QD6"] = []
    qld.tables["qld"] = [IniSection("Chromatograms", [
        ("SampleChromatograms", IniSection("SampleChromatograms", [
            ("TableID", str(i)), ("TableSize", "1"),
        ])) for i in (1, 2)
    ])]
    ds = Dataset(qld)
    first, second = to_openms(ds, 0), to_openms(ds, 1)
    assert second.report["ownership"]["QD5"] == "audited"
    assert first.report["chromatograms"][0]["native_id"] != (
        second.report["chromatograms"][0]["native_id"]
    )
    np.testing.assert_array_equal(
        second.experiment.getChromatograms()[0].get_peaks()[1], [2, 4, 6],
    )


def test_inferred_ownership_requires_explicit_recorded_override(dataset, oms):
    dataset.qld.tables["QD1"] = DBFTable(2, [], [{"ID": "a"}, {"ID": "b"}])
    dataset.qld.tables["QD5"] = [signal(), signal()]
    dataset.qld.tables["QD6"] = []
    ds = Dataset(dataset.qld)
    with pytest.raises(ValueError, match="inferred"):
        to_openms(ds, 0)
    conversion = to_openms(ds, 0, allow_inferred_ownership=True)
    assert conversion.report["allow_inferred_ownership"]
    assert conversion.report["ownership"]["QD5"] == "inferred"
    assert any("inferred" in warning for warning in conversion.report["warnings"])


@pytest.mark.parametrize("trace,match", [
    (signal("TIC"), "non-MRM"),
    (signal(times=(1, 0, 2)), "increase"),
    (signal(times=(-1, 0, 2)), "nonnegative"),
    (signal(times=(), values=()), "no stored points"),
    (signal(values=(0, float("nan"), 0)), "nonfinite"),
    (signal(values=(0, 1e100, 0)), "float32"),
])
def test_invalid_signals_cannot_be_exported(dataset, trace, match):
    dataset.qld.tables["QD5"] = [trace]
    with pytest.raises(ValueError, match=match):
        to_openms(Dataset(dataset.qld), 0)


def test_duplicate_traces_are_not_silently_discarded(dataset):
    dataset.qld.tables["QD5"] = [signal(), signal()]
    with pytest.raises(ValueError, match="ambiguous"):
        to_openms(Dataset(dataset.qld), 0)


def test_missing_optional_dependency_is_actionable(dataset, monkeypatch):
    import pyqld.interop as interop

    def missing(name):
        raise ModuleNotFoundError("missing pyopenms", name="pyopenms")

    monkeypatch.setattr(interop.importlib, "import_module", missing)
    with pytest.raises(ImportError, match=r"pyqld\[openms\]"):
        to_openms(dataset, 0)


@pytest.mark.parametrize("fixture_index", [0, 1])
def test_real_fixture_first_injection_round_trip(oms, tmp_path, fixture_index):
    from pyqld import read

    paths = sorted((Path(__file__).parents[1] / "data").glob("*.qld"))
    if len(paths) < 2:
        pytest.skip("local QLD fixtures unavailable")
    ds = read(paths[fixture_index])
    path = export_mzml(ds, 0, tmp_path / "fixture.mzML")
    exp = oms.MSExperiment()
    oms.MzMLFile().load(str(path), exp)
    expected = ds.chromatograms_for(0)
    assert exp.getNrChromatograms() == len(expected)
    for source, exported in zip(expected, exp.getChromatograms(), strict=True):
        x, y = source.to_numpy()
        np.testing.assert_array_equal(exported.get_peaks()[0], x * 60)
        np.testing.assert_allclose(exported.get_peaks()[1], y, rtol=1e-6, atol=0)


@pytest.fixture
def spectra_dataset(dataset):
    section = IniSection("Spectrum", [
        ("Function", "3"), ("Time", "1.5"), ("Points", "3"),
    ])
    for key, values in (("Masses", [25., 50., 75.]), ("Intensities", [1., 4., 2.])):
        section.add(key, IniSection(key, [
            ("__blob__", IniBlob(12, values, "float32")),
        ]))
    dataset.qld.tables["QD7"] = [section]
    return Dataset(dataset.qld)


@pytest.fixture
def spectrum_metadata():
    return {0: SpectrumMetadata(
        ms_level=2, spectrum_type="centroid", polarity="negative",
        precursor_mz=100, precursor_charge=-1, collision_energy_ev=20,
        isolation_lower_offset=0.5, isolation_upper_offset=0.5,
        evidence="Test fixture verified MS2 acquisition",
    )}


def test_spectra_are_omitted_unless_semantics_are_explicit(spectra_dataset, oms):
    conversion = to_openms(spectra_dataset, 0)
    assert conversion.experiment.getNrSpectra() == 0
    assert any("excluded" in warning for warning in conversion.report["warnings"])


def test_explicit_spectrum_round_trip(
    spectra_dataset, spectrum_metadata, oms, tmp_path,
):
    path = export_mzml(
        spectra_dataset, 0, tmp_path / "spectra.mzML", spectrum_metadata=spectrum_metadata,
    )
    valid, errors, _ = oms.MzMLFile().isSemanticallyValid(str(path))
    assert valid, errors
    exp = oms.MSExperiment()
    oms.MzMLFile().load(str(path), exp)
    assert exp.getNrSpectra() == 1
    spectrum = exp.getSpectrum(0)
    assert spectrum.getMSLevel() == 2
    assert spectrum.getRT() == 90
    assert spectrum.getType() == oms.SpectrumSettings.SpectrumType.CENTROID
    precursor = spectrum.getPrecursors()[0]
    assert precursor.getMZ() == 100
    assert precursor.getCharge() == -1
    assert precursor.getActivationEnergy() == 20
    assert precursor.getIsolationWindowLowerOffset() == 0.5
    np.testing.assert_array_equal(spectrum.get_peaks()[0], [25, 50, 75])
    report = json.loads(path.with_name(path.name + ".json").read_text())
    assert report["spectra"][0]["metadata_source"] == "caller supplied"
    assert report["spectra"][0]["evidence"] == spectrum_metadata[0].evidence


@pytest.mark.parametrize("changes,match", [
    ({"ms_level": True}, "ms_level"),
    ({"ms_level": 0}, "ms_level"),
    ({"ms_level": 1, "precursor_mz": 100}, "MS1"),
    ({"spectrum_type": "unknown"}, "spectrum_type"),
    ({"polarity": "unknown"}, "polarity"),
    ({"evidence": " "}, "evidence"),
    ({"precursor_mz": None}, "precursor_mz"),
    ({"precursor_mz": float("nan")}, "precursor_mz"),
    ({"precursor_mz": True}, "precursor_mz"),
    ({"precursor_mz": "100"}, "precursor_mz"),
    ({"precursor_charge": 0}, "charge"),
    ({"precursor_charge": 1}, "conflicts"),
    ({"collision_energy_ev": -2}, "collision_energy"),
])
def test_spectrum_metadata_rejects_unverified_or_inconsistent_values(changes, match):
    values = dict(
        ms_level=2, spectrum_type="centroid", polarity="negative",
        evidence="verified method", precursor_mz=100,
    )
    with pytest.raises(ValueError, match=match):
        SpectrumMetadata(**{**values, **changes})


def test_spectrum_metadata_must_cover_exact_injection(spectra_dataset, spectrum_metadata):
    for metadata in ({}, {7: spectrum_metadata[0]}, {True: spectrum_metadata[0]}):
        with pytest.raises(ValueError, match="metadata"):
            to_openms(spectra_dataset, 0, spectrum_metadata=metadata)


def test_spectrum_missing_time_is_not_exported_as_zero(spectra_dataset, spectrum_metadata):
    spectra_dataset.spectra[0].section.items[:] = [
        (key, value) for key, value in spectra_dataset.spectra[0].section.items if key != "Time"
    ]
    with pytest.raises(ValueError, match="missing retention"):
        to_openms(spectra_dataset, 0, spectrum_metadata=spectrum_metadata)


def test_matchms_conversion_preserves_arrays_and_evidence(spectra_dataset, spectrum_metadata):
    pytest.importorskip("matchms")
    spectra = to_matchms(spectra_dataset, 0, spectrum_metadata)
    assert len(spectra) == 1
    spectrum = spectra[0]
    np.testing.assert_array_equal(spectrum.peaks.mz, [25, 50, 75])
    np.testing.assert_array_equal(spectrum.peaks.intensities, [1, 4, 2])
    assert spectrum.get("precursor_mz") == 100
    assert spectrum.get("retention_time") == 90
    assert spectrum.get("charge") == -1
    assert spectrum.get("pyqld_metadata_evidence") == spectrum_metadata[0].evidence


def test_matchms_rejects_profile_spectrum(spectra_dataset):
    metadata = {0: SpectrumMetadata(
        ms_level=2, spectrum_type="profile", polarity="negative",
        precursor_mz=100, evidence="profile acquisition",
    )}
    with pytest.raises(ValueError, match="centroid MS2"):
        to_matchms(spectra_dataset, 0, metadata)


def test_quantitation_preserves_all_rows_units_and_audit(dataset, tmp_path):
    row = dataset.qld.tables["QD2"].records[0]
    dataset.qld.tables["QD2"] = DBFTable(3, [], [
        row, {**row, "METH_PEAK": 1, "DELETED_PK": True},
        {**row, "NAME": "", "METH_PEAK": 2},
    ])
    dataset.qld.tables["QD4"] = DBFTable(1, [], [
        {"NAME": "Analyte", "C_CALC": "Vendor model", "CURV_COEFS": "1 2"},
    ])
    dataset.qld.tables["qld"] = [IniSection("History", [("UnknownSetting", "preserve")])]
    ds = Dataset(dataset.qld)
    path = export_quantitation(ds, 0, tmp_path / "results.json")
    report = json.loads(path.read_text())
    assert len(report["results"]) == 3
    assert report["results"][0]["fields"]["area"] == 42
    assert report["results"][0]["fields"]["anal_conc"] == 1.25
    assert report["results"][0]["fields"]["rep_units"] == "ng/mL"
    assert report["results"][0]["chromatogram_ids"] == ["pyqld_sample_0_qd5_0"]
    assert report["results"][1]["deleted"]
    assert report["results"][2]["fields"]["name"] == ""
    assert report["units"]["result_rt"] == "minute"
    assert report["method"][0]["calibration"]["model"] == "Vendor model"
    assert report["audit"] == [section.to_dict() for section in ds.method.audit]
    with pytest.raises(FileExistsError):
        export_quantitation(ds, 0, path)


def test_quantitation_does_not_hide_unresolved_results(dataset):
    dataset.qld.tables["QD2"].records[0]["RAW_NAME"] = "unrelated.raw"
    with pytest.raises(ValueError, match="ownership"):
        quantitation_report(Dataset(dataset.qld), 0)


def test_source_fingerprint_tracks_decoded_stream_changes(dataset):
    dataset.qld.streams["custom"] = b"before"
    before = quantitation_report(dataset, 0)["decoded_streams_sha256"]
    dataset.qld.streams["custom"] = b"after"
    after = quantitation_report(dataset, 0)["decoded_streams_sha256"]
    assert before != after
    assert len(before) == len(after) == 64


def test_core_import_does_not_load_optional_engines():
    subprocess.run([
        sys.executable, "-c",
        "import sys, pyqld; "
        "assert 'pyopenms' not in sys.modules; assert 'matchms' not in sys.modules",
    ], check=True)


def test_quantitation_preserves_ambiguous_signal_links(dataset):
    dataset.qld.tables["QD5"] = [signal(), signal()]
    report = quantitation_report(Dataset(dataset.qld), 0)
    assert report["results"][0]["signal_link"] == "ambiguous"
    assert report["results"][0]["chromatogram_ids"] == [
        "pyqld_sample_0_qd5_0", "pyqld_sample_0_qd5_1",
    ]


def test_staging_error_does_not_publish_partial_outputs(tmp_path):
    from pyqld.interop import _write_outputs

    def good(path):
        path.write_text("staged")

    def fail(path):
        path.write_text("partial")
        raise OSError("disk write failure")

    with pytest.raises(OSError, match="disk write"):
        _write_outputs({tmp_path / "first": good, tmp_path / "second": fail})
    assert not list(tmp_path.iterdir())


def test_rollback_does_not_delete_a_replaced_output(tmp_path, monkeypatch):
    import pyqld.interop as interop

    first, second = tmp_path / "first", tmp_path / "second"
    original = interop.os.link

    def replace_before_failure(source, target):
        if target == second:
            first.unlink()
            first.write_text("external replacement")
            raise OSError("second publish failed")
        original(source, target)

    def write(path):
        path.write_text("ours")

    monkeypatch.setattr(interop.os, "link", replace_before_failure)
    with pytest.raises(OSError, match="publish failed"):
        interop._write_outputs({first: write, second: write})
    assert first.read_text() == "external replacement"
    assert not second.exists()
