"""dumpk.py kc|nat START COUNT : linear disassembly (no stop at ret), native side labelled from nm5.txt."""
import struct,subprocess,sys
F={'kc':'/home/bluekyeet/SmallProject/.scratch/ios27/ipsw/24A446/kernelcache.decompressed','nat':'/home/bluekyeet/SmallProject/.scratch/ios27-run5/k.macho'}[sys.argv[1]]
d=open(F,'rb').read(); n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,p)
    if cmd==0x19:
        va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
    p+=sz
def off(va):
    for a,f,fs in segs:
        if a<=va<a+fs and fs: return f+va-a
lab={}
if sys.argv[1]=='nat':
    for l in open('nm5.txt'):
        t=l.split()
        if len(t)==3 and t[1] in 'sStT': lab.setdefault(int(t[0],16),t[2])
s=int(sys.argv[2],16); c=int(sys.argv[3])
w=[struct.unpack_from('<I',d,off(s+4*i))[0] for i in range(c)]
bs='\n'.join(' '.join('0x%02x'%b for b in struct.pack('<I',x)) for x in w)
out=[l.strip() for l in subprocess.run(['llvm-mc','--disassemble','-triple=aarch64','-mattr=+v8.5a,+pauth,+lse'],input=bs,capture_output=True,text=True).stdout.splitlines() if l.strip() and not l.strip().startswith('.')]
for i in range(c):
    va=s+4*i
    if va in lab: print('<'+lab[va]+'>')
    print('%x %s'%(va&0xffffff,out[i].split('//')[0] if i<len(out) else '?'))
