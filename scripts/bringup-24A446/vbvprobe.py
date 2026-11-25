"""Bring-up probe: at valid_bootable_volume entry, IOLog the container superblock fields APFS uses
(pointer, magic at +32, nx_max_file_systems at +180, nx_fs_oid[0..1] at +184), then continue."""
import struct,subprocess,re,sys
k=sys.argv[1]
d=bytearray(open(k,'rb').read())
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
IOLOG=names['_IOLog']
SITE=0xfffffff00a1c18b8; assert W(SITE)==0xf9406008, hex(W(SITE))   # ldr x8,[x0,#192]
CAVE=0xfffffff00ab13400; FMT=CAVE+0x100
assert not any(d[off(CAVE):off(CAVE)+0x180]), 'cave not empty'
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True).stdout
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in o.splitlines() if 'encoding:' in l]
bl=lambda frm,to:0x94000000|(((to-frm)>>2)&0x3ffffff)
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd
code=enc("""sub sp,sp,#160
stp x0,x1,[sp,#48]
stp x2,x3,[sp,#64]
stp x4,x5,[sp,#80]
stp x6,x7,[sp,#96]
stp x9,x10,[sp,#112]
stp x29,x30,[sp,#128]
ldr x8,[x0,#192]
str x8,[sp,#0]
ldr w9,[x8,#32]
str x9,[sp,#8]
ldr w9,[x8,#180]
str x9,[sp,#16]
ldr x9,[x8,#184]
str x9,[sp,#24]
ldr x9,[x8,#192]
str x9,[sp,#32]
""")
code.append(('ADRP',)); code += enc(f"add x0,x0,#{FMT&0xfff}\n"); code.append(('BL',))
code += enc("""ldp x0,x1,[sp,#48]
ldp x2,x3,[sp,#64]
ldp x4,x5,[sp,#80]
ldp x6,x7,[sp,#96]
ldp x9,x10,[sp,#112]
ldp x29,x30,[sp,#128]
add sp,sp,#160
ldr x8,[x0,#192]
ret
""")
for i,x in enumerate(code):
    va=CAVE+4*i
    if x==('ADRP',): x=adrp(0,va,FMT)
    elif x==('BL',): x=bl(va,IOLOG)
    put(va,x)
fmt=b'VBV sb %llx magic %llx maxfs %llx oid0 %llx oid1 %llx\n\0'
d[off(FMT):off(FMT)+len(fmt)]=fmt
put(SITE,bl(SITE,CAVE))
open(k,'wb').write(d)
print('vbv probe',hex(SITE),'->',hex(CAVE),len(code),'insns')
