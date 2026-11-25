"""reach.py K ROOT [DEPTH]: walk bl-graph inside kext text from ROOT, report calls to stubs whose GOT slot in K is still unmapped."""
import struct,subprocess,sys,json
exec(open('show.py').read().split('fm=json')[0])   # d/off = kernelcache
B=0xfffffff007004000
K=sys.argv[1]; root=int(sys.argv[2],16); depth=int(sys.argv[3]) if len(sys.argv)>3 else 3
kd=open(K,'rb').read(); n=struct.unpack_from('<I',kd,16)[0]; p=32; ks=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',kd,p)
    if cmd==0x19:
        va,vs,fo,fs=struct.unpack_from('<QQQQ',kd,p+24); ks.append((va,fo,fs))
    p+=sz
def koff(va):
    for a,f,fs in ks:
        if a<=va<a+fs and fs: return f+va-a
stub=[int(l.split()[0],16) for l in subprocess.run(['llvm-nm',K],capture_output=True,text=True).stdout.splitlines() if l.endswith(' _unixnu_fileset_unmapped')][0]
fm=json.load(open('/home/bluekyeet/SmallProject/.scratch/ios27-run4/meta/kernel/kernel.map.json'))['functions']
lo,hi=root-0x40000,root+0x40000
def w(va): return struct.unpack_from('<I',d,off(va))[0]
def isstub(t):
    w0,w1,w2=w(t),w(t+4),w(t+8)
    if (w0&0x9f00001e)!=0x90000010: return None      # adrp x16|x17
    rd=w0&31; imm=((w0>>29)&3)|(((w0>>5)&0x7ffff)<<2); imm-=(1<<21) if imm&(1<<20) else 0
    pg=(t&~0xfff)+(imm<<12)
    if (w1&0xffc00000)==0x91000000 and ((w1>>5)&31)==rd and (w2&0xffc003e0)==(0xf9400000|(rd<<5)):   # add; ldr x16,[x17]
        return pg+((w1>>10)&0xfff)+((w2>>10)&0xfff)*8
    if (w1&0xffc00000)==0xf9400000 and ((w1>>5)&31)==rd: return pg+((w1>>10)&0xfff)*8               # ldr x16,[x16,#off]
    return None
seen=set(); res={}
def walk(f,dep,path):
    if f in seen or dep>depth: return
    seen.add(f); va=f
    for i in range(3000):
        x=w(va)
        if x>>26==0b100101 or (x>>26==0b000101 and i>0):
            dl=x&0x3ffffff; dl-=(1<<26) if dl&(1<<25) else 0; t=va+dl*4
            slot=isstub(t)
            if slot:
                cv=struct.unpack_from('<Q',kd,koff(slot))[0]; ov=struct.unpack_from('<Q',d,off(slot))[0]
                tgt=B+(ov&0x3fffffff)
                if B+(cv&0x3fffffff)==stub: res.setdefault(tgt,[]).append(hex(va))
            elif lo<=t<hi and x>>26==0b100101: walk(t,dep+1,path)
        if x in (0xd65f03c0,0xd65f0fff,0xd65f0bff) and w(va+4) in (0xd503237f,0xd503245f): break   # ret then new function
        va+=4
walk(root,0,[])
for t,s in sorted(res.items()): print(hex(t),(fm.get(hex(t)) or {}).get('name'),'from',s[:4])
print('functions walked',len(seen))
