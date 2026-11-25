"""Bring-up probe: at KLDBootstrap::loadKernelComponentKexts's entry, silently copy the live 8-byte
value of sAllKextLoadIdentifiers into a fixed scratch slot, then continue normally (no freeze, no
printing -- this must not disturb the loop that follows, since badcastfreeze.py's crash-site freeze
later reads this slot to compare "value when the loop started" against "value at the crash", within
the SAME boot -- the three single-boot readings taken so far (i94/i95/i96) were each a separate boot
and might just reflect normal heap-address randomization between boots, not an evolving corruption.
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
GLOBAL=0xfffffff00af67520   # sAllKextLoadIdentifiers's live storage slot (4 refs in k.macho, 0 in the kc)
ENTRYSTASH=0xfffffff00b0d7f80   # 8 bytes, 64 bytes before STASH (kextnameprobe.py's 0xb0d7fc0) --
                                # same zero-fill __DATA tail, confirmed clear below.
SITE=0xfffffff00ab10a2c; CAVE=0xfffffff00ab12160
assert W(SITE)==0xd503237f, ('site not pacibsp',hex(W(SITE)))
assert not any(d[off(CAVE):off(CAVE)+0x40]), 'cave not empty'
assert not any(d[off(ENTRYSTASH):off(ENTRYSTASH)+8]), 'entrystash not empty'
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True)
    r=o.stdout.splitlines()
    if o.stderr.strip() or any('<MCInst' in l or 'encoding: [0b' in l for l in r):
        raise SystemExit('assembly did not fully resolve:\n'+o.stdout+'\n'+o.stderr)
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in r if 'encoding:' in l]
bl=lambda frm,to:0x94000000|(((to-frm)>>2)&0x3ffffff)
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd

code = enc("stp x8,x9,[sp,#-16]!\n")
code.append(('G_ADRP',)); code.append(('G_ADD',))
code.append(('E_ADRP',)); code.append(('E_ADD',))
code += enc("""ldr x9,[x8]
str x9,[x10]
ldp x8,x9,[sp],#16
""")
code.append(0xd503237f)   # replay the original pacibsp
code.append(bl(0,0))      # placeholder -> plain b SITE+4

for i,x in enumerate(code):
    va=CAVE+4*i
    if x==('G_ADRP',): x=adrp(8,va,GLOBAL)
    elif x==('G_ADD',): x=0x91000000|((GLOBAL&0xfff)<<10)|(8<<5)|8
    elif x==('E_ADRP',): x=adrp(10,va,ENTRYSTASH)
    elif x==('E_ADD',): x=0x91000000|((ENTRYSTASH&0xfff)<<10)|(10<<5)|10
    put(va,x)
tailva=CAVE+4*(len(code)-1)
put(tailva,(bl(tailva,SITE+4) & ~0xfc000000) | 0x14000000)
put(SITE,bl(SITE,CAVE))
open(k,'wb').write(d)
print('lkcentrystash',hex(SITE),'-> cave',hex(CAVE),len(code),'insns, entrystash',hex(ENTRYSTASH))
