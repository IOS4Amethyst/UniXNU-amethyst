"""LR of a call into _unixnu_fileset_unmapped -> stub, GOT slot, original target, owning fileset entry."""
import struct,sys
BASE=0xfffffff007004000
def load(f):
    d=open(f,'rb').read()
    n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]; ents=[]
    for _ in range(n):
        cmd,sz=struct.unpack_from('<II',d,p)
        if cmd==0x19:
            va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
        if cmd==0x80000035:
            va,fo,io=struct.unpack_from('<QQI',d,p+8); ents.append((va,fo,d[p+io:d.index(b'\0',p+io)].decode()))
        p+=sz
    def off(va):
        for a,f_,fs in segs:
            if a<=va<a+fs: return f_+va-a
    return d,off,ents
c,coff,ents=load('/home/bluekyeet/SmallProject/.scratch/ios27-run5/k.macho')
o,ooff,_=load('/home/bluekyeet/SmallProject/.scratch/ios27/ipsw/24A446/kernelcache.decompressed')
def owner(va):
    # entry whose text contains va: parse each entry's segments
    best=None
    for eva,efo,name in ents:
        n=struct.unpack_from('<I',c,efo+16)[0]; p=efo+32
        for _ in range(n):
            cmd,sz=struct.unpack_from('<II',c,p)
            if cmd==0x19:
                sva,svs=struct.unpack_from('<QQ',c,p+24)
                if sva<=va<sva+svs: return name+' '+c[p+8:p+24].rstrip(b'\0').decode()
            p+=sz
for lr in sys.argv[1:]:
    lr=int(lr,16); call=lr-4
    w=struct.unpack_from('<I',c,coff(call))[0]
    if w>>26!=0b100101: print(hex(lr),'not after bl', hex(w)); continue
    dl=w&0x3ffffff; dl-=(1<<26) if dl&(1<<25) else 0; t=call+dl*4
    w0,w1=struct.unpack_from('<II',c,coff(t))
    imm=((w0>>29)&3)|(((w0>>5)&0x7ffff)<<2); imm-=(1<<21) if imm&(1<<20) else 0
    slot=(t&~0xfff)+(imm<<12)+((w1>>10)&0xfff)
    ov=struct.unpack_from('<Q',o,ooff(slot))[0]
    print(hex(lr),'in',owner(call),'stub',hex(t),'slot',hex(slot),'orig target',hex(BASE+(ov&0x3fffffff)),'owner',owner(BASE+(ov&0x3fffffff)))
