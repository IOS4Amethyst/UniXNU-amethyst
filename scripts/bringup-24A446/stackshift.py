"""Fix AMFI's CoreEntitlements stack-depth check for our page/stack layout.

Upstream (UniXNU/XnuPrelink fixtures/fileset-24A446-symbols.json, 'sites' entry) found this exact
bug: AppleMobileFileIntegrity's GOT slot at kernelcache 0xfffffff007ef0db0 points at Apple's
PAGE_SHIFT_CONST global. CoreEntitlements' DER iterator reads it and checks
    sp & ((1 << shift) - 1) < 0x80
assuming the stack is aligned to 1<<shift, true for Apple's 16 KiB-page kernel (shift=14, with two
32 KiB guard pages below). Our native kernel uses 4 KiB pages and starts its stack 8 KiB into a
32 KiB-aligned allocation, so shift=14 gives false "out of stack" positives at fixed call depths --
this is the literal mechanism behind "AMFI: failed to create OSDictionary copy: 14" on exec.

Upstream's fix: rebind the slot to a new value, 15 (the real allocation-alignment shift), instead of
Apple's constant. We don't have (or need) their kernel-source symbol; we get the same effect by
writing the raw 32-bit value 15 into an unused scratch word and repointing the GOT slot at it.

Confirmed site: kernelcache 0xfffffff008f7c838 (mov x9,sp) / 0xf7c83c (and x8,x8,x9) /
0xf7c840 (cmp x8,#0x80) -- the exact sp & mask < 0x80 pattern, inside AMFI's CE converter area.
"""
import struct,sys
K=sys.argv[1]; d=bytearray(open(K,'rb').read())
n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,p)
    if cmd==0x19:
        va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
    p+=sz
def off(v):
    for a,f,fs in segs:
        if a<=v<a+fs and fs: return f+v-a
BASE=0xfffffff007004000
SLOT=0xfffffff007ef0db0      # AppleMobileFileIntegrity's GOT slot for PAGE_SHIFT_CONST
SCRATCH=0xfffffff00ab13900   # unused word in the panic cave range; holds the raw shift value
cur=struct.unpack_from('<Q',d,off(SLOT))[0]
assert cur==0x10000000c29988, ('slot not at the expected original Apple binding', hex(cur))
assert struct.unpack_from('<I',d,off(SCRATCH))[0]==0, 'scratch word not empty'
struct.pack_into('<I',d,off(SCRATCH),15)                              # the real allocation-alignment shift
new=0x1000000 | ((SCRATCH-BASE)&0x7ffffffff)                           # non-auth rebase encoding
struct.pack_into('<Q',d,off(SLOT),new)
open(K,'wb').write(d)
print('stackshift: PAGE_SHIFT_CONST slot',hex(SLOT),'-> scratch',hex(SCRATCH),'= 15 (was Apple-16KiB-page shift 14)')
