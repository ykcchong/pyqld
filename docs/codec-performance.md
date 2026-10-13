# Codec acceleration

The original decoder performs a Python iteration for every token and every byte
copied by a back-reference. `bytes.translate` accelerates only the final XOR pass;
it leaves the expensive decompression loop unchanged. Overlapping references
depend on bytes just emitted, so this is not a straightforward NumPy operation.

## Options

| Approach | Assessment for this codec |
| --- | --- |
| Python slice copying/repetition | Can replace the innermost copy loop without build dependencies. Token dispatch remains in Python, and overlapping references need explicit repetition. |
| Numba | Can compile the loops, but introduces a JIT/runtime dependency and compilation or cache startup costs. A warmed implementation may be competitive with C; no Numba timing is claimed here. |
| Cython | A practical compiled alternative with typed loops; introduces code generation/build tooling or checked-in generated C. |
| Small C extension | Selected: the codec is small enough to implement directly, with no new runtime dependency or JIT warmup. |
| Rust extension | Also viable native code, but adds a Rust toolchain and Python binding layer for this small routine. |
| Parallel decoding | Separate streams could be processed independently; blocks within a stream share output history. Process overhead should be measured before adding complexity. The current C extension retains the GIL. |

[Cython's compilation documentation](https://docs.cython.org/en/latest/src/quickstart/build.html)
describes its generated C extension workflow.
[Setuptools' extension documentation](https://setuptools.pypa.io/en/latest/userguide/ext_modules.html)
describes the optional extension build used here.

## Implementation

`lzss_decode` dispatches to `_codec_native.decode` when available;
`decode_quan_stream` uses the same native decoder with XOR enabled. Literal bytes
are decrypted as they are emitted. References then copy decrypted history, which
is equivalent to decrypting the whole output afterward. This removes a full
output traversal and intermediate decoded `bytes` object from the combined path.

The decoder parses the original stream directly, avoiding flag/token slices. It
uses a geometrically growing buffer, `memcpy` for non-overlapping copies, and
forward byte copying for overlapping references. It preserves the original
clamping of excessive distances, zero-distance behavior, and partial output for
truncated blocks/tokens. Bounds and allocation arithmetic are checked before
reading or growing buffers.

The original Python block decoder and standalone `decrypt` remain available.
The Python stream implementation is retained as a fallback and test oracle.
Bytes inputs use the extension; other previously accepted buffer inputs use the
Python path. No public function signatures change.

## Reproducing measurements

From the package checkout:

```bash
python -m pip install -e '.[dev]'
python examples/benchmark_codec.py '../UMP 20260508 PS.qld' --repeat 5
python -m pytest -q
```

The benchmark reads the OLE streams before timing, verifies every result against
the original decoder, and reports median time across all streams. These are codec
times, not whole-file parsing times. Native performance requires a successful
extension build; the benchmark only lists native methods when it can import it.

Measured on 2026-10-01, macOS ARM64, CPython 3.14.4, Apple Clang with `-O3`:
9 streams, 16,925,058 compressed bytes, 45,038,343 decoded bytes. Medians of
three runs, with no concurrent test run:

| Decoder | Time |
| --- | ---: |
| Original Python LZSS | 2635.6 ms |
| Original Python LZSS + Python XOR | 3367.0 ms |
| Original Python LZSS + `bytes.translate` | 2627.6 ms |
| Native LZSS | 25.8 ms |
| Native LZSS + fused XOR | 26.4 ms |

The combined native path was approximately 99× faster than Python LZSS plus
`translate`, and 127× faster than the original combined path on this fixture.
Results depend on stream contents, hardware, and Python version. A warmed Numba
implementation has not been benchmarked in this comparison.

Validation: 83 package tests passed with the extension; codec tests also passed
with native dispatch disabled (16 passed, 2 native-only tests skipped). Native
parity tests cover 1,000 seeded randomized inputs, overlapping and cross-block
references, distances above 255, malformed distances, and truncation at every
offset of the explicit fixtures. Source-to-wheel builds were verified with a
working compiler and with compilation deliberately disabled. Other platforms
have not been tested locally.

## Follow-up: parsing after decompression

Profiling the same fixture with native decoding found about 1.9 million
field-name normalization calls. The record and domain normalizers now each use
a bounded 4,096-entry cache. DBF parsing also computes field keys/boundaries once
per table and decodes ASCII once per record, instead of once per cell. ASCII
replacement preserves the one-character-per-byte offsets for malformed input.

An interleaved before/after comparison on the same CPython 3.14 environment
(three runs per configuration, normalization caches cleared before each run)
reduced the Flask `/api/upload` test-client median from **1.993 s to 0.727 s**.
Both configurations used the native codec. This includes multipart handling,
temporary-file I/O, full parsing, dataset construction, and response generation;
it excludes network transfer and browser rendering. All parsed table contents
and upload responses matched, apart from generated session IDs. The package
suite, additional malformed/truncated DBF tests, and web server tests passed.

## Shared row schemas, report headers, and native DBF conversion

The next pass shares immutable alias maps between records with identical ordered
keys (a bounded 256-schema cache). Keeping the order in the cache key preserves
the existing last-key-wins behavior when normalized names collide. Deleted rows
and heterogeneous rows naturally use different schemas.

Acquisition reports now check for the `--` section prefix before applying a
compiled header regex. Report parsing remains eager and preserves the original
section/duplicate-value rules.

The optional `_dbf_native` extension converts DBF rows in C, with checked bounds
and Python's own integer/float conversions. It preserves ASCII replacement, blank
values, duplicate columns, deleted rows, clipped fields, and malformed numeric
fallbacks. Header parsing stays in Python; installations without the extension
continue to use the Python row loop.

On the same fixture and CPython 3.14.4, five interleaved upload-handler runs per
configuration, with caches cleared before each run, gave these medians:

| Configuration | Upload-handler time |
| --- | ---: |
| Before these three changes (native LZSS already enabled) | 645.3 ms |
| Shared row schemas | 553.0 ms |
| Shared schemas + report header guard | 524.4 ms |
| All three, including native DBF conversion | 282.7 ms |

All table contents, acquisition reports, and upload responses matched the
preceding implementation (excluding temporary paths/session IDs). The combined
package/web suite passed 91 tests. DBF fallback tests passed with its extension
disabled (19 passed, 2 native-only tests skipped). Timings exclude network and
browser rendering and are fixture-specific.

Both extensions were also built for the GUI's Python 3.13.13 environment. The
same 91 tests passed there; five upload-handler runs with cold normalization and
schema caches had a median of **353.3 ms**. Source-to-wheel packaging was verified
with both extensions included. A running server must restart to load the changes.
