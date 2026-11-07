"""Bring-up only: replace one instruction with `b .` so the console log stays on screen."""
import struct,sys
k=sys.argv[1]; addrs=[int(a,16) for a in sys.argv[2:]]
d=bytearray(open(k,'rb').read())
n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,p)
    if cmd==0x19:
        va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
    p+=sz
for a in addrs:
    o=[f+a-v for v,f,fs in segs if v<=a<v+fs][0]
    print(hex(a),'was',hex(struct.unpack_from('<I',d,o)[0]),'-> b .'); struct.pack_into('<I',d,o,0x14000000)
open(k,'wb').write(d)
