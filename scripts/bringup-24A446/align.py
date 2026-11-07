"""align.py KC_START KC_END NAT_START NAT_END : kc function starts (with known names) vs native symbols."""
import sys,json,subprocess
ks,ke,ns,ne=[int(x,16) for x in sys.argv[1:5]]
fm=json.load(open('/home/bluekyeet/SmallProject/.scratch/ios27-run4/meta/kernel/kernel.map.json'))['functions']
known={int(k,16):v['name']+'(map)' for k,v in fm.items()}
for f in ('bind-i71.json',):
    for k,v in json.load(open(f)).items(): known[int(k,16)]=v+'(bind)'
want=set(int(x,16) for x in sys.argv[5:])
out=subprocess.run(['python3','dumpk.py','kc',hex(ks),str((ke-ks)//4)],capture_output=True,text=True).stdout.splitlines()
starts=[];p='ret'
for l in out:
    a=l.split()
    if len(a)<2: continue
    op=a[1]
    if (op=='pacibsp' or (op=='bti' and a[2]=='c')) and p in('ret','retab','b','brk'): starts.append(0xfffffff00a000000|int(a[0],16))
    p=op
ks_=[]
for s in starts:
    ks_.append('%x %s%s'%(s&0xffffff,known.get(s,''),' <<<' if s in want else ''))
nat=[]
for l in open('nm5.txt'):
    t=l.split()
    if len(t)==3 and t[1] in 'sStT' and ns<=int(t[0],16)<ne: nat.append('%x %s'%(int(t[0],16)&0xffffff,t[2]))
from itertools import zip_longest
for a,b in zip_longest(ks_,nat,fillvalue=''): print('%-60s %s'%(a,b))
