"""Explicit assay metadata, safe publication and real TraML round-trip guarantees."""

import csv
import hashlib
import json
from dataclasses import FrozenInstanceError, replace

import numpy as np
import pytest

from pyqld import Dataset
from pyqld.assays import (
    AssayTransition,
    export_skyline,
    export_traml,
    to_targeted_experiment,
)
from pyqld.dbf import DBFTable
from pyqld.interop import to_openms
from tests.test_interop import dataset, oms, signal  # noqa: F401


@pytest.fixture
def assay():
    return AssayTransition(0, "quantifier", -1, -1, 123.5, 75.0, "external standard")


@pytest.fixture
def paired(dataset):  # noqa: F811
    dataset.qld.tables["QD3"].records[0].update({"SEC_EXIST": True, "SEC_TRACE": "100>40"})
    dataset.qld.tables["QD5"] = [signal(), signal("100>40")]
    dataset.qld.tables["QD6"] = []
    return Dataset(dataset.qld)


def test_metadata_is_explicit_and_frozen(assay):
    with pytest.raises(TypeError):
        AssayTransition(0, "quantifier")
    with pytest.raises(FrozenInstanceError):
        assay.evidence = "changed"


@pytest.mark.parametrize("field,value", [
    ("compound_index", -1), ("compound_index", True), ("compound_index", 0.0),
    ("role", "channel1"),
    ("precursor_charge", 0), ("precursor_charge", True), ("precursor_charge", -1.0),
    ("product_charge", 0), ("product_charge", False), ("product_charge", -1.5),
    ("library_intensity", -1), ("library_intensity", float("nan")),
    ("library_intensity", float("inf")),
    ("retention_time_seconds", -1), ("retention_time_seconds", float("nan")),
    ("retention_time_seconds", float("inf")),
    ("evidence", ""), ("evidence", "  "), ("evidence", None),
])
def test_invalid_metadata_rejected(assay, field, value):
    with pytest.raises(ValueError, match=field):
        replace(assay, **{field: value})


def test_real_targeted_experiment_is_small_molecule(dataset, oms, assay):  # noqa: F811
    exp = to_targeted_experiment(dataset, 0, [assay])
    assert not exp.getPeptides()
    assert not exp.getProteins()
    compound, = exp.getCompounds()
    assert compound.id == "pyqld_compound_0"
    assert compound.getChargeState() == -1
    assert compound.getRetentionTime() == 75
    assert compound.getRetentionTimeType() == oms.RetentionTime.LOCAL
    assert compound.getRetentionTimeUnit() == oms.RetentionTime.SECOND
    transition, = exp.getTransitions()
    assert transition.getCompoundRef() == compound.id
    assert transition.getPeptideRef() == ""
    assert transition.getProductChargeState() == -1
    assert transition.getPrecursorMZ() == 100
    assert transition.getProductMZ() == 50
    assert transition.getLibraryIntensity() == 123.5
    assert transition.getMetaValue("pyqld:evidence") == "external standard"
    assert transition.getNativeID() == (
        to_openms(dataset, 0).experiment.getChromatograms()[0].getNativeID()
    )


def test_real_traml_roundtrip_groups_and_preserves_metadata(paired, oms, assay, tmp_path):  # noqa: F811
    qualifier = replace(assay, role="qualifier", product_charge=-2, library_intensity=0)
    with pytest.warns(UserWarning, match="TraML semantic validation"):
        path = export_traml(
            paired, 0, [assay, qualifier], tmp_path / "assays.TraML",
            allow_nonconformant=True,
        )
    restored = oms.TargetedExperiment()
    oms.TraMLFile().load(str(path), restored)
    assert not restored.getPeptides()
    compound, = restored.getCompounds()
    assert compound.getChargeState() == -1
    assert compound.getRetentionTime() == 75
    assert compound.getRetentionTimeUnit() == oms.RetentionTime.SECOND
    assert compound.getRetentionTimeType() == oms.RetentionTime.LOCAL
    first, second = restored.getTransitions()
    assert [t.getNativeID() for t in (first, second)] == [
        "pyqld_sample_0_qd5_0", "pyqld_sample_0_qd5_1",
    ]
    assert first.getCompoundRef() == second.getCompoundRef() == compound.id
    assert first.getProductChargeState() == -1
    assert second.getProductChargeState() == -2
    assert first.getLibraryIntensity() == 123.5
    assert second.getLibraryIntensity() == 0
    assert first.getMetaValue("pyqld:role") == "quantifier"
    assert second.getMetaValue("pyqld:role") == "qualifier"
    assert second.getMetaValue("pyqld:evidence") == "external standard"
    assert first.isQuantifyingTransition()
    assert not second.isQuantifyingTransition()
    report = json.loads(path.with_name(path.name + ".json").read_text())
    assert report["traml"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert report["assays"][1]["role"] == "qualifier"
    assert report["omitted_chromatograms"] == []
    valid, errors, warnings = oms.TraMLFile().isSemanticallyValid(str(path))
    assert not valid
    assert report["allow_nonconformant"] is True
    assert report["semantic_validation"] == {
        "is_valid": False, "errors": errors, "warnings": warnings,
    }


def test_traml_semantic_failure_refuses_publication(dataset, oms, assay, tmp_path):  # noqa: F811
    with pytest.raises(ValueError, match="TraML semantic validation") as error:
        export_traml(dataset, 0, [assay], tmp_path / "semantic.TraML")
    assert "MS:1000041" in str(error.value)
    assert "MS:1001226" in str(error.value)
    assert "allow_nonconformant=True" in str(error.value)
    assert not list(tmp_path.iterdir())


def test_real_targeted_peak_finding_recovers_gaussian_rt(
    paired, oms, assay, tmp_path,  # noqa: F811
):
    expected_rt_seconds = 90.0
    times_seconds = np.arange(30.0, 151.0)
    intensities = 10000 * np.exp(-0.5 * ((times_seconds - expected_rt_seconds) / 6) ** 2)
    paired.qld.tables["QD5"] = [
        signal("100>50", times=times_seconds / 60, values=intensities),
        signal("100>40", times=times_seconds / 60, values=intensities / 2),
    ]
    source = Dataset(paired.qld)
    assays = [
        replace(assay, library_intensity=10000, retention_time_seconds=expected_rt_seconds),
        replace(
            assay, role="qualifier", library_intensity=5000,
            retention_time_seconds=expected_rt_seconds,
        ),
    ]
    with pytest.warns(UserWarning, match="TraML semantic validation"):
        path = export_traml(
            source, 0, assays, tmp_path / "gaussian.TraML", allow_nonconformant=True,
        )
    report = json.loads(path.with_name(path.name + ".json").read_text())
    assert report["allow_nonconformant"] is True
    assert report["semantic_validation"]["is_valid"] is False
    assert report["semantic_validation"]["errors"]
    targeted = oms.TargetedExperiment()
    oms.TraMLFile().load(str(path), targeted)
    chromatograms = to_openms(source, 0).experiment
    assert not targeted.getPeptides()
    assert [t.getNativeID() for t in targeted.getTransitions()] == [
        c.getNativeID() for c in chromatograms.getChromatograms()
    ]

    finder = oms.MRMFeatureFinderScoring()
    parameters = finder.getDefaults()
    parameters.setValue("TransitionGroupPicker:PeakPickerChromatogram:gauss_width", 10.0)
    finder.setParameters(parameters)
    features = oms.FeatureMap()
    finder.pickExperiment(
        chromatograms, features, targeted, oms.TransformationDescription(), oms.MSExperiment(),
    )

    assert features.size() == 1
    feature = features[0]
    assert feature.getRT() == pytest.approx(expected_rt_seconds, abs=1.0)
    assert feature.getIntensity() > 0
    assert feature.getMZ() == pytest.approx(100.0)


def test_skyline_headers_units_selection_and_no_optional_import(
    paired, assay, tmp_path, monkeypatch,
):
    import pyqld.interop as interop

    def fail_import(name):
        pytest.fail(f"unexpected optional import: {name}")

    monkeypatch.setattr(interop.importlib, "import_module", fail_import)
    path = export_skyline(paired, 0, iter([assay]), tmp_path / "assays.csv")
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert reader.fieldnames == [
        "Molecule List Name", "Molecule Name", "Precursor m/z", "Product m/z",
        "Precursor Charge", "Product Charge", "Explicit Retention Time",
    ]
    row, = rows
    assert row["Molecule Name"] == "Analyte"
    assert row["Molecule List Name"] == "pyqld_compound_0"
    assert float(row["Precursor m/z"]) == 100
    assert float(row["Product m/z"]) == 50
    assert row["Precursor Charge"] == row["Product Charge"] == "-1"
    assert float(row["Explicit Retention Time"]) == 1.25
    report = json.loads(path.with_name(path.name + ".json").read_text())
    assert report["units"]["export_rt"] == "minute"
    assert report["assays"][0]["retention_time_seconds"] == 75
    assert report["assays"][0]["library_intensity"] == 123.5
    assert report["omitted_chromatograms"][0]["native_id"] == "pyqld_sample_0_qd5_1"
    assert report["skyline"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("changes,match", [
    ({"compound_index": 99}, "compound_index"),
    ({"role": "qualifier"}, "declared qualifier"),
    ({"precursor_charge": 1}, "polarity"),
    ({"product_charge": 1}, "polarity"),
])
def test_invalid_assignment(dataset, assay, tmp_path, changes, match):  # noqa: F811
    with pytest.raises(ValueError, match=match):
        export_skyline(dataset, 0, [replace(assay, **changes)], tmp_path / "assays.csv")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("field,value", [
    ("precursor_charge", -2), ("retention_time_seconds", 76),
])
def test_inconsistent_group_rejected(paired, assay, tmp_path, field, value):
    qualifier = replace(assay, role="qualifier", **{field: value})
    with pytest.raises(ValueError, match="inconsistent"):
        export_skyline(paired, 0, [assay, qualifier], tmp_path / "assays.csv")


def test_duplicate_native_assignment_rejected(dataset, assay, tmp_path):  # noqa: F811
    with pytest.raises(ValueError, match="duplicate native"):
        export_skyline(dataset, 0, [assay, assay], tmp_path / "assays.csv")
    original = dataset.qld.tables["QD3"].records[0]
    dataset.qld.tables["QD3"] = DBFTable(2, [], [original, {**original, "NAME": "Other"}])
    with pytest.raises(ValueError, match="duplicate native"):
        export_skyline(
            Dataset(dataset.qld), 0, [assay, replace(assay, compound_index=1)],
            tmp_path / "assays.csv",
        )


def test_empty_selection_and_wrong_metadata_type(dataset, tmp_path):  # noqa: F811
    with pytest.raises(ValueError, match="explicit assay"):
        export_skyline(dataset, 0, [], tmp_path / "assays.csv")
    with pytest.raises(TypeError, match="AssayTransition"):
        export_skyline(dataset, 0, [{}], tmp_path / "assays.csv")


def test_missing_or_duplicate_stored_trace_rejected(dataset, assay, tmp_path):  # noqa: F811
    dataset.qld.tables["QD5"] = [signal("100>40")]
    dataset.qld.tables["QD6"] = []
    with pytest.raises(ValueError, match="no stored"):
        export_skyline(Dataset(dataset.qld), 0, [assay], tmp_path / "assays.csv")
    dataset.qld.tables["QD5"] = [signal(), signal()]
    with pytest.raises(ValueError, match="ambiguous"):
        export_skyline(Dataset(dataset.qld), 0, [assay], tmp_path / "assays.csv")


def test_exact_transition_lookup_does_not_round(dataset, assay, tmp_path):  # noqa: F811
    dataset.qld.tables["QD3"].records[0]["QUAN_TRACE"] = "100.00000000000001>50"
    dataset.qld.tables["QD6"] = []
    with pytest.raises(ValueError, match="no stored"):
        export_skyline(Dataset(dataset.qld), 0, [assay], tmp_path / "assays.csv")


def test_roles_follow_declaration_not_trace_order(paired, assay, tmp_path):
    paired.qld.tables["QD3"].records[0].update({
        "QUAN_TRACE": "100>40", "SEC_TRACE": "100>50",
    })
    path = export_skyline(
        Dataset(paired.qld), 0, [replace(assay, role="qualifier")],
        tmp_path / "assays.csv",
    )
    report = json.loads(path.with_name(path.name + ".json").read_text())
    row, = report["assays"]
    assert row["native_id"] == "pyqld_sample_0_qd5_0"
    assert row["role"] == "qualifier"
    assert row["polarity"] is None


def test_zero_values_are_explicit_not_replaced(dataset, assay, tmp_path):  # noqa: F811
    path = export_skyline(
        dataset, 0, [replace(assay, library_intensity=0, retention_time_seconds=0)],
        tmp_path / "assays.csv",
    )
    report = json.loads(path.with_name(path.name + ".json").read_text())
    assert report["assays"][0]["library_intensity"] == 0
    assert report["assays"][0]["retention_time_seconds"] == 0


def test_qualifier_trace_without_declaration_is_rejected(paired, assay, tmp_path):
    paired.qld.tables["QD3"].records[0]["SEC_EXIST"] = False
    with pytest.raises(ValueError, match="declared qualifier"):
        export_skyline(
            Dataset(paired.qld), 0, [replace(assay, role="qualifier")],
            tmp_path / "assays.csv",
        )


def test_missing_openms_is_actionable(dataset, assay, monkeypatch):  # noqa: F811
    import pyqld.interop as interop

    def missing(name):
        raise ModuleNotFoundError("missing pyopenms", name="pyopenms")

    monkeypatch.setattr(interop.importlib, "import_module", missing)
    with pytest.raises(ImportError, match=r"pyqld\[openms\]"):
        to_targeted_experiment(dataset, 0, [assay])


def test_source_structure_is_checked(dataset, assay, tmp_path):  # noqa: F811
    dataset.qld.tables["QD3"].nrecs = 2
    with pytest.raises(ValueError, match="invalid source structure"):
        export_skyline(Dataset(dataset.qld), 0, [assay], tmp_path / "assays.csv")


@pytest.mark.parametrize("occupied", ["assays.csv", "assays.csv.json"])
def test_never_overwrite_csv_or_sidecar(dataset, assay, tmp_path, occupied):  # noqa: F811
    existing = tmp_path / occupied
    existing.write_text("keep")
    with pytest.raises(FileExistsError):
        export_skyline(dataset, 0, [assay], tmp_path / "assays.csv")
    assert existing.read_text() == "keep"
    assert list(tmp_path.iterdir()) == [existing]


def test_publish_failure_rolls_back(dataset, assay, tmp_path, monkeypatch):  # noqa: F811
    import pyqld.interop as interop

    original = interop.os.link

    def fail_second(source, target):
        if target.name.endswith(".json"):
            raise OSError("publication failed")
        original(source, target)

    monkeypatch.setattr(interop.os, "link", fail_second)
    with pytest.raises(OSError, match="publication failed"):
        export_skyline(dataset, 0, [assay], tmp_path / "assays.csv")
    assert not list(tmp_path.iterdir())


def test_inferred_ownership_requires_opt_in(dataset, assay, tmp_path):  # noqa: F811
    dataset.qld.tables["QD1"] = DBFTable(2, [], [{"ID": "a"}, {"ID": "b"}])
    dataset.qld.tables["QD5"] = [signal(), signal()]
    dataset.qld.tables["QD6"] = []
    ds = Dataset(dataset.qld)
    with pytest.raises(ValueError, match="inferred"):
        export_skyline(ds, 0, [assay], tmp_path / "assays.csv")
    path = export_skyline(
        ds, 0, [assay], tmp_path / "assays.csv", allow_inferred_ownership=True,
    )
    report = json.loads(path.with_name(path.name + ".json").read_text())
    assert report["ownership"]["QD5"] == "inferred"
    assert report["allow_inferred_ownership"]
    assert any("inferred" in warning for warning in report["warnings"])
