# User Guide

## Requirements

`pyqld` supports Python 3.10 and newer. Its only runtime dependency is
[`olefile`](https://pypi.org/project/olefile/), used to read the OLE2 container.

Install from a checkout:

```bash
python -m pip install ./pyqld
```

For development:

```bash
cd pyqld
python -m pip install -e ".[dev]"
```

## Read a file

`read()` is the normal entry point. It decodes the complete file eagerly and
returns a `Dataset`.

```python
import pyqld

dataset = pyqld.read("batch.qld")

print(dataset.samples)
print(dataset.compounds)
print(dataset.results)
print(dataset.chromatograms)
print(dataset.spectra)
```

Input may be a string or `pathlib.Path`. The file remains closed after reading;
no context manager is required.

## Look up results

Samples may be selected by zero-based index, sample ID, file name, or an
existing `Sample`. Compounds may be selected by index, exact name, or an
existing `Compound`. Existing objects must belong to the same dataset; use the
sample ID, file name, or compound name when looking up an equivalent record in
another file.

```python
sample = dataset.sample(0)
compound = dataset.compound("5-HIAA")
result = dataset[sample, compound]

print(result.area)
print(result.height)
print(result.retention_time)
print(result.result)
```

A sample/compound pair can have more than one quantitation result. `result`
selects the primary row, falling back to the first row if necessary.

```python
for quantitation in result.results:
    print(quantitation.integration)
    print(quantitation.quality)
    print(quantitation.concentration, quantitation.response)
```

All table-backed records are read-only mappings with snake_case names:

```python
print(sample.injection_volume)
print(sample["stock_dil"])
print(compound.quantifier_transition)
print(dict(compound))
```

Use `raw_fields` only for format research that requires original vendor names.
Top-level loaded collections such as `samples`, `compounds`, `results`,
`chromatograms`, and `spectra` are immutable tuples. Filtering and pair-level
accessors return new lists that callers may modify.

## Filter results

```python
results = dataset.results_for(
    sample,
    function=compound.function,
    transition=compound.quantifier_transition,
)

for result in dataset.unassigned_results:
    print(result.sample_id, result.function, result.transition)
```

`unassigned_results` retains QD2 rows with no compound name. They can still be
selected by sample, function, and transition.

## Acquisition, calibration, and audit metadata

```python
acquisition = dataset.acquisition(sample)
if acquisition is not None:
    print(acquisition.instrument)
    print(acquisition.acquired_at)
    print(acquisition.report["calibration"])

curve = dataset.method[compound.name].calibration
for point in curve.points:
    print(point.concentration, point.response)

for event in dataset.method.audit_events:
    print(event.timestamp, event.event, event.details)
```

## Chromatograms and spectra

```python
chromatogram = result.quantifier_chromatogram
if chromatogram is not None:
    for time, intensity in chromatogram:
        print(time, intensity)

for spectrum in result.spectra(rt_window=0.2):
    for mass, intensity in spectrum:
        print(spectrum.time, mass, intensity)
```

Chromatograms are associated using sample-major stream order plus the method's
function and trace fields. Spectra are associated by function; `rt_window`
optionally filters around the pair's primary retention time.

### Numeric MRM matching and acquisition evidence

MRM transitions are matched as exact `Decimal` precursor/product m/z pairs,
within the owning sample and acquisition function. For example, `568.20 > 347.20`,
`568.2>347.2`, and `5.682e2 > 3.472e2` identify the same transition. This applies
to chromatogram lookup, result filtering, and peak editing. No mass tolerance
or rounding is applied: genuinely different masses remain distinct.
Other traces, such as `TIC`, retain case-sensitive text matching. Original labels,
decoded signal arrays, and raw streams remain unchanged.

```python
from pyqld import MRMTransition

transition = MRMTransition.parse(compound.quantifier_transition)
if transition is not None:
    print(transition.precursor_mz, transition.product_mz)  # Decimal values

acquisition = dataset.acquisition(sample)
if acquisition is not None:
    for channel in acquisition.mrm_channels:
        print(channel.function, channel.number, channel.transition)
        print(channel.ionization_mode, channel.collision_energy)
```

For recognized `Function N` / `MRM of N channels` tables in the embedded
acquisition report, chromatogram lookup additionally checks that the numeric
transition is declared for that sample's function. Channel numbers are not
quantifier/qualifier roles: those roles come from the quantitation method.
A missing quantifier is returned as `None`, never replaced with the qualifier.

Multiple stored chromatograms for the same sample/function/transition, or
multiple acquisition channels with that same function/transition, raise
`ValueError` rather than choosing the first. A mismatch with a recognized
acquisition table also raises `ValueError`. A recognized but malformed or
unsupported MRM table raises `ValueError` when channels or an MRM chromatogram
are requested. All stored traces remain available through
`dataset.chromatograms` for inspection.

If acquisition metadata is absent, its format is unrecognized, or the requested
function has no recognized MRM table, numeric matching still works but is
**not acquisition-validated**. `acquisition.mrm_channels` exposes only recognized
channels (an empty tuple when none are recognized). Parsing a transition label
alone is not independent acquisition evidence; even the embedded report describes
configured channels, not a verification against the original raw instrument file.

## Iterate a batch

```python
for result in dataset.pairs():
    if result.area is not None:
        print(result.sample.id, result.compound.name, result.area)
```

`pairs()` visits every sample/compound combination, including combinations with
no detected peak.

## Raw tables and sections

Use `open_qld()` when the high-level relationships are not needed:

```python
from pyqld import open_qld

qld = open_qld("batch.qld")
print(qld.stream_names())
print(qld.tables["QD1"].records)
print(qld.tables["QD5"][0].to_dict())
```

`qld.streams` contains decoded plaintext bytes. `qld.tables` contains
`DBFTable` objects for QD1-QD4 and lists of `IniSection` for QD5-QD8 and `qld`.

## Edit And Save QLD

Edits return a new dataset, leaving the original unchanged:

```python
dataset = pyqld.read("batch.qld")
edited = dataset.with_changes(dataset.samples[0], file_text="Reviewed sample")
edited = edited.with_changes(edited.results[0], area=125.5)
edited.save("batch-edited.qld")
```

Pass a record belonging to the current dataset and snake_case field names.
Sample, compound, method-compound, and quantitation-result records are supported.
Values must fit their existing DBF field widths: text is printable ASCII,
numbers must be finite, and logical fields require booleans. Invalid edits fail
without changing the original. Relationship keys (including IDs, names, file
names, functions, transitions, and primary-peak markers) are protected.

At the lower level, use `qld.with_changes("QD1", 0, file_text="Reviewed")`,
`qld.to_bytes()`, or `qld.save(path)`. Saving refuses existing destinations
unless `overwrite=True` is supplied, and commits the output atomically.
An unedited file is saved byte-for-byte; edited files preserve untouched OLE
streams and directory metadata. The writer supports version-3 OLE containers.

This is stored-value editing, not quantitation recalculation. Changing area,
integration boundaries, calibration, or concentration does not update dependent
values or create vendor audit events. Rows and raw trace arrays cannot yet be
added, removed, or edited. Modified streams may grow because they use literal
LZSS blocks. Native TargetLynx acceptance has not been verified; retain originals
and validate edited copies in the vendor software before operational use.

## Export JSON

```python
from pyqld import export_dir, export_json

export_json("batch.qld", "batch.json", indent=2)
export_dir("batch.qld", "batch", indent=2, split_sections=True)
```

Set `include_raw=True` only when decoded stream bytes are required. In a single
JSON document they are base64 encoded; directory export writes binary files.
Large chromatogram and spectrum streams can be split into one JSON file per
section with `split_sections=True`.

## Pickle and Spyder

Datasets, record objects, compound-result views, chromatograms, and acquisition
reports support Python `pickle` and `cloudpickle` (used by Spyder's Variable
Explorer). Record aliases and acquisition-report mappings remain read-only
after restoration, and references within a serialized dataset remain linked.

```python
import pickle

restored = pickle.loads(pickle.dumps(dataset))
print(restored.samples[0].id)
```

Only unpickle trusted data: loading a pickle can execute arbitrary code.
Pickles are version-sensitive Python object snapshots, not a stable archival
format. Use QLD for preserving source data or JSON for interoperable exports.
`cloudpickle` is optional at runtime and is included in the development
dependencies for compatibility tests.

After updating pyqld for Spyder, restart Spyder and its kernel so both processes
use the updated package. If they use different Python environments, update
pyqld in both.

## Command line

```text
pyqld INPUT [OUTPUT]
pyqld INPUT --list
pyqld INPUT --out-dir DIR [--split-sections]
```

Useful options are `--indent N`, `--include-raw`, `--split-threshold N`, and
`--out-dir DIR`. Use `OUTPUT` equal to `-` to write JSON to standard output.

## Errors and limits

* A missing path raises `FileNotFoundError` through `olefile`.
* A non-OLE or damaged container raises an `olefile` parsing exception.
* Unknown decoded blob types are preserved as base64 rather than discarded.
* Loading is eager. Large batches require memory for decoded streams and object
  wrappers at the same time.
* Names are exact and case-sensitive.
* QD8 is normally empty but is retained when present.