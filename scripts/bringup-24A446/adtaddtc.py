"""adtaddtc.py IN OUT: add a 16-byte zero 'TrustCache' property to /chosen/memory-map (as unixnu devicetree.py does with trust=True)."""
import struct,sys
d=open(sys.argv[1],'rb').read()
def parse(o):
    np,nc=struct.unpack_from('<II',d,o); p=o+8; props=[]
    for _ in range(np):
        name=d[p:p+32].rstrip(b'\0'); ln=struct.unpack_from('<I',d,p+32)[0]; L=ln&0x7fffffff
        props.append([name,ln,d[p+36:p+36+L]]); p+=36+((L+3)&~3)
    kids=[]
    for _ in range(nc):
        k,p=parse(p); kids.append(k)
    return [props,kids],p
def ser(n):
    props,kids=n; out=struct.pack('<II',len(props),len(kids))
    for name,ln,v in props:
        out+=name.ljust(32,b'\0')+struct.pack('<I',ln)+v+b'\0'*(((len(v)+3)&~3)-len(v))
    return out+b''.join(ser(k) for k in kids)
def nm(n): return next((v.rstrip(b'\0') for k,l,v in n[0] if k==b'name'),None)
root,end=parse(0); assert end==len(d) and ser(root)==d
ch=next(k for k in root[1] if nm(k)==b'chosen'); mm=next(k for k in ch[1] if nm(k)==b'memory-map')
assert not any(k==b'TrustCache' for k,_,_ in mm[0])
i=next((j for j,(k,_,_) in enumerate(mm[0]) if k in (b'GpuAddon',b'RootPolicies')),len(mm[0])); mm[0].insert(i,[b'TrustCache',16,bytes(16)])
open(sys.argv[2],'wb').write(ser(root)); print('memory-map props:',[k.decode() for k,_,_ in mm[0]])
