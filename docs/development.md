# Architecture and Development

## Decode path

One call to `pyqld.read(path)` follows this path:

1. `qld._iter_ole_streams` opens the OLE2 container with `olefile` and selects
   `QuanData/.../DataStream.QD*` and `DataStream.qld` entries.
2. `codec.decode_quan_stream` expands each custom LZSS block and applies XOR
   key `0x65`.
3. `qld.open_qld` identifies the decoded payload by its dBase magic.
4. `dbf.parse_dbf` parses QD1-QD4; `ini.parse_ini` parses all other streams.
5. `model.Dataset` wraps records and builds sample, compound, peak-pair, and
   chromatogram lookup indexes.

`read(..., strict=True)` runs the shared compression checker before decoding
and enables strict DBF/INI parsing. Canonical stream identities prevent
case-related omissions and duplicate-stream overwrites. Unrecognized payloads
are retained as raw bytes. Default tolerant loading remains available, with
structural diagnostics exposed through `Dataset.validate()`.

The dependency direction is deliberately one-way:

```text
cli -> qld -> codec/dbf/ini
       model -> qld + dbf/ini
       __init__ -> public re-exports
```

## Module ownership

| Module | Responsibility |
| --- | --- |
| `codec.py` | LZSS blocks and XOR transformation |
| `dbf.py` | Decoded QD1-QD4 field descriptors and records |
| `ini.py` | Nested sections, scalar values, and binary blobs |
| `qld.py` | OLE traversal, stream dispatch, JSON serialization |
| `model.py` | Domain wrappers and cross-stream relationships |
| `validation.py` | Structural, ownership, signal and recognized metadata diagnostics |
| `interop.py`, `assays.py` | Optional MS adapters and scientific export policies |
| `cli.py` | Argument parsing and terminal output |
| `__init__.py` | Stable public API only |

The old root packages are not dependencies of `pyqld`. Do not copy fixes into
them unless maintaining a historical experiment.

Peak rows are indexed by `(resolved_sample_index, compound_name)`. Resolve
ownership from audited `Peaks/PeakList` row boundaries and raw-file identity;
use sample ID only when unique. Never split result rows equally to infer
ownership. Ambiguous labels must not collapse distinct injections.
Chromatograms are
indexed per sample by `(function, trace)`. Keep those indexes internal so the
public record order remains identical to stream order.

`validation.py` collects read-only diagnostics without changing tolerant
loading. Numerical models and diagnostics live in `quantitation.py`; stored
vendor calibration metadata remains in `domain.py`. Signal wrappers cache
immutable NumPy arrays on first use and rebuild those caches after pickling.

## Tests

```bash
cd pyqld
python -m pip install -e ".[dev]"
python -m pytest
ruff check pyqld tests
mypy pyqld
```

For optional interoperability tests, install
`".[dev,openms,matchms,interop-test]"` in a compatible interpreter. Native helper
stubs describe their verified positional C interfaces; runtime still falls back
to Python when the extensions are absent. When checking against newer NumPy
stubs, select the matching Python target, for example
`mypy --python-version 3.13 pyqld`.

Unit tests construct small codec, dBase, and INI payloads. `test_api.py` also
opens a repository fixture when both the fixture and `olefile` are available.
Keep synthetic tests for edge cases and at least one real-file check for changes
to stream traversal, parsing, or model relationships.

## Reader coverage and remaining gaps

The current corpus contains two QLD files: a 13-injection batch with spectra
and a 21-injection batch without spectra. Both pass strict structural reading,
and strict/tolerant decoded contents and signal arrays agree. This is not a
vendor-version compatibility matrix.

The reader now covers:

* Compression integrity checks shared by strict reading and validation, with
  equivalent native/Python acceptance rules.
* DBF header/descriptor/record bounds, explicit recovery diagnostics, unique
  duplicate-column keys and non-lossy snake-case alias enumeration. Unsupported
  field types no longer hide later columns; they are retained as text with
  diagnostics, while strict reads refuse their unknown semantics.
* CR/LF/CRLF and mixed INI line endings, progress on bare CR, bounded-line section
  headers, float32 blob alignment, and conservative unknown binary preservation.
* Canonical stream keys, duplicate-stream rejection, rejection of unrelated OLE
  containers, and raw export of opaque future streams.
* Real-file read, edit/save/reopen, quantitation and preservation tests using the
  actual repository fixture paths, rather than silently skipping those checks.

Still unresolved or intentionally limited:

| Area | Remaining work / evidence needed |
| --- | --- |
| QD8 and future streams | Both current QD8 streams are empty. Obtain nonempty examples before assigning meanings; opaque bytes are preserved. |
| QD7 spectrum meaning | Arrays/function/RT are readable, but MS level, centroid/profile state, precursor and scan completeness are not established by QD7 alone. Compare with vendor exports and original acquisition data. |
| Media ownership | Audited boundaries are supported. Without them, equal-group ownership is still an explicitly reported heuristic; no trustworthy missing foreign key has been discovered. |
| Acquisition reports | Recognized MRM tables are typed. LC/tune/calibration details and non-MRM settings remain report text; broader vendor-version examples are needed. |
| Calibration and flags | Stored coefficients, points and raw fields are exposed. Undocumented model/origin/status/quality codes and vendor calculation equivalence need reference outputs. |
| Character encoding | DBF text keeps the existing ASCII replacement behavior; original decoded bytes remain in `QLDFile.streams`. A vendor/code-page corpus is needed before selecting a different encoding. |
| Unknown DBF field types | Width-based text recovery does not establish a date/binary/numeric interpretation. Strict mode refuses these types and editing them is unsupported. |
| Scale | Loading is eager, and validation reparses retained structural data. Large-file/many-batch memory and throughput need measurement before a streaming rewrite. |
| Vendor compatibility | Two files cannot establish behavior across TargetLynx versions, all acquisition modes, or native acceptance of rewritten files. |

Use both checks for scientific workflows:

```python
dataset = pyqld.read("batch.qld", strict=True)
report = dataset.validate()
report.raise_for_errors()
print(report.warnings)
```

Strict reading is structural validation, not a certificate of complete scans,
correct vendor interpretation, or unambiguous ownership.

## Release check

```bash
python -m pytest
ruff check pyqld tests
mypy pyqld
python -m pip wheel . --no-deps --wheel-dir dist
python -m venv /tmp/pyqld-check
/tmp/pyqld-check/bin/pip install dist/pyqld-*.whl
/tmp/pyqld-check/bin/pyqld --help
```

Before changing a decoded field interpretation, compare it against more than
one real file. Vendor versions and acquisition methods can change optional
fields while preserving the stream envelope.
