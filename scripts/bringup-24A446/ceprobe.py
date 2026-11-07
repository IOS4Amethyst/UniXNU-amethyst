"""Bring-up probe: at the three 'mov w0,#14' failure exits of AMFI's CE->OSObject converter, IOLog a tag and spin.

BUG FIX (this revision): every earlier version of this file stored the values it wanted to print
on the stack (`str x0,[sp,#0]`, `stp x9,x10,[sp,#8]`, ...) and never loaded them into x1..x7.
`IOLog` is variadic: with the format string in x0, the first seven conversions are read from
x1..x7 and only the eighth onwards come from the stack. So a six-conversion format like
`'CEIT err %llx ctx %llx %llx %llx %llx %llx'` read six *leftover* registers and never touched
the stack slots at all. Everything those probes printed was uninitialised garbage.

That is where the widely quoted "0x620-byte DER blob" came from: it was never a length, it was
whatever happened to be in a caller-saved register. Do not build on that number.

This version loads the values into x1..x6 in order before the call.
"""
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
# i85 result: masking the context words to 32 bits did NOT stop IOLog printing "<ptr>" for them,
# while the error code (also %llx, also in the same call) printed as a real small integer. That
# rules out a value-magnitude heuristic. IOLog is a thin wrapper that composes through an
# os_log-style path (see its own disassembly: it builds a va_list save area, then re-emits through
# a second call using a fixed "%s" format) and that path appears to redact some argument classes
# regardless of value. kprintf is a different, lower-level function -- a direct vararg console
# printf with no such composition step -- and it is already enabled by this bring-up's own boot
# args (-enable_kprintf_spam is in unixnu.toml). Use it instead for this probe.
IOLOG=names['_IOLog']
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True).stdout
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in o.splitlines() if 'encoding:' in l]
bl=lambda frm,to:0x94000000|(((to-frm)>>2)&0x3ffffff)
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd
MOV14=0x528001c0   # mov w0,#14

# Each site is (site_va, cave_va, format, setup_code).
#
# For the IT site the six values are: iterate error code, then five words of the CE context at
# x21 (the OSEntitlements/CE pod held in the converter's arg0). We load them straight into
# x1..x6 because that is where IOLog reads them.
#
# NOTE: at the IT site x0 still holds the iterate return code, so capture it first and use x9/x10
# as scratch -- they are caller-saved and IOLog may clobber them.
#
# REVERTED (this revision): tried masking p8..p40 to 32 bits (did not stop "<ptr>"), then tried
# calling _kprintf instead of _IOLog (DID stop "<ptr>" from the little that could be read, but
# caused a reproducible freeze much earlier in boot -- confirmed on two independent boots of the
# identical image, same freeze point both times, right after root volume mount, well before
# launchd's own exec). AMFI's kext is active from very early boot (its own log lines appear before
# root volume mounts), so this converter is plausibly reached for other purposes earlier than
# launchd's exec, and something about reaching it through kprintf there behaves differently than
# through IOLog. Not pursuing that further -- reverted to IOLog + masking, the combination proven
# to reach the real blocker reliably (two independent builds, i84 and i85, both landed on this
# exact CEIT site with the same err value). p8..p40 will still print as "<ptr>"; err does not.
IT=(0xfffffff008f7dd50,0xfffffff00ab13700,
    b'CEIT err %llx p8 %llx p16 %llx p24 %llx p32 %llx p40 %llx\n\0',
    # x0 = error code from the iterate call. Park it, then load x1..x6 and mask the pointer-shaped ones.
    'mov w9, w0\n'
    'ldp x1,x2,[x21,#8]\n'
    'ldp x3,x4,[x21,#24]\n'
    'ldr x5,[x21,#40]\n'
    'mov x6, x9\n'
    'mov w1, w1\nmov w2, w2\nmov w3, w3\nmov w4, w4\nmov w5, w5\n')

# The three 'mov w0,#14' exits. CEFAIL A is the walker-returned-null case: the walker type word
# lives in the converter's local frame, but the frame offset depends on where we patch, so only
# log the context pointer here and read the type from the caller's own trace if needed.
CEA=(0xfffffff008f7dc44,0xfffffff00ab13400,b'CEFAIL A walker-null\n\0','')
CEB=(0xfffffff008f7deb0,0xfffffff00ab13500,b'CEFAIL B dict-entry\n\0','')
CEC=(0xfffffff008f7df10,0xfffffff00ab13600,b'CEFAIL C array-elem\n\0','')
sites=[IT,CEA,CEB,CEC]

for SITE,CAVE,fmt,setup in sites:
    FMT=CAVE+0xc0
    assert W(SITE)==(0x350002e0 if SITE==0xfffffff008f7dd50 else MOV14), (hex(SITE),hex(W(SITE)))   # cbnz w0,+92 at the IT site
    assert not any(d[off(CAVE):off(CAVE)+0x100]), 'cave not empty'
    code=[0x35000040,0xd65f03c0] if SITE==0xfffffff008f7dd50 else []   # IT: cbnz w0,+8 ; ret  (success path returns to the original 'b' after the call)
    code+=enc("sub sp,sp,#96\nstp x0,x30,[sp,#64]\n"+setup)
    code.append(('ADRP',)); code+=enc(f"add x0,x0,#{FMT&0xfff}\n"); code.append(('BL',))
    code.append(0x14000000)      # b .  (keep the log on screen)
    for i,x in enumerate(code):
        va=CAVE+4*i
        if x==('ADRP',): x=adrp(0,va,FMT)
        elif x==('BL',): x=bl(va,IOLOG)
        put(va,x)
    assert 4*len(code)<0xc0
    d[off(FMT):off(FMT)+len(fmt)]=fmt
    put(SITE,bl(SITE,CAVE))
    print('ceprobe',hex(SITE),'->',hex(CAVE))
open(k,'wb').write(d)
