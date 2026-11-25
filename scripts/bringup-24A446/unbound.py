"""List GOT slots still on _unixnu_fileset_unmapped, grouped by owning kext: orig kernelcache target + map name."""
import json,struct,subprocess,sys
K,ORIG=sys.argv[1],'/home/bluekyeet/SmallProject/.scratch/ios27/ipsw/24A446/kernelcache.decompressed'
BASE=0xfffffff007004000
def load(f):
    d=open(f,'rb').read(); n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]; ents=[]
    for _ in range(n):
        cmd,sz=struct.unpack_from('<II',d,p)
        if cmd==0x19:
            va,vs,fo,fs,mx,ip=struct.unpack_from('<QQQQII',d,p+24); segs.append((va,fo,fs,ip))
        if cmd==0x80000035:
            va,fo,io=struct.unpack_from('<QQI',d,p+8); ents.append((fo,d[p+io:d.index(b'\0',p+io)].decode()))
        p+=sz
    return d,segs,ents
c,cs,ents=load(K); o,os_,_=load(ORIG)
def off(segs,va):
    for a,f,fs,ip in segs:
        if a<=va<a+fs: return f+va-a
own=[]
for fo,name in ents:
    m=struct.unpack_from('<I',c,fo+16)[0]; q=fo+32
    for _ in range(m):
        cm,s=struct.unpack_from('<II',c,q)
        if cm==0x19:
            sva,svs=struct.unpack_from('<QQ',c,q+24); own.append((sva,svs,name))
        q+=s
def owner(va):
    for a,s,n in own:
        if a<=va<a+s: return n
stub=None
for l in subprocess.run(['llvm-nm',K],capture_output=True,text=True).stdout.splitlines():
    t=l.split()
    if len(t)==3 and t[2]=='_unixnu_fileset_unmapped': stub=int(t[0],16)
fm=json.load(open('/home/bluekyeet/SmallProject/.scratch/ios27-run4/meta/kernel/kernel.map.json'))['functions']
filt=sys.argv[2] if len(sys.argv)>2 else None
res={}
for a,f,fs,ip in cs:
    if ip&4: continue
    for va in range(a,a+fs,8):
        cv=struct.unpack_from('<Q',c,f+va-a)[0]
        if not cv>>63 or BASE+(cv&0x3fffffff)!=stub: continue
        oo=off(os_,va)
        if oo is None: continue
        ov=struct.unpack_from('<Q',o,oo)[0]
        if not ov>>63: continue
        t=BASE+(ov&0x3fffffff); n=owner(va)
        if filt and filt not in (n or ''): continue
        res.setdefault(n,set()).add(t)
for n,ts in sorted(res.items()):
    print('##',n,len(ts))
    for t in sorted(ts): print('  ',hex(t),(fm.get(hex(t)) or {}).get('name'))
