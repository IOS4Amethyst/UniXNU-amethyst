"""Bring-up probe: at OSKext::loadKextWithIdentifier(OSString*, ...)'s entry, silently stash up to
24 bytes of the identifier string (via OSString::getCStringNoCopy) into a fixed scratch buffer, then
continue normally. No printing here -- this runs for every kext load attempt, far too fast to
photograph, and IOLog's redaction behaviour for %s is untested. badcastfreeze.py reads this buffer
back at the exact point that later crashes.

ALSO stashes the live value of sAllKextLoadIdentifiers (same global lkcentrystash.py/initprobe.py
read) into CALLSTASH on every call, overwriting it each time -- so by the time the crash happens
(processing com.apple.kpi.bsd, the second entry in sKernelComponentNames[], right after
com.apple.kernel, the first) CALLSTASH holds the global's value as of the START of THAT call,
i.e. recordIdentifierRequest's very own read of it moments later. Comparing CALLSTASH against
lkcentrystash.py's loop-entry value pinpoints whether the stray write happens during
com.apple.kernel's own processing (the only thing between those two points) or in the sliver of
loadKextWithIdentifier's own body between this probe and recordIdentifierRequest's read (which
source shows is called as the first statement, so should be a negligible window)."""
import struct,subprocess,re,sys
k=sys.argv[1]; d=bytearray(open(k,'rb').read())
n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,p)
    if cmd==0x19:
        va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
    p+=sz
off=lambda va:[f+va-a for a,f,fs in segs if a<=va<a+fs][0]
W=lambda va:struct.unpack_from('<I',d,off(va))[0]
put=lambda va,x:struct.pack_into('<I',d,off(va),x)
names={}
for l in subprocess.run(['llvm-nm',k],capture_output=True,text=True).stdout.splitlines():
    t=l.split()
    if len(t)==3 and all(c in '0123456789abcdef' for c in t[0]): names.setdefault(t[2],int(t[0],16))
GETCSTR=names['__ZNK8OSString16getCStringNoCopyEv']
SITE=0xfffffff00a8e9b1c; CAVE=0xfffffff00ab13a00
STASH=0xfffffff00b0d7fc0    # last 64 (zero-fill) bytes of __DATA, writable at runtime -- the panic cave is __TEXT_EXEC (r-x only)
GLOBAL=0xfffffff00af67520   # sAllKextLoadIdentifiers's live storage slot (see lkcentrystash.py)
CALLSTASH=0xfffffff00b0d7f70  # 8 bytes, 16 before ENTRYSTASH (0xb0d7f80): overwritten on every call
assert W(SITE)==0xd503237f, 'site not pacibsp'
assert not any(d[off(CAVE):off(CAVE)+0x180]), 'cave not empty'
assert not any(d[off(STASH):off(STASH)+32]), 'stash not empty'
assert not any(d[off(CALLSTASH):off(CALLSTASH)+8]), 'callstash not empty'
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True).stdout
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in o.splitlines() if 'encoding:' in l]
bl=lambda frm,to:0x94000000|(((to-frm)>>2)&0x3ffffff)
code=enc("""sub sp,sp,#80
stp x0,x1,[sp,#0]
stp x2,x3,[sp,#16]
stp x4,x5,[sp,#32]
stp x6,x30,[sp,#48]
""")
code.append(bl(CAVE+4*len(code),GETCSTR))      # x0 (OSString*) -> x0 (char*)
code.append(('STASH_ADRP',)); code.append(('STASH_ADD',))
code+=enc("""mov x9,x0
ldr x10,[x9]
str x10,[x11]
ldr x10,[x9,#8]
str x10,[x11,#8]
ldr w10,[x9,#16]
str w10,[x11,#16]
""")
code.append(('GLOBAL_ADRP',)); code.append(('GLOBAL_ADD',))      # -> x12
code.append(('CALLSTASH_ADRP',)); code.append(('CALLSTASH_ADD',))  # -> x13
code+=enc("ldr x14,[x12]\nstr x14,[x13]\n")
code+=enc("""ldp x0,x1,[sp,#0]
ldp x2,x3,[sp,#16]
ldp x4,x5,[sp,#32]
ldp x6,x30,[sp,#48]
add sp,sp,#80
""")
code.append(0xd503237f)  # replay the original pacibsp
code.append(bl(0,0))     # placeholder, patched below to a plain unconditional branch back to SITE+4
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd
for i,x in enumerate(code):
    va=CAVE+4*i
    if x==('STASH_ADRP',): x=adrp(11,va,STASH)
    elif x==('STASH_ADD',): x=0x91000000|((STASH&0xfff)<<10)|(11<<5)|11   # add x11,x11,#STASH&0xfff
    elif x==('GLOBAL_ADRP',): x=adrp(12,va,GLOBAL)
    elif x==('GLOBAL_ADD',): x=0x91000000|((GLOBAL&0xfff)<<10)|(12<<5)|12
    elif x==('CALLSTASH_ADRP',): x=adrp(13,va,CALLSTASH)
    elif x==('CALLSTASH_ADD',): x=0x91000000|((CALLSTASH&0xfff)<<10)|(13<<5)|13
    put(va,x)
tailva=CAVE+4*(len(code)-1)
put(tailva,(bl(tailva,SITE+4) & ~0xfc000000) | 0x14000000)
put(SITE,bl(SITE,CAVE))
open(k,'wb').write(d)
print('kextnameprobe',hex(SITE),'-> cave',hex(CAVE),'stash',hex(STASH),len(code),'insns')
