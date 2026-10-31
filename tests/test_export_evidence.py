"""Evidence needed by MS exporters without changing legacy model behavior."""

import math

import pytest

from pyqld import Dataset, QLDFile
from pyqld.dbf import DBFTable
from pyqld.ini import IniSection
from pyqld.model import Spectrum


def _dataset(stream, samples, media, counts=None):
    top, child, section = (
        ("Chromatograms", "SampleChromatograms", "Chromatogram")
        if stream == "QD5" else ("Spectra", "SampleSpectra", "Spectrum")
    )
    tables = {
        "QD1": DBFTable(samples, [], [{"ID": str(index)} for index in range(samples)]),
        stream: [IniSection(section, [("Function", "1")]) for _ in range(media)],
    }
    if counts is not None:
        tables["qld"] = [IniSection(top, [
            (child, IniSection(child, [("TableID", str(index)), ("TableSize", str(size))]))
            for index, size in counts
        ])]
    return Dataset(QLDFile(path="evidence.qld", tables=tables))


@pytest.mark.parametrize("stream", ["QD5", "QD7"])
@pytest.mark.parametrize(
    "samples,media,counts,expected,sizes",
    [
        (2, 4, [(1, 2), (2, 2)], "audited", [2, 2]),
        (2, 3, [(2, 2), (1, 1)], "audited", [1, 2]),
        (2, 3, [(1, 0), (2, 3)], "audited", [0, 3]),
        (1, 3, [(1, 3)], "audited", [3]),
        (2, 4, None, "inferred", [2, 2]),
        (1, 3, None, "single_sample", [3]),
        (2, 3, None, "unresolved", None),
        (0, 1, None, "unresolved", None),
        (2, 0, None, "empty", [0, 0]),
        (1, 0, None, "empty", [0]),
        (0, 0, None, "empty", []),
        (2, 0, [(1, 0), (2, 0)], "empty", [0, 0]),
        (2, 4, [(1, 2)], "unresolved", None),
        (2, 4, [(1, 2), (1, 2)], "unresolved", None),
        (2, 4, [(1, 1), (2, 2)], "unresolved", None),
        (2, 4, [(1, -1), (2, 5)], "unresolved", None),
        (2, 4, [(1, 2), (3, 2)], "unresolved", None),
        (2, 4, [(0, 2), (2, 2)], "unresolved", None),
        (2, 4, [(1, "bad"), (2, 2)], "unresolved", None),
        (2, 4, [("bad", 2), (2, 2)], "unresolved", None),
        (1, 3, [(1, 2)], "unresolved", None),
        (1, 3, [(1, 3), (1, 3)], "unresolved", None),
        (1, 0, [(1, 1)], "unresolved", None),
        (2, 0, [(1, 0)], "unresolved", None),
    ],
)
def test_media_ownership_matches_existing_layout(
    stream, samples, media, counts, expected, sizes,
):
    dataset = _dataset(stream, samples, media, counts)
    assert dataset.media_ownership(stream) == expected
    lookup = dataset.chromatograms_for if stream == "QD5" else dataset.spectra_for
    stored = dataset.chromatograms if stream == "QD5" else dataset.spectra
    if sizes is None:
        for index in range(samples):
            with pytest.raises(ValueError, match="unresolved .* ownership"):
                lookup(index)
    else:
        offset = 0
        for index, size in enumerate(sizes):
            assert lookup(index) == list(stored[offset:offset + size])
            offset += size
        assert offset == media
    other_stream = "QD7" if stream == "QD5" else "QD5"
    assert dataset.media_ownership(other_stream) == "empty"


@pytest.mark.parametrize("stream", ["QD1", "qd5", "", None, 5])
def test_media_ownership_rejects_invalid_stream(stream):
    with pytest.raises(ValueError, match="expected QD5 or QD7"):
        _dataset("QD5", 1, 0).media_ownership(stream)


@pytest.mark.parametrize("items", [[], [("Time", "")], [("Time", " \t ")]])
def test_retention_time_distinguishes_absence_from_legacy_zero(items):
    spectrum = Spectrum(IniSection("Spectrum", items), 0)
    assert spectrum.retention_time is None
    assert spectrum.time == 0.0
    assert spectrum.to_dict() == dict(items)


@pytest.mark.parametrize("value,expected", [("0", 0.0), ("0.0", 0.0), ("1.25", 1.25),
                                          (" 2.5 ", 2.5), ("1e-3", 0.001)])
def test_retention_time_preserves_valid_minutes(value, expected):
    spectrum = Spectrum(IniSection("Spectrum", [("Time", value)]), 0)
    assert spectrum.retention_time == expected
    assert spectrum.time == expected


@pytest.mark.parametrize(
    "value", ["bad", "1.2 minutes", "nan", "NaN", "inf", "-inf", "1e999", "-0.1",
              IniSection("Time"), None, []],
)
def test_retention_time_rejects_invalid_values_without_changing_legacy_time(value):
    spectrum = Spectrum(IniSection("Spectrum", [("Time", value)]), 0)
    legacy = spectrum.time
    with pytest.raises(ValueError, match="invalid spectrum retention time"):
        _ = spectrum.retention_time
    if math.isnan(legacy):
        assert math.isnan(spectrum.time)
    else:
        assert spectrum.time == legacy
