"""LZSS + XOR codec for Waters TargetLynx ``.qld`` substreams.

Decode pipeline
---------------

.. code-block::text

    raw stream bytes  --(LZSS decompress)-->  encrypted  --(XOR 0x65)-->  plaintext

The LZSS decoder uses a custom block format with 2-bit token codes.
The XOR layer uses a single-byte key ``0x65`` (ASCII ``'e'``).

See :doc:`../docs/format` for the full reverse-engineering write-up.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

#: The single-byte XOR key used by ``CQuanFileEncrypt`` (ASCII ``'e'``).
XOR_KEY: int = 0x65


# ---------------------------------------------------------------------------
# LZSS block decoder
# ---------------------------------------------------------------------------


def lzss_decode_block(
    flag_buf: bytes,
    token_buf: bytes,
    out: bytearray,
) -> int:
    """Decode one LZSS block, appending bytes to ``out``.

    Returns the number of bytes consumed from ``token_buf``.

    The flag buffer encodes 2 bits per token (4 tokens per byte, LSB first).
    Token codes:

    * ``0``      — literal: emit ``*token_ptr++``
    * ``1`` / ``3`` — short back-ref: ``dist (u8), len (u8)``; copy ``len``
      bytes from ``output[-dist]``
    * ``2``      — long back-ref: ``dist (u16 LE), len (u8)``; copy ``len``
      bytes from ``output[-dist]``
    """
    ntoks = len(flag_buf) * 4
    tp = 0
    tlen = len(token_buf)

    for bit_index in range(ntoks):
        byte = flag_buf[bit_index >> 2]
        shift = (bit_index & 3) * 2
        code = (byte >> shift) & 3

        if code == 0:
            if tp >= tlen:
                break
            out.append(token_buf[tp])
            tp += 1

        else:
            if code == 2:
                if tp + 2 >= tlen:
                    break
                distance = token_buf[tp] | (token_buf[tp + 1] << 8)
                tp += 2
            else:  # code 1 or 3
                if tp + 1 >= tlen:
                    break
                distance = token_buf[tp]
                tp += 1

            if tp >= tlen:
                break
            length = token_buf[tp]
            tp += 1

            src = len(out) - distance
            if src < 0:
                src = 0  # guard against corrupt distance values
            for k in range(length):
                if src + k < len(out):
                    out.append(out[src + k])

    return tp


def iter_blocks(data: bytes) -> Iterator[tuple[int, bytes, bytes]]:
    """Yield ``(block_offset, flag_buf, token_buf)`` for each LZSS block."""
    off = 0
    n = len(data)

    while off + 4 <= n:
        sz1 = struct.unpack_from("<I", data, off)[0]
        if sz1 == 0 or off + 4 + sz1 + 4 > n:
            return
        flag_off = off + 4
        flag_buf = data[flag_off : flag_off + sz1]
        sz2_off = flag_off + sz1
        sz2 = struct.unpack_from("<I", data, sz2_off)[0]
        tok_off = sz2_off + 4
        if tok_off + sz2 > n:
            return
        token_buf = data[tok_off : tok_off + sz2]
        yield off, flag_buf, token_buf
        off = tok_off + sz2


# ---------------------------------------------------------------------------
# Full stream decoders
# ---------------------------------------------------------------------------


def lzss_decode(data: bytes) -> bytes:
    """Decompress the LZSS layer of an entire stream payload.

    Parameters
    ----------
    data : bytes
        The raw (compressed) stream bytes from an OLE2 substream.

    Returns
    -------
    bytes
        The LZSS-decompressed (still XOR-encrypted) data.
    """
    out = bytearray()
    for _off, flag_buf, token_buf in iter_blocks(data):
        lzss_decode_block(flag_buf, token_buf, out)
    return bytes(out)


def decrypt(buf: bytes, key: int = XOR_KEY) -> bytes:
    """Apply the ``CQuanFileEncrypt`` XOR cipher.

    Parameters
    ----------
    buf : bytes
        The LZSS-decompressed data.
    key : int, optional
        The XOR key (default ``0x65``).

    Returns
    -------
    bytes
        The plaintext data.
    """
    return bytes(b ^ key for b in buf)


def decode_quan_stream(data: bytes) -> bytes:
    """Full decode of one ``QuanData/DataStream.QDx`` substream.

    Applies LZSS decompression followed by XOR decryption.

    Parameters
    ----------
    data : bytes
        The raw compressed+encrypted stream bytes.

    Returns
    -------
    bytes
        The fully decoded plaintext (dBase or INI content).
    """
    return decrypt(lzss_decode(data))
