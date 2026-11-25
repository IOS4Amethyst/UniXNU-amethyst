"""Bring-up probe: after each static MAC policy's cred_label_update_execve hook, IOLog 'MACX <policy> idx <i> ret <r>'."""
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
IOLOG=names['_IOLog']
SITE=0xfffffff00aa18af4; assert W(SITE)==0xaa1303e1, hex(W(SITE))   # mov x1,x19
CAVE=0xfffffff00ab13c00; FMT=CAVE+0x100
assert not any(d[off(CAVE):off(CAVE)+0x180]), 'cave not empty'
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True).stdout
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in o.splitlines() if 'encoding:' in l]
bl=lambda frm,to:0x94000000|(((to-frm)>>2)&0x3ffffff)
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd
code=enc("""sub sp,sp,#64
stp x0,x30,[sp,#32]
ldr x9,[x23,#24]
ldr x9,[x9,x27,lsl #3]
ldr x9,[x9]
str x9,[sp,#0]
str x27,[sp,#8]
str x0,[sp,#16]
""")
code.append(('ADRP',)); code += enc(f"add x0,x0,#{FMT&0xfff}\n"); code.append(('BL',))
code += enc("""ldp x0,x30,[sp,#32]
add sp,sp,#64
""")
code += [0xb4000040, 0x14000000]          # cbz x0,+8 ; b .  (spin on a non-zero hook result so the log stays on screen)
code += enc("""mov x1,x19
ret
""")
for i,x in enumerate(code):
    va=CAVE+4*i
    if x==('ADRP',): x=adrp(0,va,FMT)
    elif x==('BL',): x=bl(va,IOLOG)
    put(va,x)
fmt=b'MACX %s idx %llx ret %llx\n\0'
d[off(FMT):off(FMT)+len(fmt)]=fmt
put(SITE,bl(SITE,CAVE))
open(k,'wb').write(d); print('mxprobe',hex(SITE),'->',hex(CAVE),len(code),'insns')
