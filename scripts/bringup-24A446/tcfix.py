"""tcfix.py K: DEVELOPMENT-style TrustCache placement in arm_vm_prot_init. The RELEASE check panics when the region is above
segLOWEST (DarwinBoot always puts it above the kernel); instead record segTRUSTCACHE/segSizeTRUSTCACHE and continue."""
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
def rw(v): return struct.unpack_from('<I',d,off(v))[0]
def ww(v,x): struct.pack_into('<I',d,off(v),x)
SITE=0xfffffff00a4cc8bc; JOIN=0xfffffff00a4cc908; CAVE=0xfffffff00ab13800
assert rw(SITE)==0x54002922, hex(rw(SITE))           # b.hs panic path
assert rw(JOIN)==0xf900bb40 and rw(JOIN+4)==0xf900bf68, (hex(rw(JOIN)),hex(rw(JOIN+4)))  # str x0,[x26,#368]; str x8,[x27,#376]
assert all(rw(CAVE+4*i)==0 for i in range(8)), 'cave not empty'
def b(src,dst): return 0x14000000|(((dst-src)>>2)&0x3ffffff)
code=[0xf9400be8,   # ldr x8,[sp,#16]   (DTMemoryMapRange*)
      0xf9400508,   # ldr x8,[x8,#8]    length
      0xf940b721,   # ldr x1,[x25,#360] segSizeEXTRADATA
      b(CAVE+12,JOIN)]
for i,x in enumerate(code): ww(CAVE+4*i,x)
PAD=0xfffffff00a4ccde0                               # b.hs target: 'bl arm_vm_prot_init.cold.2', reached only from SITE
assert rw(PAD)>>26==0b100101 and rw(PAD-4)>>26==0b100101, 'pad is not a lone panic call after a noreturn call'
ww(PAD,b(PAD,CAVE))
open(K,'wb').write(d); print('tcfix: b.hs',hex(SITE),'-> pad',hex(PAD),'-> cave',hex(CAVE),'-> join',hex(JOIN))
