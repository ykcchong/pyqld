"""CLI integration without changing existing JSON/list behavior."""

import json

import pytest

from pyqld import Dataset
from pyqld.cli import main
from tests.test_interop import dataset as dataset


@pytest.mark.parametrize("arguments,match", [
    (["--format", "mzml"], "--sample-index"),
    (["--sample-index", "0"], "non-JSON"),
    (["--format", "mzml", "--sample-index", "0", "--list"], "JSON/list"),
    (["--format", "skyline", "--sample-index", "0"], "--assays"),
    (["--format", "mzml", "--sample-index", "0", "--assays", "x"], "only"),
    (["--format", "quantitation", "--sample-index", "0", "--spectrum-metadata", "x"], "only"),
    (["--format", "mzml", "--sample-index", "0", "--allow-nonconformant-traml"], "only"),
])
def test_invalid_cli_combinations_fail_before_reading_input(arguments, match, capsys):
    with pytest.raises(SystemExit) as error:
        main(["missing.qld", *arguments])
    assert error.value.code == 2
    assert match in capsys.readouterr().err


@pytest.mark.parametrize("format,suffix", [("mzml", "mzML"), ("quantitation", "json")])
def test_cli_export(dataset, tmp_path, monkeypatch, capsys, format, suffix):
    if format == "mzml":
        pytest.importorskip("pyopenms")
    monkeypatch.setattr(Dataset, "from_path", lambda _: dataset)
    path = tmp_path / f"output.{suffix}"
    assert main([
        "input.qld", str(path), "--format", format, "--sample-index", "0",
    ]) == 0
    assert path.is_file()
    assert "wrote" in capsys.readouterr().out
    if format == "quantitation":
        assert json.loads(path.read_text())["results"][0]["fields"]["area"] == 42


@pytest.mark.parametrize("format", ["skyline", "traml"])
def test_cli_assay_metadata(dataset, tmp_path, monkeypatch, format):
    if format == "traml":
        pytest.importorskip("pyopenms")
    monkeypatch.setattr(Dataset, "from_path", lambda _: dataset)
    metadata = tmp_path / "assays.json"
    metadata.write_text(json.dumps([{
        "compound_index": 0, "role": "quantifier",
        "precursor_charge": -1, "product_charge": -1,
        "library_intensity": 1.0, "retention_time_seconds": 90.0,
        "evidence": "verified test method",
    }]))
    path = tmp_path / ("transitions.csv" if format == "skyline" else "assays.TraML")
    arguments = [
        "input.qld", str(path), "--format", format, "--sample-index", "0",
        "--assays", str(metadata),
    ]
    if format == "traml":
        with pytest.raises(SystemExit) as error:
            main(arguments)
        assert error.value.code == 2
        assert not path.exists()
        with pytest.warns(UserWarning, match="TraML semantic validation"):
            assert main([*arguments, "--allow-nonconformant-traml"]) == 0
    else:
        assert main(arguments) == 0
        assert "Precursor Charge" in path.read_text()
    assert path.with_name(path.name + ".json").is_file()


def test_cli_does_not_allow_negative_injection_index(dataset, monkeypatch, capsys):
    monkeypatch.setattr(Dataset, "from_path", lambda _: dataset)
    with pytest.raises(SystemExit) as error:
        main(["input.qld", "--format", "quantitation", "--sample-index", "-1"])
    assert error.value.code == 2
    assert "outside" in capsys.readouterr().err


def test_default_json_cli_is_preserved(dataset, tmp_path, monkeypatch):
    import pyqld.cli as cli

    calls = []

    def export(input_path, output_path, **options):
        calls.append((input_path, options))
        output_path.write_text("{}")
        return output_path

    monkeypatch.setattr(cli, "export_json", export)
    path = tmp_path / "output.json"
    assert main(["input.qld", str(path), "--indent", "2"]) == 0
    assert calls[0][1] == {"indent": 2, "include_raw": False}


def test_duplicate_metadata_keys_are_rejected(dataset, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Dataset, "from_path", lambda _: dataset)
    metadata = tmp_path / "duplicate.json"
    metadata.write_text('{"0": {}, "0": {}}')
    with pytest.raises(SystemExit) as error:
        main([
            "input.qld", "--format", "mzml", "--sample-index", "0",
            "--spectrum-metadata", str(metadata),
        ])
    assert error.value.code == 2
    assert "duplicate metadata key" in capsys.readouterr().err
