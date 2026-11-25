"""Rebind GOT slots of retained kexts from _unixnu_fileset_unmapped to reviewed native functions (test only)."""
import json,struct,subprocess,sys
orig_path,cand,out,review=sys.argv[1:5]
BASE=0xfffffff007004000
def load(f):
    d=bytearray(open(f,'rb').read())
    n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
    for _ in range(n):
        cmd,sz=struct.unpack_from('<II',d,p)
        if cmd==0x19:
            nm=d[p+8:p+24].rstrip(b'\0').decode(); va,vs,fo,fs,mx,ip=struct.unpack_from('<QQQQII',d,p+24); segs.append((nm,va,fo,fs,ip))
        p+=sz
    return d,segs
o,osegs=load(orig_path); c,csegs=load(cand)
names={}
for l in subprocess.run(['llvm-nm',cand],capture_output=True,text=True).stdout.splitlines():
    t=l.split()
    if len(t)==3 and all(ch in '0123456789abcdef' for ch in t[0]): names.setdefault(t[2],int(t[0],16))
stub=names['_unixnu_fileset_unmapped']
want={int(k,16):names[v] for k,v in json.load(open(review)).items()}
# Overrides: kernelcache target -> native name, applied even where the prelinker map already bound the slot (map errors).
import os
over={int(k,16):names[v] for k,v in (json.load(open(os.environ['GOT_OVERRIDE'])).items() if os.environ.get('GOT_OVERRIDE') else [])}
def off(segs,va):
    for nm,a,f,fs,ip in segs:
        if a<=va<a+fs: return f+va-a
count={}
for nm,a,f,fs,ip in csegs:
    if ip&4: continue   # data only
    for va in range(a,a+fs,8):
        cv=struct.unpack_from('<Q',c,f+va-a)[0]
        if not cv>>63: continue
        oo=off(osegs,va)
        if oo is None: continue
        ov=struct.unpack_from('<Q',o,oo)[0]
        if not ov>>63: continue
        t=BASE+(ov&0x3fffffff)
        if t in over:
            nv=(cv&~0x3fffffff)|((over[t]-BASE)&0x3fffffff)
            struct.pack_into('<Q',c,f+va-a,nv); count['over '+hex(t)]=count.get('over '+hex(t),0)+1; continue
        if BASE+(cv&0x3fffffff)!=stub: continue
        if t in want:
            nv=(cv&~0x3fffffff)|((want[t]-BASE)&0x3fffffff)
            struct.pack_into('<Q',c,f+va-a,nv); count[hex(t)]=count.get(hex(t),0)+1
open(out,'wb').write(c)
print('rebound',sum(count.values()),'slots',count)
print('stub at',hex(stub))
