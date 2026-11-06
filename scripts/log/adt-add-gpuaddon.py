#!/usr/bin/env python3
"""Add the 16-byte GpuAddon property to an ADT's /chosen/memory-map, as UniXNU's devicetree.py does with --gpu-addon.

DarwinBoot (DarwinBoot.c AdtNode) fills it with the address and size of Payloads/kernel.macho.gpuaddon; the kernel's
addon mount finds the addon there. ADT layout: node = u32 nprops, u32 nchildren, props, children; prop = name[32],
u32 length (bit 31 template), value padded to 4. The property is appended after TrustCache; nothing else moves.
  adt-add-gpuaddon.py IN OUT"""
import struct, sys

def walk(a, off, depth, path, found):
    nprops, nchildren = struct.unpack_from('<II', a, off)
    head = off; off += 8; name = None; props_end = None
    for _ in range(nprops):
        pname = a[off:off + 32].split(b'\0')[0]; length = struct.unpack_from('<I', a, off + 32)[0] & 0x7fffffff
        if pname == b'name': name = a[off + 36:off + 36 + length].split(b'\0')[0]
        off += 36 + ((length + 3) & ~3)
    props_end = off
    p = path + [name]
    if p[1:] == [b'chosen', b'memory-map']: found.append((head, props_end))
    for _ in range(nchildren): off = walk(a, off, depth + 1, p, found)
    return off

def main(src, dst):
    a = bytearray(open(src, 'rb').read())
    assert b'GpuAddon' not in a, 'already has GpuAddon'
    found = []
    end = walk(a, 0, 0, [], found)
    assert end == len(a), f'parse ended at {end} of {len(a)}'
    assert len(found) == 1, found
    head, props_end = found[0]
    prop = b'GpuAddon'.ljust(32, b'\0') + struct.pack('<I', 16) + bytes(16)
    a[props_end:props_end] = prop
    struct.pack_into('<I', a, head, struct.unpack_from('<I', a, head)[0] + 1)
    check = []
    assert walk(a, 0, 0, [], check) == len(a) and len(check) == 1
    open(dst, 'wb').write(a)
    print(f'GpuAddon added to /chosen/memory-map: {len(a) - len(prop)} -> {len(a)} bytes')

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
