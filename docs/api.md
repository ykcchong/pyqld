# API Reference

All names below are importable from `pyqld` unless a module is shown.

This page describes the in-process Python library, not Flask endpoints.
For browser use, see the [web interface guide](../../webapp/README.md).
For HTTP clients, see the [HTTP API reference](../../webapp/HTTP_API.md),
including JSON schemas, revision handling, and curl examples.

## Primary API

### `read(path) -> Dataset`

Read, decode, parse, and index one TargetLynx `.qld` file. This is the preferred
entry point for applications.

### `Dataset`

| Attribute or method | Result |
| --- | --- |
| `Dataset.from_path(path)` | Equivalent constructor to `read(path)` |
| `samples` | Ordered `tuple[Sample, ...]` from QD1 |
| `results` | Ordered `tuple[QuantitationResult, ...]` from QD2 |
| `peaks` | Compatibility alias for `results` |
| `compounds` | Ordered `tuple[Compound, ...]` from QD3 |
| `chromatograms` | Ordered `tuple[Chromatogram, ...]` from QD5 |
| `acquisitions` | Parsed `tuple[AcquisitionInfo, ...]` from QD6 |
| `spectra` | Ordered `tuple[Spectrum, ...]` from QD7 |
| `method` | `Method` view over QD4 and the audit stream |
| `sample(key)` | Find a sample by index, ID, or file name |
| `get_sample(key)` | Find a sample or return `None` |
| `compound(key)` | Find a compound by index or exact name |
| `get_compound(key)` | Find a compound or return `None` |
| `dataset[sample, compound]` | Build a `CompoundResult` view |
| `pairs()` | Iterate every sample/compound combination |
| `results_for(...)` | Filter by sample, compound, function, transition, or name presence |
| `unassigned_results` | QD2 rows without a compound name |
| `acquisition(sample)` | Acquisition metadata corresponding to a sample |

Loaded collections are immutable so lookup indexes remain consistent.
`Sample` and `Compound` objects passed to lookup methods must belong to that
dataset; use IDs or names to resolve equivalent objects in another dataset.

### Editing

#### `Dataset.with_changes(record, **values) -> Dataset`

Return a new dataset with replacements in one existing record. `record` must
be a `Sample`, `QuantitationResult`, `Compound`, or `MethodCompound` owned by
that exact dataset instance. The original dataset is unchanged, and indexes
and domain projections are rebuilt in the returned dataset.

Use actual table field keys, not convenience-property names. For example,
change a sample description with `file_text=...`, not `description=...`.
For a result, use `found_rt` rather than `retention_time`, and `anal_conc`
rather than `concentration`. `dict(record)` exposes available snake_case keys;
the table's `fields` metadata provides types and fixed byte widths.

```python
import pyqld

original = pyqld.read("batch.qld")
edited = original.with_changes(original.samples[0], file_text="Reviewed sample")
edited = edited.with_changes(edited.samples[0], bottle="2")
output = edited.save("batch-edited.qld")
```

These fields must exist in the input file. When chaining edits, select the
record from the latest dataset. Passing `original.samples[0]` to
`edited.with_changes(...)` raises `ValueError`, even when the identifiers are
the same. Keeping earlier dataset references gives an application its own
copy-on-write history; the Python API itself has no revision counter or
undo/redo manager.

Several fields in one record can be updated atomically:

```python
pair = edited[0, 0]
record = pair.result
if record is not None:
    edited = edited.with_changes(record, start_rt="1.10", end_rt="1.40")
```

This changes stored boundaries only. It does not reintegrate the chromatogram
or calculate a new area. One invalid replacement rejects the entire call.
The public editor does not support multi-record transactions.

#### `QLDFile.with_changes(table: str, index: int, **values) -> QLDFile`

Lower-level equivalent that returns an edited file object without constructing
dataset indexes. `table` must be exactly `QD1`, `QD2`, `QD3`, or `QD4`, and
`index` is an absolute zero-based record index in that table.

```python
qld = pyqld.open_qld("batch.qld")
updated = qld.with_changes("QD1", 0, file_text="Reviewed sample")
payload = updated.to_bytes()
updated.save("batch-low-level-edit.qld")
```

Field keys are matched case-insensitively. Duplicate vendor names are
distinguished by occurrence suffixes such as `_2`; use `DBFField.key` or the
record mapping key. Direct mutations of `qld.tables`, raw records, or sections
are not a supported save mechanism: the writer tracks changes made through
`with_changes()`. Treat parsed structures as read-only when using the writer.

#### `Dataset.save(path, *, overwrite: bool = False) -> Path`
#### `QLDFile.save(path, *, overwrite: bool = False) -> Path`
#### `QLDFile.to_bytes() -> bytes`

`path` accepts `str` or `pathlib.Path`. The parent directory must already exist.
Save serializes the file, writes a temporary sibling, flushes it, and commits
the destination atomically. By default an existing target raises
`FileExistsError`; `overwrite=True` explicitly allows replacement. This does
not create a backup or change the in-memory object's original source path.

`to_bytes()` performs the same container serialization without writing to
disk, suitable for attachments and other transports. A dataset exposes it as
`dataset.qld.to_bytes()`, not `dataset.to_bytes()`.

An untouched loaded file serializes to its exact original bytes. Edited files
patch only selected DBF field bytes and preserve unmodified OLE stream payloads
and directory metadata while rebuilding the container. Modified streams use
literal LZSS blocks and may be larger. Saving edited containers currently
supports version-3 OLE with 512-byte sectors and 64-byte mini-sectors.

#### Validation and exceptions

| Condition | Result |
| --- | --- |
| Foreign record passed to `Dataset.with_changes()` | `ValueError` |
| Unsupported/missing table or missing original container | `ValueError` |
| Row index outside the table | `IndexError` |
| Unknown field key | `KeyError` |
| Protected relationship field | `ValueError` |
| Invalid type, non-finite number, non-ASCII text, or field-width overflow | `ValueError` |
| Incomplete table or invalid size headers | `ValueError` |
| Edited container has unsupported sector format | `ValueError` during serialization |
| Destination exists and overwrite is false | `FileExistsError` |
| Missing parent, filesystem permissions, or other I/O failure | Corresponding `OSError` subclass |

`C` fields accept printable ASCII strings, including an empty string. `N`
fields accept finite numeric values or numeric strings; `None` and `""` encode
a missing value. `L` fields require Python `bool`. Numeric text is encoded
without rounding to the declared decimal count and must fit the field width
after expansion to fixed decimal notation. Reading numeric values still uses
integers/floats and therefore does not promise arbitrary decimal precision.

Protected names are `ID`, `SAMPLE_ID`, `NAME`, `FILE_NAME`, `RAW_NAME`,
`RAW_FUNC`, `QUAN_TRACE`, `SEC_TRACE`, `SEC_EXIST`, and `METH_PEAK`, including
repeated occurrences. There is no supported override for this protection.

Editing requires `read()` or `open_qld()` to retain the original container;
constructing a `QLDFile` from parsed tables alone is not sufficient. No row
creation/deletion, raw trace editing, recalculation, or vendor audit/signature
generation is provided. Native TargetLynx acceptance is unverified. See
[editing guidance](usage.md#edit-and-save-qld) for preservation limits and
scientific cautions.

### Record Fields

`Sample`, `Compound`, `MethodCompound`, and `QuantitationResult` are read-only
`Mapping` objects. Iteration, indexing, attribute fallback, `get()`, and
`to_dict()` use snake_case names. `raw_fields` is a read-only mapping of the
original vendor names for diagnostics.

Common convenience properties:

| Class | Properties |
| --- | --- |
| `Sample` | `id`, `file_name`, `description`, `sample_type`, `vial`, `injection_volume` |
| `Compound` | `name`, `cas_number`, `units`, `function`, `quantifier_transition`, `qualifier_transition`, `expected_retention_time` |
| `QuantitationResult` | `sample_id`, `analyte_name`, `function`, `transition`, `area`, `height`, `retention_time`, `concentration`, `response`, `integration`, `quality` |
| `Chromatogram` | `function`, `trace`, `times`, `intensities`, metadata properties, iteration over `(time, intensity)` |
| `Spectrum` | `function`, `time`, `points`, `masses`, `intensities`, iteration over `(mass, intensity)` |

### Domain values

* `Integration`: peak boundaries, scans, area, and height.
* `Quality`: quality score, noise, ion ratio, limits, and named failure flags.
* `AcquisitionInfo`: sample ID, instrument, acquisition time, parsed report,
    function count, preserved binary header, and `mrm_channels`.
* `MRMTransition`: immutable `precursor_mz` and `product_mz` as positive, finite
    `Decimal` values. `MRMTransition.parse(label)` returns a transition for a
    numeric `precursor > product` label, or `None` for other syntax. Numeric
    nonpositive masses raise `ValueError`; construction requires `Decimal`
    values, not binary floats. Equality and hashing ignore decimal formatting
    without applying a mass tolerance.
* `AcquisitionChannel`: immutable `function`, `number`, `transition`,
    `ionization_mode`, and `collision_energy` (`Decimal`) from a recognized
    embedded MRM acquisition table. `number` is the acquisition channel number,
    not a quantifier/qualifier designation. Missing ionization mode is `""`.
* `CalibrationCurve` and `CalibrationPoint`: curve model, weighting,
    coefficients, validity, concentrations, and responses.
* `AuditEvent`: UTC timestamp, action, user text, sample/peak indexes, changed
    integration boundaries, and signature state.
* `CompoundResult`: results, primary result, chromatograms, spectra, and common
    area/height/retention-time shortcuts for one sample and compound.

`Peak` and `SampleCompound` remain compatibility aliases for
`QuantitationResult` and `CompoundResult`.

`AcquisitionInfo.mrm_channels` and `AcquisitionReport.mrm_channels` return
`tuple[AcquisitionChannel, ...]`. These lazily parse recognized `Function N` /
`MRM of N channels` acquisition tables and reject malformed, incomplete, or
duplicate tables with `ValueError`. Unrecognized reports return an empty tuple;
a missing function is not evidence that its transition was validated.

Pair-level chromatogram lookups use sample + function + exact numeric MRM
identity, or a text key for non-MRM traces. They validate against the embedded
acquisition table when one is recognized for that function. Ambiguous stored
traces, ambiguous acquisition channels, and acquisition conflicts raise
`ValueError` with sample/function/trace context. Missing traces return `None`
through individual accessors and are omitted from the pair's list, without
changing quantifier/qualifier roles. Raw `Dataset.chromatograms` still retains
all stored traces. See [matching details](usage.md#numeric-mrm-matching-and-acquisition-evidence).

## File and export API

### `open_qld(path) -> QLDFile`

Return the decoded streams and parsed low-level structures without building the
object-model indexes.

### `QLDFile`

* `path`: resolved source path as a string.
* `streams`: stream basename to decoded plaintext bytes.
* `tables`: stream basename to `DBFTable` or `list[IniSection]`.
* `stream_names()`: decoded names in OLE directory order.
* `label_for(stream)`: stable human-readable export label.
* `to_dict(include_raw=False)`: JSON-ready representation.

### JSON functions

* `to_json(qld, indent=None, include_raw=False) -> str`
* `export_json(qld_path, json_path, indent=None, include_raw=False) -> Path`
* `export_dir(qld_path, out_dir, indent=None, include_raw=False,
  split_sections=False, split_threshold=50) -> Path`

## Parser API

* `decode_quan_stream(data) -> bytes`: LZSS decompress, then XOR decrypt.
* `lzss_decode(data) -> bytes`: decompress only.
* `decrypt(data, key=0x65) -> bytes`: XOR transform only.
* `parse_dbf(data) -> DBFTable`: parse a decoded QD1-QD4 table.
* `parse_ini(data) -> list[IniSection]`: parse decoded QD5-QD8/audit data.

`DBFTable.records` is a list of dictionaries. `IniSection.items` is an ordered
list of key/value pairs so repeated keys are preserved. `IniSection.to_dict()`
promotes repeated keys to lists. `IniBlob.payload_kind` is `text`, `float32`, or
`bytes`.

The parser-level functions are public for research and custom extraction, but
normal applications should use `read()`.