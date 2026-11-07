import struct,subprocess,json,sys,difflib
def load(K):
    d=open(K,'rb').read(); n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
    for _ in range(n):
        cmd,sz=struct.unpack_from('<II',d,p)
        if cmd==0x19:
            va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
        p+=sz
    return d,segs
O=load('/home/bluekyeet/SmallProject/.scratch/ios27/ipsw/24A446/kernelcache.decompressed')
N=load('/home/bluekyeet/SmallProject/.scratch/ios27-run5/k.macho')
names={}
for l in open('nm5.txt'):
    t=l.split()
    if len(t)==3 and all(c in '0123456789abcdef' for c in t[0]): names.setdefault(t[2],int(t[0],16))
def mn(img,va,n=48):
    d,segs=img
    o=[f+va-a for a,f,fs in segs if a<=va<a+fs][0]
    bs='\n'.join(' '.join('0x%02x'%b for b in d[o+4*i:o+4*i+4]) for i in range(n))
    out=[l.split()[0] for l in subprocess.run(['llvm-mc','--disassemble','-triple=aarch64','-mattr=+v8.5a,+pauth,+lse'],input=bs,capture_output=True,text=True).stdout.splitlines() if l.strip() and not l.strip().startswith('.')]
    seq=[]
    for m in out:
        if m in('movk',): continue
        seq.append(m)
        if m in ('ret','retab') : break
    return seq
for line in sys.stdin:
    k,name=line.split()[:2]
    if name not in names: print(k,name,'NOSYM'); continue
    a=mn(O,int(k,16)); b=mn(N,names[name])
    r=difflib.SequenceMatcher(None,a,b).ratio()
    print(k,name,round(r,2),len(a),len(b))
