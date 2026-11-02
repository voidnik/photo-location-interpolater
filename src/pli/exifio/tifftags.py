"""Just-enough TIFF parsing for EXIF tags ``piexif`` does not know about.

``piexif`` silently skips IFD entries that are missing from its own tag tables,
and that includes ``OffsetTimeOriginal`` (0x9011) -- the tag that says which
time zone the camera clock was in.  Losing it would push whole trips by hours,
so the handful of ASCII tags we need are read straight out of the IFDs here.
Everything in this module is strictly read-only and gives up quietly on any
structure it does not understand.
"""

from __future__ import annotations

import struct

from . import png as png_chunks

EXIF_SUB_IFD = 0x8769
OFFSET_TIME = 0x9010
OFFSET_TIME_ORIGINAL = 0x9011
OFFSET_TIME_DIGITIZED = 0x9012
OFFSET_TAGS = (OFFSET_TIME_ORIGINAL, OFFSET_TIME, OFFSET_TIME_DIGITIZED)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_ASCII_TYPE = 2
_MAX_ASCII = 32
_MAX_IFDS = 8


def tiff_blocks(data: bytes) -> list[bytes]:
    """Every TIFF block that could hold EXIF inside one file's bytes."""
    if data[:8] == PNG_SIGNATURE:
        tiff = png_chunks.read_exif(data)
        return [tiff] if tiff else []
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return [data]
    blocks: list[bytes] = []
    start = 0
    while True:
        index = data.find(b"Exif\x00\x00", start)
        if index < 0 or index + 6 >= len(data):
            return blocks
        blocks.append(data[index + 6 :])
        start = index + 6


def ascii_tag(data: bytes, tag: int) -> str | None:
    """First ASCII value of ``tag`` found anywhere in the file's EXIF."""
    for block in tiff_blocks(data):
        value = _ascii_in_block(block, tag)
        if value:
            return value
    return None


def offset_time_from_bytes(data: bytes) -> int | None:
    """UTC offset (seconds) recorded by the camera, or ``None``."""

    from ..times import parse_utc_offset

    for tag in OFFSET_TAGS:
        text = ascii_tag(data, tag)
        if text:
            offset = parse_utc_offset(text)
            if offset is not None:
                return offset
    return None


def _ascii_in_block(block: bytes, tag: int) -> str | None:
    if len(block) < 8:
        return None
    endian = "<" if block[:2] == b"II" else ">"
    try:
        if struct.unpack_from(endian + "H", block, 2)[0] != 42:
            return None  # big.tiff and friends are out of scope
        ifd = struct.unpack_from(endian + "I", block, 4)[0]
    except struct.error:
        return None
    queue = [ifd]
    seen: set[int] = set()
    while queue:
        offset = queue.pop(0)
        if offset in seen or not 8 <= offset < len(block) - 2:
            continue
        seen.add(offset)
        try:
            count = struct.unpack_from(endian + "H", block, offset)[0]
        except struct.error:
            continue
        entries = offset + 2
        for index in range(count):
            base = entries + 12 * index
            if base + 12 > len(block):
                break
            entry_tag, entry_type, size = struct.unpack_from(
                endian + "HHI", block, base
            )
            if entry_tag == tag and entry_type == _ASCII_TYPE:
                return _read_ascii(block, endian, base, size)
            if entry_tag == EXIF_SUB_IFD:
                queue.append(struct.unpack_from(endian + "I", block, base + 8)[0])
        follower = entries + 12 * count
        if len(queue) < _MAX_IFDS and follower + 4 <= len(block):
            queue.append(struct.unpack_from(endian + "I", block, follower)[0])
    return None


def _read_ascii(block: bytes, endian: str, base: int, size: int) -> str | None:
    if not 1 <= size <= _MAX_ASCII:
        return None
    if size <= 4:
        raw = block[base + 8 : base + 8 + size]
    else:
        try:
            where = struct.unpack_from(endian + "I", block, base + 8)[0]
        except struct.error:
            return None
        if not 0 <= where <= len(block):
            return None
        raw = block[where : where + size]
    return raw.decode("ascii", "ignore").rstrip("\x00 ").strip() or None
