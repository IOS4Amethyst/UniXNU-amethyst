"""Turn _unixnu_fileset_unmapped into log-and-return-0 (bring-up only): one console line per call, boot continues."""
import struct,subprocess,sys
k=sys.argv[1]
d=bytearray(open(k,'rb').read())
n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,p)
    if cmd==0x19:
        va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
    p+=sz
def off(va):
    for a,f,fs in segs:
        if a<=va<a+fs: return f+va-a
names={}
for l in subprocess.run(['llvm-nm',k],capture_output=True,text=True).stdout.splitlines():
    t=l.split()
    if len(t)==3 and all(c in '0123456789abcdef' for c in t[0]): names.setdefault(t[2],int(t[0],16))
STUB=names['_unixnu_fileset_unmapped']; PRINTF=names['_IOLog']
CAVE=0xfffffff00ab12400; FMT=CAVE+0x100
assert not any(d[off(CAVE):off(CAVE)+0x140]), 'cave not empty'
w=lambda va:struct.unpack_from('<I',d,off(va))[0]
assert w(STUB)==0xd503237f, 'stub does not start with pacibsp'
def b(frm,to,link=False): return (0x94000000 if link else 0x14000000)|(((to-frm)>>2)&0x3ffffff)
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd
def addimm(rd,rn,imm): return 0x91000000|((imm&0xfff)<<10)|(rn<<5)|rd
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True).stdout
    import re; return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in o.splitlines() if 'encoding:' in l]
pre=enc("sub sp,sp,#48\nstp x29,x30,[sp,#32]\nadd x29,sp,#32\nmov x8,x30\nxpaci x8\nmovz x9,#0x4000\nmovk x9,#0x0700,lsl #16\nmovk x9,#0xfff0,lsl #32\nmovk x9,#0xffff,lsl #48\nsub x8,x8,x9\nmov w10,w0\nmov w11,w1\nstp x10,x11,[sp,#8]\nstr x8,[sp]\n")
code=pre[:]
va=CAVE+4*len(code); code.append(adrp(0,va,FMT)); code.append(addimm(0,0,FMT&0xfff))
va=CAVE+4*len(code); code.append(b(va,PRINTF,True))
code+=enc("mov x0,#0\nldp x29,x30,[sp,#32]\nadd sp,sp,#48\nretab\n")
for i,x in enumerate(code): struct.pack_into('<I',d,off(CAVE)+4*i,x)
fmt=b'UNMAPPED +%llx x0 %llx x1 %llx\n\0'
d[off(FMT):off(FMT)+len(fmt)]=fmt
struct.pack_into('<I',d,off(STUB+4),b(STUB+4,CAVE))
open(k,'wb').write(d)
print('stub',hex(STUB),'-> cave',hex(CAVE),'printf',hex(PRINTF),len(code),'insns')
