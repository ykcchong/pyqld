# pyqld

Read Waters TargetLynx `.qld` quantitation files on any platform — no Windows, MFC, or proprietary runtime required. pyqld is an independent interoperability project and is not affiliated with or endorsed by Waters Corporation.

## Overview

The `.qld` file is a proprietary binary container used by Waters TargetLynx to store quantitation results from LC-MS/MS analyses. It is a Microsoft OLE2 Compound Document whose substreams are LZSS-compressed and XOR "encrypted".

`pyqld` fully decodes these files and exposes the data through:

* A **high-level object model** (`Dataset`, `Sample`, `Compound`, `Peak`, `Chromatogram`, `Spectrum`) with attribute access
* A **JSON exporter** (single file or directory tree)
* A **CLI** for quick inspection and export

## Installation

Clone the project. Then `pip install .`

Requires Python 3.10+ and the `olefile` package (installed automatically).

## Quick start

```python
from pyqld import Dataset

ds = Dataset.from_path("datafile.qld")
print(ds)  # <Dataset samples=13 compounds=146 peaks=...>

# Lookup by name or index
sample = ds.sample("sample")
compound = ds.compound("compound")

# Get a (sample, compound) pair
pair = ds.pair(sample, compound)
print(pair.area, pair.height, pair.retention_time)

# Access chromatogram data
for ch in pair.chromatograms:
    print(ch.trace, len(ch.times), len(ch.intensities))

# Access spectra
for sp in pair.spectra(rt_window=0.2):
    print(sp.time, len(sp.masses))

# Iterate all pairs
n_detected = sum(1 for p in ds.pairs() if p.area and p.area > 0)
```

## API reference

### High-level I/O

* **`open_qld(path)`** — read a `.qld` file into a `QLDFile`
* **`to_json(qld, indent=None)`** — serialize to JSON string
* **`export_json(qld_path, json_path)`** — write JSON to disk
* **`export_dir(qld_path, out_dir, split_sections=False)`** — directory tree export

### Object model

* **`Dataset.from_path(path)`** — high-level wrapper with attribute access
* **`Dataset.sample(key)`** — lookup by index, ID, or file name
* **`Dataset.compound(key)`** — lookup by index or name
* **`Dataset.pair(sample, compound)`** — get a `SampleCompound` join view
* **`Dataset.pairs()`** — iterate all (sample, compound) pairs
* **`Dataset.method`** — analytical method view (method compounds + audit)

### `SampleCompound`

* **`.peaks`** — list of `Peak` for this pair
* **`.primary_peak`** — peak with `METH_PEAK == 0`
* **`.area`**, **`.height`**, **`.retention_time`** — from primary peak
* **`.chromatograms`** — list of `Chromatogram` (quan, then qual)
* **`.quan_chromatogram`**, **`.qual_chromatogram`**
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
| `qld` | audit | `[Header]` audit-trail sections (INI) |

Each substream is **LZSS-compressed** then **XOR-encrypted** with key `0x65`.

See [docs/format.md](docs/format.md) for the full format specification.

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
