import struct,sys
exec(open('show.py').read().split('def cstr')[0])
B=0xfffffff007004000
n=struct.unpack_from('<I',d,16)[0]; p=32; ents=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,p)
    if cmd==0x80000035:
        va,fo,no=struct.unpack_from('<QQI',d,p+8); ents.append((va,d[p+no:d.find(b'\0',p+no)].decode()))
    p+=sz
segmap=[]
for va,name in ents:
    o=off(va); m=struct.unpack_from('<I',d,o+16)[0]; q=o+32
    for _ in range(m):
        c,s=struct.unpack_from('<II',d,q)
        if c==0x19:
            sva,svs=struct.unpack_from('<QQ',d,q+24); segmap.append((sva,svs,name,d[q+8:q+24].rstrip(b'\0').decode()))
        q+=s
for x in sys.argv[1:]:
    t=B+int(x,16)
    print(x,hex(t),[(nm,sg) for a,s,nm,sg in segmap if a<=t<a+s])
