"""Rebind kext data slots still holding an unresolved original data address to the reviewed native object (test only)."""
import json,struct,subprocess,sys
orig_path,cand,out,review=sys.argv[1:5]
BASE=0xfffffff007004000
def load(f):
    d=bytearray(open(f,'rb').read())
    n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
    for _ in range(n):
        cmd,sz=struct.unpack_from('<II',d,p)
        if cmd==0x19:
            va,vs,fo,fs,mx,ip=struct.unpack_from('<QQQQII',d,p+24); segs.append((va,fo,fs,ip))
        p+=sz
    return d,segs
o,osegs=load(orig_path); c,csegs=load(cand)
names={}
for l in subprocess.run(['llvm-nm',cand],capture_output=True,text=True).stdout.splitlines():
    t=l.split()
    if len(t)==3 and all(ch in '0123456789abcdef' for ch in t[0]): names.setdefault(t[2],int(t[0],16))
want={int(k,16):names[v] for k,v in json.load(open(review)).items()}
def off(segs,va):
    for a,f,fs,ip in segs:
        if a<=va<a+fs: return f+va-a
count={}
for a,f,fs,ip in csegs:
    if ip&4: continue
    for va in range(a,a+fs,8):
        cv=struct.unpack_from('<Q',c,f+va-a)[0]
        if not cv or cv>>63: continue
        t=BASE+(cv&0x3fffffff)
        if t not in want: continue
        oo=off(osegs,va)
        if oo is None or struct.unpack_from('<Q',o,oo)[0]!=cv: continue    # untouched since the original
        struct.pack_into('<Q',c,f+va-a,(cv&~0x3fffffff)|((want[t]-BASE)&0x3fffffff)); count[hex(t)]=count.get(hex(t),0)+1
open(out,'wb').write(c)
print('rebound',sum(count.values()),'data slots',count)
