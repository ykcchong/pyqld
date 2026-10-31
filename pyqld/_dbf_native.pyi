from typing import Any

def parse_records(
    data: bytes,
    offset: int,
    stride: int,
    count: int,
    layout: list[tuple[str, int, int, str, int]],
    /,
) -> list[dict[str, Any]]: ...
