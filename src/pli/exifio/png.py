"""PNG container helpers: read and write the standard ``eXIf`` chunk.

``piexif`` cannot touch PNG, but PNG stores EXIF as an ordinary ``eXIf`` chunk
holding a bare TIFF block, so a minimal chunk editor is enough and the image
data is copied through byte for byte.
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EXIF_CHUNK = b"eXIf"


class PngFormatError(ValueError):
    """Raised when a file does not look like a PNG stream."""


def _chunks(data: bytes):
    offset = len(PNG_SIGNATURE)
    total = len(data)
    while offset + 8 <= total:
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        if payload_end + 4 > total:
            raise PngFormatError("truncated PNG chunk")
        stored_crc = struct.unpack(">I", data[payload_end : payload_end + 4])[0]
        if zlib.crc32(data[offset + 4 : payload_end]) & 0xFFFFFFFF != stored_crc:
            raise PngFormatError(f"bad CRC in PNG chunk {kind!r}")
        yield kind, data[payload_start:payload_end], offset, payload_end + 4
        offset = payload_end + 4


def build_chunk(kind: bytes, payload: bytes) -> bytes:
    """Serialise one PNG chunk (length, type, payload, CRC)."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def read_exif(data: bytes) -> bytes | None:
    """Return the TIFF block stored in the ``eXIf`` chunk, if any."""
    if not data.startswith(PNG_SIGNATURE):
        raise PngFormatError("not a PNG file")
    found: bytes | None = None
    for kind, payload, _start, _end in _chunks(data):
        if kind == EXIF_CHUNK:
            found = payload
    return found


def write_exif(data: bytes, tiff: bytes) -> bytes:
    """Replace (or insert) the ``eXIf`` chunk, keeping everything else intact."""
    if not data.startswith(PNG_SIGNATURE):
        raise PngFormatError("not a PNG file")
    out = bytearray(PNG_SIGNATURE)
    inserted = False
    for kind, payload, start, end in _chunks(data):
        if kind == EXIF_CHUNK:
            continue
        if kind in {b"IHDR", b"PLTE", b"acTL"}:
            out += data[start:end]
            if kind == b"PLTE":  # eXIf must precede the first IDAT
                out += build_chunk(EXIF_CHUNK, tiff)
                inserted = True
            continue
        if kind in {b"IDAT", b"IEND"} and not inserted:
            out += build_chunk(EXIF_CHUNK, tiff)
            inserted = True
        out += data[start:end]
    if not inserted:
        out += build_chunk(EXIF_CHUNK, tiff)
    return bytes(out)
