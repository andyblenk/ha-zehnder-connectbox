"""Firmware packages of the Zehnder system, as the official app ships them.

The app's bundle holds a package such as Gateway-1.8.1.fw and uploads it to
the ConnectBox, which then updates itself and the units. The package starts
with a header whose layout was read from that file:

    0x00  "Info"
    0x04  header length, format version, payload length, CRC-32 of the payload
          (four little-endian 32-bit numbers)
    0x14  0, major, minor, patch of the system version (32-bit numbers)
    0x24  the system version as text, 32 bytes, zero padded
    ...   the payload, which fills the rest of the file

The payload length and CRC-32 matched the file, so both are checked here
before a package is offered or uploaded.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

FIRMWARE_DIRECTORY = "zehnder_connectbox"
FIRMWARE_SUFFIX = ".fw"
FIRMWARE_MAGIC = b"Info"
FIRMWARE_FIXED_HEADER = 0x44
# The app's package has about 0.5 MB; anything far larger is not one.
FIRMWARE_MAX_SIZE = 16 * 1024 * 1024
VERSION_TEXT_LENGTH = 32

type Version = tuple[int, int, int]


class FirmwareError(ValueError):
    """The file is not a complete firmware package."""


@dataclass(frozen=True, slots=True)
class FirmwarePackage:
    """A checked firmware package."""

    path: Path
    version: Version
    data: bytes

    @property
    def version_text(self) -> str:
        """Return the version as the app shows it."""
        return format_version_tuple(self.version)


def format_version_tuple(version: Version) -> str:
    """Format a version like the app's update page."""
    return ".".join(str(part) for part in version)


def parse_firmware(data: bytes, path: Path) -> FirmwarePackage:
    """Check a package's header, length and CRC-32 and return it."""
    if len(data) > FIRMWARE_MAX_SIZE:
        raise FirmwareError("the file is too large for a firmware package")
    if len(data) <= FIRMWARE_FIXED_HEADER or data[:4] != FIRMWARE_MAGIC:
        raise FirmwareError("the file does not start like a firmware package")
    _header_length, _format, payload_length, crc = struct.unpack_from("<IIII", data, 4)
    _zero, major, minor, patch = struct.unpack_from("<IIII", data, 0x14)
    payload_offset = len(data) - payload_length
    if payload_length <= 0 or payload_offset < FIRMWARE_FIXED_HEADER:
        raise FirmwareError("the file is shorter or longer than its header says")
    if zlib.crc32(data[payload_offset:]) & 0xFFFFFFFF != crc:
        raise FirmwareError("the checksum of the file does not match")
    text = data[0x24 : 0x24 + VERSION_TEXT_LENGTH].split(b"\0", 1)[0]
    version = (major, minor, patch)
    if text.decode(errors="replace") != format_version_tuple(version):
        raise FirmwareError("the version numbers of the file do not agree")
    return FirmwarePackage(path=path, version=version, data=data)


def read_firmware(path: Path) -> FirmwarePackage:
    """Read and check one package file."""
    try:
        if path.stat().st_size > FIRMWARE_MAX_SIZE:
            raise FirmwareError("the file is too large for a firmware package")
        data = path.read_bytes()
    except OSError as err:
        raise FirmwareError("the file cannot be read") from err
    return parse_firmware(data, path)


def find_firmware(directory: Path) -> tuple[FirmwarePackage | None, list[str]]:
    """Return the newest valid package of a folder and the files left out."""
    newest: FirmwarePackage | None = None
    rejected: list[str] = []
    try:
        candidates = sorted(
            item
            for item in directory.iterdir()
            if item.is_file() and item.suffix.lower() == FIRMWARE_SUFFIX
        )
    except OSError:
        return None, rejected
    for path in candidates:
        try:
            package = read_firmware(path)
        except FirmwareError as err:
            rejected.append(f"{path.name}: {err}")
            continue
        if newest is None or package.version > newest.version:
            newest = package
    return newest, rejected
