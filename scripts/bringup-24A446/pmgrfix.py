"""Bring-up: undo the -0x1000000 slide compile-pmgr-arm64e.py applied to the six original ApplePMGR bindings.
Retained kexts keep their kernelcache addresses in this prelinked kernel, so every native reference to
(address - 0x1000000) is retargeted to the address: bl/b immediates and raw 64-bit data words."""
import struct, sys
k = sys.argv[1]
d = bytearray(open(k, 'rb').read())
n = struct.unpack_from('<I', d, 16)[0]; p = 32; segs = []
for _ in range(n):
    cmd, sz = struct.unpack_from('<II', d, p)
    if cmd == 0x19:
        va, vs, fo, fs, mx, ip = struct.unpack_from('<QQQQII', d, p + 24)
        segs.append((va, fo, fs, ip, d[p + 8:p + 24].rstrip(b'\0')))
    p += sz
SLIDE = 0x1000000
REAL = {'C2': 0xfffffff008feb7bc, 'D2': 0xfffffff008feb98c, 'gMetaClass': 0xfffffff00b03a960,
        'nub_create': 0xfffffff00903da68, 'nub_read': 0xfffffff009041284, 'nub_write': 0xfffffff00903ecd4}
# Data words carry address - 0x1000000; relative branches to the absolute .set symbols moved with the
# native code (+0x3184000 in this prelink), landing at address + 0x2184000.
BRANCH = 0x3184000 - SLIDE
wrong = {v - SLIDE: (name, v) for name, v in REAL.items()}
wrong_branch = {v + BRANCH: (name, v) for name, v in REAL.items()}
hits = {}
NATIVE = (0xfffffff00a300000, 0xfffffff00ac00000)   # native kernel text range (com.apple.kernel)
for a, f, fs, ip, nm in segs:
    for o in range(f, f + fs - 7, 4):
        va = a + o - f
        if ip & 4 and NATIVE[0] <= va < NATIVE[1]:
            w = struct.unpack_from('<I', d, o)[0]
            if w >> 26 in (0x25, 0x05):
                dl = w & 0x3ffffff; dl -= (1 << 26) if dl & (1 << 25) else 0
                t = va + dl * 4
                if t in wrong_branch:
                    name, real = wrong_branch[t]
                    struct.pack_into('<I', d, o, (w & 0xfc000000) | (((real - va) >> 2) & 0x3ffffff))
                    hits[name] = hits.get(name, 0) + 1
        if not ip & 4 and nm != b'__LINKEDIT' and o % 8 == 0:
            v = struct.unpack_from('<Q', d, o)[0]
            if v in wrong:
                name, real = wrong[v]
                struct.pack_into('<Q', d, o, real); hits[name + '(data)'] = hits.get(name + '(data)', 0) + 1
open(k, 'wb').write(d)
print('pmgr bindings retargeted:', hits)
