# pyqld

Read Waters TargetLynx `.qld` quantitation files on any platform — no Windows, MFC, or proprietary runtime required. pyqld is an independent interoperability project and is not affiliated with or endorsed by Waters Corporation.

## Overview

The `.qld` file is a proprietary binary container used by Waters TargetLynx to store quantitation results from LC-MS/MS analyses. It is a Microsoft OLE2 Compound Document whose substreams are LZSS-compressed and XOR "encrypted".

`pyqld` decodes the supported stream structures and exposes the data through:

* A **high-level object model** with snake_case mappings, typed result quality,
  integration, acquisition, calibration, and audit values
* A **JSON exporter** (single file or directory tree)
* A **CLI** for quick inspection and export

## Installation

From a checkout of this repository:

```bash
python -m pip install ./pyqld
```

To build a wheel for direct distribution:

```bash
cd pyqld
python -m pip wheel . --no-deps --wheel-dir dist
```

Requires Python 3.10+, `olefile` and NumPy (installed automatically).

Optional interoperability extras are available as `pyqld[openms]` and
`pyqld[matchms]`, subject to those engines' Python/platform support. Export
retained MRM traces to pyOpenMS/mzML, explicitly described assays to TraML or
Skyline CSV, and linked quantitation/provenance JSON without rewriting the QLD.
Spectrum export requires externally verified metadata; it never reconstructs
missing raw scans. See [MS interoperability](docs/usage.md#interoperate-with-ms-software).

Installation also attempts to build small C extensions that accelerate LZSS
decoding (combined with XOR decryption) and DBF cell conversion. Source builds
need a C compiler and Python development headers for acceleration. If compilation
is unavailable, the package automatically uses the Python implementations.
Compiled wheels are specific to the target Python version and platform.
See [codec performance](docs/codec-performance.md)
for the implementation tradeoffs and a reproducible benchmark.

## Quick start

```python
import pyqld

ds = pyqld.read("datafile.qld")
print(ds)  # <Dataset samples=13 compounds=146 peaks=...>

# Lookup by name or index
sample = ds.sample("sample")
compound = ds.compound("compound")

# Get a (sample, compound) result
pair = ds[sample, compound]
print(pair.area, pair.height, pair.retention_time)
print(pair.result.integration, pair.result.quality)

# Filter even unnamed QD2 rows by transition
rows = ds.results_for(sample, transition=compound.quantifier_transition)

# Access chromatogram data
for chromatogram in pair.chromatograms:
    for time, intensity in chromatogram:
        pass

# Access spectra
for sp in pair.spectra(rt_window=0.2):
    print(sp.time, len(sp.masses))

# Iterate all pairs
n_detected = sum(1 for p in ds.pairs() if p.area and p.area > 0)
```

The entire file is decoded eagerly. The returned object does not hold an open
file handle and does not require a context manager.

For structural integrity checks while loading, use `pyqld.read(path, strict=True)`
or the CLI's `--strict` flag. Default loading remains tolerant for recovery;
inspect `dataset.validate()` before scientific use. Unknown binary payloads
are preserved, not assigned guessed meanings. See
[reader coverage and remaining gaps](docs/development.md#reader-coverage-and-remaining-gaps).

Sample labels may repeat across injections: use a unique file name or index
when lookup by ID is ambiguous. Result convenience properties require a unique
active primary; use `pair.selection_status` and `pair.select_result(...)` for
explicit selection. See [result selection](docs/api.md#result-selection).

```python
report = ds.validate()
report.raise_for_errors()

for trace in ds.chromatograms_for(sample, function=compound.function):
    times, intensities = trace.to_numpy()  # cached read-only NumPy arrays
    window_times, window_values = trace.time_window(1.0, 1.5)  # minutes
```

For explicit calibration fitting, reintegration and requantitation, see
[numerical workflows](docs/usage.md#fit-and-apply-an-explicit-calibration).

## Edit A Copy

```python
edited = ds.with_changes(ds.samples[0], file_text="Reviewed sample")
edited.save("reviewed.qld")
```

Existing sample, compound, method, and result fields can be edited with width
and type validation. Relationship fields are protected. Saving preserves
unmodified streams and refuses existing destinations by default. This does not
recalculate quantitation or add vendor audit records; native TargetLynx
compatibility is not yet verified. See [editing details](docs/usage.md#edit-and-save-qld).

## Command line

```bash
pyqld datafile.qld --list
pyqld datafile.qld datafile.json --indent 2
pyqld datafile.qld --out-dir exported --split-sections --indent 2
```

## API reference

### High-level I/O

* **`read(path)`** — preferred API; return a navigable `Dataset`
* **`open_qld(path)`** — read a `.qld` file into a `QLDFile`
* **`to_json(qld, indent=None)`** — serialize to JSON string
* **`export_json(qld_path, json_path)`** — write JSON to disk
* **`export_dir(qld_path, out_dir, split_sections=False)`** — directory tree export

### Object model

* **`Dataset.from_path(path)`** — high-level wrapper with attribute access
* **`Dataset.sample(key)`** — lookup by index, ID, or file name
* **`Dataset.compound(key)`** — lookup by index or name
* **`dataset[sample, compound]`** — get a `CompoundResult` view
* **`Dataset.results_for(...)`** — filter named or unnamed quantitation results
* **`Dataset.pairs()`** — iterate all (sample, compound) pairs
* **`Dataset.acquisition(sample)`** — parsed acquisition metadata
* **`Dataset.method`** — calibrations, method compounds, and audit events

Acquisitions expose numbered functions and MRM channels directly:

```python
acquisition = ds.acquisition(sample)
if acquisition is not None:
    for function in acquisition.functions:
        print(function.number, function.start_time, function.end_time)  # minutes
        for channel in function.mrm_channels:
            print(channel.transition, channel.dwell_time, channel.collision_energy)
```

Use `acquisition.function(number).channel(number)` for vendor-number lookup or
`acquisition.channels_for(function=..., transition="568.2>347.2")` to filter.
The typed API covers recognized MRM reports; other settings remain available in
the preserved report. See [acquisition coverage and examples](docs/usage.md#acquisition-metadata).

### `CompoundResult`

* **`.results`** — list of `QuantitationResult` objects for this pair
* **`.result`** — primary quantitation result
* **`.area`**, **`.height`**, **`.retention_time`** — from primary peak
* **`.chromatograms`** — list of `Chromatogram` (quan, then qual)
* **`.quantifier_chromatogram`**, **`.qualifier_chromatogram`**
* **`.spectra(rt_window=None)`** — matching spectra, optionally filtered by RT

### Low-level parsers

* **`parse_dbf(data)`** — parse dBase-III bytes into `DBFTable`
* **`parse_ini(data)`** — parse INI bytes into `list[IniSection]`
* **`decode_quan_stream(data)`** — full LZSS + XOR decode

## File format

A `.qld` file is an OLE2 Compound Document containing:

| Stream | Label | Content |
|--------|-------|---------|
| `QD1` | samples | Sample list (dBase table) |
| `QD2` | peaks | Peak results (dBase table) |
| `QD3` | compounds | Compound definitions (dBase table) |
| `QD4` | method_compounds | Method compound parameters (dBase table) |
| `QD5` | chromatograms | `[Chromatogram]` trace sections (INI) |
| `QD6` | sample_info | `[SampleInfo]` + acquisition headers (INI) |
| `QD7` | spectra | `[Spectrum]` mass+intensity sections (INI) |
| `QD8` | qd8 | Reserved/unknown INI stream; retained when present |
| `qld` | audit | `[Header]` audit-trail sections (INI) |

Each substream is **LZSS-compressed** then **XOR-encrypted** with key `0x65`.

## Documentation

* [User guide](docs/usage.md) — installation, lookup, chromatograms, spectra, export, and errors
* [API reference](docs/api.md) — public classes, methods, and return values
* [File format](docs/format.md) — OLE streams, LZSS/XOR codec, dBase, and INI layouts
* [Development guide](docs/development.md) — architecture, module ownership, tests, and releases
* [Codebase review](docs/review.md) — consolidation decision, verified fixture, and remaining risks

The supported implementation is this package. The repository's
`targetlynx_parser`, `qld_parser`, and `old` directories are historical
reverse-engineering work and are not runtime dependencies.

## Development

```bash
git clone https://github.com/ykcchong/pyqld.git
cd pyqld
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check pyqld/

# Type check
mypy pyqld/
```

## License

MIT — see [LICENSE](LICENSE).
