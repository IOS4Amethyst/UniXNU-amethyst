import sys,struct,hashlib
def der(d,at):
    tag,sz=d[at],d[at+1]; at+=2
    if sz&128:
        c=sz&127; sz=int.from_bytes(d[at:at+c],'big'); at+=c
    return tag,d[at:at+sz],at+sz
def payload(path):
    o=open(path,'rb').read(); t,seq,e=der(o,0); f=[];c=0
    while c<len(seq):
        k,v,c=der(seq,c); f.append((k,v))
    assert f[0][1]==b'IM4P', f[0]
    return f[1][1].decode(), f[3][1], len(f)
if __name__=='__main__':
    for p in sys.argv[1:]:
        typ,m,nf=payload(p)
        ver=struct.unpack_from('<I',m)[0]; n=struct.unpack_from('<I',m,20)[0]
        print(p.split('/')[-1],typ,'fields',nf,'ver',ver,'n',n,'len',len(m),'v2ok',len(m)==24+n*24,m[:4].hex())
