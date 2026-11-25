"""Make every PAGE_SHIFT_CONST reader see 12 (ARM_PGSHIFT, 4K pages).

iOS 27 sets PAGE_SHIFT_CONST at runtime in osfmk/arm64/sptm/arm_init_sptm.c. UniXNU's
native-4k-user-abi.patch adds the "force 4K" assignment only to the OLD non-SPTM
osfmk/arm/arm_init.c (the iPadOS-17 fallback). The SPTM arm_init was never patched and
DarwinBoot's custom early boot does not run that assignment, so PAGE_SHIFT_CONST is never set to
the page shift.

This kernel uses PAGE_SHIFT_CONST as the runtime page shift. With it unset, the file pager crashed:
vnode_pagein -> upl_page_present computed a page index of (0x8000 >> shift) - 1. The compiler emits
the read as `ldr x,[GOT]; ldr w,[x]`, where GOT holds a pointer to the constant. In the Apple
kernelcache every such GOT slot holds 0x0010000000c29988 -> VA 0xfffffff007c2d988, and the int there
reads 0 at runtime (it is not a stable const in the running layout -- writing 12 into the image does
not stick, exactly as AMFI's identical slot did not). So with shift 0 the index was
(0x8000 >> 0) - 1 = 0x7fff instead of 7 -> walk off the UPL page-list -> data abort at
_upl_page_present+0x8 (esr 0x96000007, translation fault L3).

Fix (mirrors stackshift.py): point every PAGE_SHIFT_CONST GOT slot at a scratch word that holds 12,
so the reader dereferences 12 regardless of the unstable global. AMFI's own slot (0xfffffff007ef0db0)
is NOT in this set -- stackshift already redirected it to a different scratch = 15 (a CoreEntitlements
stack-alignment quirk, not the page shift). The two needs are genuinely different values for the same
overloaded symbol, so they use two scratch words.

Proper fix (needs a kernel rebuild): port native-4k-user-abi.patch's `PAGE_SHIFT_CONST = ARM_PGSHIFT`
(and page_shift_user32 / commpage USER_PAGE_SHIFT_64) into osfmk/arm64/sptm/arm_init_sptm.c.
"""
import struct, sys
K = sys.argv[1]
d = bytearray(open(K, 'rb').read())
n = struct.unpack_from('<I', d, 16)[0]; p = 32; segs = []
for _ in range(n):
    cmd, sz = struct.unpack_from('<II', d, p)
    if cmd == 0x19:
        va, vs, fo, fs = struct.unpack_from('<QQQQ', d, p + 24); segs.append((va, fo, fs))
    p += sz
def off(v):
    for a, f, fs in segs:
        if a <= v < a + fs and fs: return f + v - a
    raise ValueError('va %#x not file-backed' % v)
BASE = 0xfffffff007004000
OLD_SLOT_VAL = 0x0010000000c29988     # format-8 rebase -> 0xfffffff007c2d988 (Apple PAGE_SHIFT_CONST), next=2
SCRATCH12 = 0xfffffff00ab13908        # free word in the panic-screen cave (0xab11700..0xab13ff0); stackshift uses 0xab13900
ARM_PGSHIFT = 12
# 1) write 12 into the scratch word (same cave stackshift proved stable at runtime)
assert struct.unpack_from('<Q', d, off(SCRATCH12))[0] == 0, 'scratch12 word not empty'
struct.pack_into('<q', d, off(SCRATCH12), ARM_PGSHIFT)
new_slot = (OLD_SLOT_VAL & ~0x3fffffff) | ((SCRATCH12 - BASE) & 0x3fffffff)   # preserve next, retarget low 30
assert ((new_slot >> 51) & 0xFFF) == 2 and (new_slot >> 63) == 0, ('encode failed', hex(new_slot))
# 2) redirect every GOT slot that points at the (unstable) PAGE_SHIFT_CONST to the scratch=12 word
AMFI_SLOT_FO = off(0xfffffff007ef0db0)   # AMFI's slot -> stackshift redirects this one to 15; never touch it
needle = struct.pack('<Q', OLD_SLOT_VAL)
repl = struct.pack('<Q', new_slot)
count = 0; i = 0
while True:
    i = d.find(needle, i)
    if i < 0: break
    if i == AMFI_SLOT_FO:          # leave AMFI's PAGE_SHIFT_CONST slot for stackshift (=15), order-independent
        i += 8; continue
    d[i:i + 8] = repl; count += 1; i += 8
assert count >= 1, 'no PAGE_SHIFT_CONST GOT slots found'
# 3) belt-and-suspenders: also set the (clobberable) global itself, harmless for any direct reader
try:
    o = off(0xfffffff007c2d988)
    if struct.unpack_from('<i', d, o)[0] in (0, ARM_PGSHIFT):
        struct.pack_into('<i', d, o, ARM_PGSHIFT)
except ValueError:
    pass
open(K, 'wb').write(d)
print('pageshift: redirected %d PAGE_SHIFT_CONST GOT slots -> scratch %#x = %d' % (count, SCRATCH12, ARM_PGSHIFT))
