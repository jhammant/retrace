"""Resolve a Windows shortcut (.lnk) to the path it points at, without COM.

Windows records recently opened documents as shortcuts in
``%APPDATA%\\Microsoft\\Windows\\Recent``. This reads the LinkInfo block of the
Shell Link format ([MS-SHLLINK] 2.3) for the local target path. Pure Python, so
it is tested on any OS.
"""

from __future__ import annotations

import struct

_HEADER_SIZE = 0x4C
_HAS_ID_LIST = 0x01
_HAS_LINK_INFO = 0x02
_VOLUME_ID_AND_LOCAL_BASE_PATH = 0x01


def _cstr(data: bytes, start: int) -> str:
    end = data.index(b"\x00", start)
    raw = data[start:end]
    for codec in ("mbcs", "cp1252"):  # the ANSI code page; "mbcs" exists only on Windows
        try:
            return raw.decode(codec)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1")


def _wstr(data: bytes, start: int) -> str:
    end = start
    while data[end:end + 2] != b"\x00\x00":
        end += 2
        if end >= len(data):
            raise ValueError("unterminated UTF-16 string")
    return data[start:end].decode("utf-16-le")


def lnk_target(data: bytes) -> str | None:
    """The local file a .lnk points at, or None (network target, no LinkInfo, malformed)."""
    try:
        if len(data) < _HEADER_SIZE or struct.unpack_from("<I", data, 0)[0] != _HEADER_SIZE:
            return None
        flags = struct.unpack_from("<I", data, 20)[0]
        pos = _HEADER_SIZE
        if flags & _HAS_ID_LIST:
            pos += 2 + struct.unpack_from("<H", data, pos)[0]
        if not flags & _HAS_LINK_INFO:
            return None
        info = pos
        (_size, header_size, info_flags, _vol, base_off, _net, suffix_off) = struct.unpack_from("<7I", data, info)
        if not info_flags & _VOLUME_ID_AND_LOCAL_BASE_PATH:
            return None
        if header_size >= 0x24:  # Unicode offsets present
            base_u, suffix_u = struct.unpack_from("<2I", data, info + 28)
            if base_u:
                base = _wstr(data, info + base_u)
                suffix = _wstr(data, info + suffix_u) if suffix_u else ""
                return _join(base, suffix)
        base = _cstr(data, info + base_off)
        suffix = _cstr(data, info + suffix_off) if suffix_off else ""
        return _join(base, suffix)
    except (struct.error, ValueError, IndexError):
        return None


def _join(base: str, suffix: str) -> str | None:
    if not base:
        return None
    if suffix and not base.endswith("\\"):
        return base + "\\" + suffix
    return base + suffix
