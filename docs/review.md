# Codebase Review

## Decision

`pyqld` is the canonical implementation. It is the only package in this
repository that combines the verified LZSS/XOR decoder, dBase and INI parsers,
a domain model, a CLI, packaging metadata, and focused tests.

The root `qld_parser` package duplicates early versions of the same layers.
`targetlynx_parser` remains useful for exported XML, TXT, MDB, and CDB files,
but its QLD exploration path predates the working decoder. `old` contains
reverse-engineering artifacts. None of these are imported by `pyqld`.

## Supported data path

```text
.qld OLE2 file
  -> selected QuanData streams
  -> custom LZSS decompression
  -> XOR 0x65 decryption
  -> dBase or nested INI parsing
  -> Dataset indexes and record wrappers
  -> Python API, JSON, or CLI
```

Keeping this path in one package avoids format logic drifting between parser
copies. New QLD fixes belong in `pyqld` with a synthetic regression test and,
when possible, a real-file check.

## Verified behavior

The repository fixture `data/UMP 20260508 PS.qld` decodes to:

| Data | Count |
| --- | ---: |
| Samples | 13 |
| Compounds | 146 |
| Peak rows | 7,446 |
| Chromatograms | 1,898 |
| Spectra | 1,333 |

Of the peak rows, 1,924 have a named compound matching QD3 and participate in
the high-level `Dataset.pairs()` view. The remaining rows have an empty compound
name and remain available through `Dataset.peaks` and the raw QD2 table.

The real fixture contains QD1-QD8 plus the `qld` audit stream. QD8 is empty in
the observed file but is not filtered out by the reader.

## Simplifications made

* `pyqld.read(path)` is the single recommended application entry point.
* Peak lookup is indexed once by sample and compound rather than rescanning QD2
  for every pair.
* Legacy packages are clearly marked and excluded from the supported runtime
  path.
* Installation, usage, API, format, architecture, testing, and release steps
  are documented alongside the package.

## Remaining risks

* The format is reverse-engineered and tested against a limited set of vendor
  files. Unknown TargetLynx versions may contain new fields or blob types.
* Parsing is eager, so memory use scales with decoded chromatograms and spectra.
* Chromatogram association relies on sample-major ordering observed in current
  fixtures.
* Spectrum association is by function and optional retention-time window, not
  by a vendor-provided sample foreign key.
* The low-level codec tolerates truncated token buffers by returning decoded
  output up to the truncation point. Strict corruption reporting would be a
  separate compatibility change.
* XML/TXT/MDB/CDB support remains in the legacy package and is intentionally not
  folded into the direct QLD reader.