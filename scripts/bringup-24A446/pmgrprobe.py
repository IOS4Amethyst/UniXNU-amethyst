"""Bring-up probe: after bsd_init's call to sheng_pmgr_run, panic with sheng_pmgr_observation[] unless it succeeded.
observation[0] = progress (1 kext load .. 5 published), [1] = result (0 success), others per ShengPlatformPower.cpp."""
import struct, subprocess, re, sys
k = sys.argv[1]
d = bytearray(open(k, 'rb').read())
n = struct.unpack_from('<I', d, 16)[0]; p = 32; segs = []
for _ in range(n):
    cmd, sz = struct.unpack_from('<II', d, p)
    if cmd == 0x19:
        va, vs, fo, fs = struct.unpack_from('<QQQQ', d, p + 24); segs.append((va, fo, fs))
    p += sz
off = lambda va: [f + va - a for a, f, fs in segs if a <= va < a + fs][0]
W = lambda va: struct.unpack_from('<I', d, off(va))[0]
put = lambda va, x: struct.pack_into('<I', d, off(va), x)
names = {}
for l in subprocess.run(['llvm-nm', k], capture_output=True, text=True).stdout.splitlines():
    t = l.split()
    if len(t) == 3 and all(c in '0123456789abcdef' for c in t[0]): names.setdefault(t[2], int(t[0], 16))
RUN, OBS, PANIC, BSD = names['_sheng_pmgr_run'], names['_sheng_pmgr_observation'], names['_panic'], names['_bsd_init']
bl = lambda frm, to: 0x94000000 | (((to - frm) >> 2) & 0x3ffffff)
site = next(va for va in range(BSD, BSD + 0x2000, 4) if W(va) == bl(va, RUN))
CAVE = 0xfffffff00ab12b00; FMT = CAVE + 0x100
assert not any(d[off(CAVE):off(CAVE) + 0x200]), 'cave not empty'
def enc(s):
    o = subprocess.run(['llvm-mc', '-triple=aarch64', '-mattr=+v8.5a,+pauth', '-show-encoding'], input=s, capture_output=True, text=True).stdout
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})', l.split('encoding:')[1]))), 16) for l in o.splitlines() if 'encoding:' in l]
def adrp(rd, frm, to):
    imm = ((to & ~0xfff) - (frm & ~0xfff)) >> 12; return 0x90000000 | ((imm & 3) << 29) | (((imm >> 2) & 0x7ffff) << 5) | rd
addimm = lambda rd, rn, imm: 0x91000000 | ((imm & 0xfff) << 10) | (rn << 5) | rd
SLOTS = [0, 1, 2, 3, 5, 6, 7, 8, 13, 14, 15]
code = enc("pacibsp\nstp x29,x30,[sp,#-16]!\nmov x29,sp\n")
code.append(('BL', RUN))
code.append(('ADRP', 9, OBS)); code.append(('ADD', 9, OBS))
code += enc("ldr x10,[x9,#8]\n")
code.append(('CBZ10',))
code += enc("sub sp,sp,#96\n")  # 11 slots, 16-byte aligned; panic does not return
for i, s in enumerate(SLOTS):
    code += enc(f"ldr x10,[x9,#{8 * s}]\nstr x10,[sp,#{8 * i}]\n")
code.append(('ADRP', 0, FMT)); code.append(('ADD', 0, FMT)); code.append(('BL', PANIC))
out = len(code)
code += enc("ldp x29,x30,[sp],#16\nretab\n")
for i, x in enumerate(code):
    va = CAVE + 4 * i
    if isinstance(x, tuple):
        if x[0] == 'BL': x = bl(va, x[1])
        elif x[0] == 'ADRP': x = adrp(x[1], va, x[2])
        elif x[0] == 'ADD': x = addimm(x[1], x[1], x[2] & 0xfff)
        elif x[0] == 'CBZ10': x = 0xb4000000 | ((((CAVE + 4 * out) - va) >> 2 & 0x7ffff) << 5) | 10
    put(va, x)
fmt = ('ShengPlatformPower did not publish: ' + ' '.join(f'o{s}=%llx' for s in SLOTS) + '\n').encode() + b'\0'
d[off(FMT):off(FMT) + len(fmt)] = fmt
put(site, bl(site, CAVE))
open(k, 'wb').write(d)
print('pmgr probe at', hex(site), '-> cave', hex(CAVE), len(code), 'insns')
