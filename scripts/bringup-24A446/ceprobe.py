"""Bring-up probe: at the three 'mov w0,#14' failure exits of AMFI's CE->OSObject converter, IOLog a tag and spin."""
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
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True).stdout
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in o.splitlines() if 'encoding:' in l]
bl=lambda frm,to:0x94000000|(((to-frm)>>2)&0x3ffffff)
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd
MOV14=0x528001c0   # mov w0,#14
IT=(0xfffffff008f7dd50,0xfffffff00ab13700,b'CEIT err %llx ctx %llx %llx %llx %llx %llx\n\0',
    'str x0,[sp,#0]\nldp x9,x10,[x21,#8]\nstp x9,x10,[sp,#8]\nldp x9,x10,[x21,#24]\nstp x9,x10,[sp,#24]\nldr x9,[x21,#40]\nstr x9,[sp,#40]\n')
sites=[IT,(0xfffffff008f7dc44,0xfffffff00ab13400,b'CEFAIL A walker-null type %llx\n\0','ldr w9,[sp,#96]\nstr x9,[sp,#0]\n'),
       (0xfffffff008f7deb0,0xfffffff00ab13500,b'CEFAIL B dict-entry\n\0',''),
       (0xfffffff008f7df10,0xfffffff00ab13600,b'CEFAIL C array-elem\n\0','')]
for SITE,CAVE,fmt,extra in sites:
    FMT=CAVE+0xc0
    assert W(SITE)==(0x350002e0 if SITE==0xfffffff008f7dd50 else MOV14), (hex(SITE),hex(W(SITE)))   # cbnz w0,+92 at the IT site
    assert not any(d[off(CAVE):off(CAVE)+0x100]), 'cave not empty'
    code=[0x35000040,0xd65f03c0] if SITE==0xfffffff008f7dd50 else []   # IT: cbnz w0,+8 ; ret  (success path returns to the original 'b' after the call)
    code+=enc("sub sp,sp,#96\nstp x0,x30,[sp,#64]\n"+extra)
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
