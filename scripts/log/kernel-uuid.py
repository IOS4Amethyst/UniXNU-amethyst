#!/usr/bin/env python3
"""Print LC_UUID and SHA256 for an uncompressed thin ARM64 Mach-O kernel."""
import hashlib
from pathlib import Path
import struct
import sys
import uuid


def kernel_uuid(data):
    if len(data) < 32 or struct.unpack_from('<II', data) != (0xfeedfacf, 0x100000c):
        raise ValueError('expected a thin little-endian ARM64 Mach-O')
    count, size = struct.unpack_from('<II', data, 16)
    end = 32 + size
    if end > len(data):
        raise ValueError('truncated load commands')
    at, found = 32, []
    for _ in range(count):
        if at + 8 > end:
            raise ValueError('truncated command')
        cmd, length = struct.unpack_from('<II', data, at)
        if length < 8 or length % 8 or at + length > end:
            raise ValueError('invalid command length')
        if cmd == 0x1b:
            if length != 24:
                raise ValueError('invalid LC_UUID length')
            found.append(str(uuid.UUID(bytes=data[at + 8:at + 24])).upper())
        at += length
    if at != end or len(found) != 1:
        raise ValueError('expected exactly one LC_UUID and complete command table')
    return found[0]


if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 1, 24, 0, 0)
        sample = header + struct.pack('<II', 0x1b, 24) + bytes(range(16))
        assert kernel_uuid(sample) == '00010203-0405-0607-0809-0A0B0C0D0E0F'
        for invalid in (b'', sample[:-1], sample[:36] + bytes(4) + sample[40:]):
            try:
                kernel_uuid(invalid)
            except ValueError:
                pass
            else:
                raise AssertionError('accepted malformed kernel')
        print('PASS: UUID extraction and malformed-input checks')
    else:
        for name in sys.argv[1:]:
            data = Path(name).read_bytes()
            print(kernel_uuid(data), hashlib.sha256(data).hexdigest(), name)
