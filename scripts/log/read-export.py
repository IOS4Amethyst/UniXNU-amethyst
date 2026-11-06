#!/usr/bin/env python3
"""Unpack a UFS log export (export.bin from pull-log.py) into its files.

The export is the newest complete blob a GPU client sent with the kernel's export method (NativeAdreno 0x7100), as
AdrenoMetal CacheExport.h writes it: "SHEXR001", u32 stream bytes, u32 0, then the stream "M2VCACH1", u32 directory
length, directory, u32 file count, and per file u32 path length, u32 mode, u32 uid, u32 gid, u64 size, path, contents.
On the springboard-oslog branch the directory is SpringBoard's Caches/amethyst-oslog and its file SpringBoard.txt.

Usage: read-export.py <export.bin> <output-dir>
"""
import struct
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    data = Path(sys.argv[1]).read_bytes()
    out = Path(sys.argv[2])
    if data[:8] == b'SHEXR001':
        streamed = struct.unpack_from('<I', data, 8)[0]
        data = data[16:16 + streamed]
    if data[:8] != b'M2VCACH1':
        sys.exit(f'not an export stream: {data[:8]!r}')
    at = 8
    length = struct.unpack_from('<I', data, at)[0]
    at += 4
    directory = data[at:at + length].decode(errors='replace')
    at += length
    count = struct.unpack_from('<I', data, at)[0]
    at += 4
    print(f'directory {directory}: {count} files')
    out.mkdir(parents=True, exist_ok=True)
    for _ in range(count):
        plen, mode, uid, gid, size = struct.unpack_from('<IIIIQ', data, at)
        at += 24
        path = data[at:at + plen].decode(errors='replace')
        at += plen
        body = data[at:at + size]
        at += size
        if len(body) != size:
            sys.exit(f'{path}: short ({len(body)} of {size} bytes)')
        target = (out / path).resolve()
        if out.resolve() not in target.parents:
            sys.exit(f'{path}: outside the output directory')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        print(f'  {path} {size} bytes (mode {mode:o} uid {uid} gid {gid})')


main()
