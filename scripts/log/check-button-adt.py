#!/usr/bin/env python3
"""Extract bus-relative SPMI ranges; parent ranges must translate them to PA."""
import struct
import sys
from pathlib import Path


def spmi_nodes(blob):
    def node(at, depth=0):
        if depth > 64 or at + 8 > len(blob):
            raise ValueError('truncated or excessively nested ADT')
        properties, children = struct.unpack_from('<II', blob, at)
        at += 8
        values = {}
        for _ in range(properties):
            if at + 36 > len(blob):
                raise ValueError('truncated property')
            name = blob[at:at + 32].split(b'\0')[0]
            size = struct.unpack_from('<I', blob, at + 32)[0] & 0x7fffffff
            at += 36
            if at + size > len(blob):
                raise ValueError('truncated value')
            values[name] = blob[at:at + size]
            at += (size + 3) & ~3
        if values.get(b'name', b'').rstrip(b'\0') == b'spmi':
            names = values[b'reg-names'].rstrip(b'\0').split(b'\0')
            regs = values[b'reg']
            if len(regs) != len(names) * 16:
                raise ValueError('SPMI range/name mismatch')
            found.append(dict(zip(names, struct.iter_unpack('<QQ', regs))))
        for _ in range(children):
            at = node(at, depth + 1)
        return at

    found = []
    end = node(0)
    if end != len(blob):
        raise ValueError('trailing ADT data')
    return found


if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        assert spmi_nodes(struct.pack('<II', 0, 0)) == []
        for bad in (b'', struct.pack('<II', 1, 0), struct.pack('<II', 0, 1)):
            try:
                spmi_nodes(bad)
            except ValueError:
                pass
            else:
                raise AssertionError('accepted truncated tree')
        print('PASS: ADT bounds checks')
    else:
        for device in spmi_nodes(Path(sys.argv[1]).read_bytes()):
            for name, (base, size) in device.items():
                print(f'{name.decode()}: bus-relative=0x{base:x} size=0x{size:x} (not a physical address)')
