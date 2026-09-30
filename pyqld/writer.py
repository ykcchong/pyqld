"""Copy-on-write editing of existing QLD table cells and OLE containers."""

from __future__ import annotations

import io
import struct
from typing import Any

from .codec import decrypt
from .dbf import parse_dbf

PROTECTED_FIELDS = frozenset({
    "ID", "SAMPLE_ID", "NAME", "FILE_NAME", "RAW_NAME", "RAW_FUNC",
    "QUAN_TRACE", "SEC_TRACE", "SEC_EXIST", "METH_PEAK",
})


def edit_table(data: bytes, index: int, changes: dict[str, Any]) -> bytes:
    """Patch existing cells, preserving all bytes outside the edited fields."""
    return edit_table_rows(data, {index: changes})


def edit_table_rows(data: bytes, changes: dict[int, dict[str, Any]]) -> bytes:
    """Validate and patch several rows atomically, parsing the table once."""
    table = parse_dbf(data)
    header_size, record_size = struct.unpack_from("<HH", data, 8)
    minimum_header = 32 + len(table.fields) * 32 + 1
    minimum_record = 1 + sum(field.width for field in table.fields)
    if (header_size < minimum_header or record_size < minimum_record
            or header_size + table.nrecs * record_size > len(data)):
        raise ValueError("editing requires a complete table with valid size headers")
    fields = {field.key.lower(): field for field in table.fields}
    output = bytearray(data)
    for index, values in changes.items():
        if type(index) is not int or not 0 <= index < len(table.records):
            raise IndexError(index)
        replacements = {}
        for key, value in values.items():
            field = fields.get(key.lower())
            if field is None:
                raise KeyError(key)
            if field.name in PROTECTED_FIELDS:
                raise ValueError(f"{field.key} is a protected relationship field")
            replacements[field.key] = field.encode(value)
        offset = header_size + index * record_size + 1
        for field in table.fields:
            if field.key in replacements:
                output[offset:offset + field.width] = replacements[field.key]
            offset += field.width
    return bytes(output)


def encode_quan_stream(data: bytes) -> bytes:
    """Encode XOR/LZSS literal blocks; edited streams may grow in size."""
    blocks = []
    for offset in range(0, len(data), 65536):
        payload = decrypt(data[offset:offset + 65536])
        flags = bytes((len(payload) + 3) // 4)
        blocks.append(struct.pack("<I", len(flags)) + flags
                      + struct.pack("<I", len(payload)) + payload)
    return b"".join(blocks)


def replace_streams(source: bytes, replacements: dict[str, bytes]) -> bytes:
    """Rebuild a version-3 CFB, preserving directory metadata and other streams."""
    import olefile

    free, end, fat_marker, difat_marker = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
    sectors: list[bytes] = []
    fat: list[int] = []

    def allocate(payload: bytes) -> int:
        if not payload:
            return end
        start = len(sectors)
        count = (len(payload) + 511) // 512
        for number in range(count):
            sectors.append(payload[number * 512:(number + 1) * 512].ljust(512, b"\0"))
            fat.append(start + number + 1 if number + 1 < count else end)
        return start

    with olefile.OleFileIO(io.BytesIO(source)) as ole:
        if ole.sectorsize != 512 or ole.minisectorsize != 64:
            raise ValueError("writing supports only version-3 OLE containers")
        paths = ole.listdir(streams=True, storages=False)
        streams = {tuple(path): ole.openstream(path).read() for path in paths}
        directory = bytearray(ole.directory_fp.getvalue())
        mini_data = bytearray()
        mini_fat: list[int] = []
        applied = set()
        for path, original in streams.items():
            name = "/".join(path)
            payload = replacements.get(name, original)
            if name in replacements:
                applied.add(name)
            entry = ole.root
            for part in path:
                entry = entry.kids_dict[part.lower()]
            if 0 < len(payload) < 4096:
                start = len(mini_fat)
                count = (len(payload) + 63) // 64
                mini_data.extend(payload.ljust(count * 64, b"\0"))
                mini_fat.extend(start + number + 1 if number + 1 < count else end
                                for number in range(count))
            else:
                start = allocate(payload)
            struct.pack_into("<IQ", directory, entry.sid * 128 + 116, start, len(payload))
        if applied != set(replacements):
            raise ValueError("replacement stream not found in original container")
        root_start = allocate(bytes(mini_data))
        struct.pack_into("<IQ", directory, 116, root_start, len(mini_data))
        mini_fat_start = allocate(
            b"".join(struct.pack("<I", value) for value in mini_fat).ljust(
                ((len(mini_fat) + 127) // 128) * 512, b"\xff"
            )
        )
        directory_start = allocate(bytes(directory))

    data_count = len(sectors)
    fat_count, difat_count = 0, 0
    while True:
        next_fat = (data_count + fat_count + difat_count + 127) // 128
        next_difat = max(0, (next_fat - 109 + 126) // 127)
        if (next_fat, next_difat) == (fat_count, difat_count):
            break
        fat_count, difat_count = next_fat, next_difat
    fat_ids = list(range(data_count, data_count + fat_count))
    difat_ids = list(range(data_count + fat_count, data_count + fat_count + difat_count))
    fat.extend([fat_marker] * fat_count + [difat_marker] * difat_count)
    fat.extend([free] * (fat_count * 128 - len(fat)))
    for offset in range(0, len(fat), 128):
        sectors.append(struct.pack("<128I", *fat[offset:offset + 128]))
    extra_fat = fat_ids[109:]
    for number in range(difat_count):
        values = extra_fat[number * 127:(number + 1) * 127]
        values += [free] * (127 - len(values))
        values.append(difat_ids[number + 1] if number + 1 < difat_count else end)
        sectors.append(struct.pack("<128I", *values))
    header = bytearray(source[:512])
    struct.pack_into("<IIII", header, 40, 0, fat_count, directory_start, 0)
    struct.pack_into("<IIIII", header, 56, 4096, mini_fat_start,
                     (len(mini_fat) + 127) // 128,
                     difat_ids[0] if difat_ids else end, difat_count)
    struct.pack_into("<109I", header, 76, *(fat_ids[:109] + [free] * max(0, 109 - fat_count)))
    return bytes(header) + b"".join(sectors)


