"""Bring-up probe: at KLDBootstrap::loadKernelComponentKexts's entry, right before its loop over sKernelComponentNames (which later reaches and crashes on com.apple.kpi.bsd), hex-render the live value of the
static global sAllKextLoadIdentifiers (an OSSharedPtr<OSSet>, set exactly once inside initialize()
via OSSet::withCapacity()) and hang. initialize() runs exactly once during boot, so no conditional
guard is needed here (unlike badcastfreeze.py, which patches a function called thousands of times).

Purpose: determine whether the global is already corrupted (0xdc008005, matching the later crash's
bad pointer) the moment initialize() finishes, or whether it's still valid/null there and gets
overwritten by something else afterward -- two very different bugs with the same symptom.

Global's absolute storage address comes from OSKext::initialize() itself, which is authoritative:
it stores the freshly built OSSet (capacity 150, kOSKextTypicalLoadCount) at
0xfffffff00af99ed0 with `str x21, [x26, #3792]` at 0xfffffff00aa748f0, and
OSKext::recordIdentifierRequest reads that same slot back with `ldr x8, [x23, #3792]` at
0xfffffff00aa78810. The nine globals initialize() builds form one ascending array
(sKextsByID 0xaf99ea0, sLoadedKexts 0xaf99ea8, sLoadedDriverKitKexts 0xaf99eb0,
sUnloadedPrelinkedKexts 0xaf99eb8 [capacity 15 = 150/10], sKernelRequests 0xaf99ec0,
sPostedKextLoadIdentifiers 0xaf99ec8, sAllKextLoadIdentifiers 0xaf99ed0,
sRequestCallbackRecords 0xaf99ed8, sDriverKitToUpgradeByID 0xaf99ee0 [capacity 5]), and
0xaf99ed0 is the only member with capacity 150 that is an OSSet read by a "set" operation.
An earlier revision of this file used 0xfffffff00af67520, derived by hand from the wrong
address (0xa8e9fec does not hold recordIdentifierRequest; the real one is 0xaa787ec), and
that slot is plain zeroed __DATA that nothing in the kernel ever touches -- every reading
taken from it was meaningless.
"""
import struct,subprocess,re,sys
k=sys.argv[1]; d=bytearray(open(k,'rb').read())
n=struct.unpack_from('<I',d,16)[0]; p=32; segs=[]
for _ in range(n):
    cmd,sz=struct.unpack_from('<II',d,p)
    if cmd==0x19:
        va,vs,fo,fs=struct.unpack_from('<QQQQ',d,p+24); segs.append((va,fo,fs))
    p+=sz
off=lambda va:[f+va-a for a,f,fs in segs if a<=va<a+fs][0]
W=lambda va:struct.unpack_from('<I',d,off(va))[0]
put=lambda va,x:struct.pack_into('<I',d,off(va),x)
names={}
for l in subprocess.run(['llvm-nm',k],capture_output=True,text=True).stdout.splitlines():
    t=l.split()
    if len(t)==3 and all(c in '0123456789abcdef' for c in t[0]): names.setdefault(t[2],int(t[0],16))
IOLOG=names['_IOLog']
GLOBAL=0xfffffff00af67520
RENDER=0xfffffff00b0d6fc0   # same writable __DATA scratch used by badcastfreeze.py; this probe
                            # runs and finishes (hangs) well before that one is ever reached, so
                            # reusing it is safe -- nothing else writes here in between.
SITE=0xfffffff00ab10a2c; CAVE=0xfffffff00ab12160
assert W(SITE)==0xd503237f, ('site not pacibsp',hex(W(SITE)))
assert not any(d[off(CAVE):off(CAVE)+0x280]), 'cave not empty'
assert not any(d[off(RENDER):off(RENDER)+64]), 'render buffer not empty'
def enc(s):
    o=subprocess.run(['llvm-mc','-triple=aarch64','-mattr=+v8.5a,+pauth','-show-encoding'],input=s,capture_output=True,text=True)
    r=o.stdout.splitlines()
    if o.stderr.strip() or any('<MCInst' in l or 'encoding: [0b' in l for l in r):
        raise SystemExit('assembly did not fully resolve:\n'+o.stdout+'\n'+o.stderr)
    return [int(''.join(reversed(re.findall(r'0x([0-9a-f]{2})',l.split('encoding:')[1]))),16) for l in r if 'encoding:' in l]
bl=lambda frm,to:0x94000000|(((to-frm)>>2)&0x3ffffff)
def adrp(rd,frm,to):
    imm=((to&~0xfff)-(frm&~0xfff))>>12; return 0x90000000|((imm&3)<<29)|(((imm>>2)&0x7ffff)<<5)|rd

def hex_byte_block(load_text):
    return load_text + f"""
lsr w9,w8,#4
and w10,w8,#0xf
cmp w9,#10
b.lo #12
add w9,w9,#{ord('a')-10}
b #8
add w9,w9,#{ord('0')}
strb w9,[x6],#1
cmp w10,#10
b.lo #12
add w10,w10,#{ord('a')-10}
b #8
add w10,w10,#{ord('0')}
strb w10,[x6],#1
"""

ID_OFF = 8   # "SALLKLID" (8 bytes) prefix, hex digits of the 8-byte global value start right after

body = "mov x5,x11\nadd x6,x12,#{}\n".format(ID_OFF)
for i in range(8):
    body += hex_byte_block(f"ldrb w8,[x5,#{i}]\n")
body += "mov x0,#0x0a\nstrb w0,[x12,#{}]\n".format(ID_OFF+16)   # trailing '\n'
body += "mov x0,x12\n"

code = enc("add x11,sp,#0\n")        # placeholder, overwritten below with GLOBAL's adrp/add
code=[]
code.append(('GLOBAL_ADRP',)); code.append(('GLOBAL_ADD',))    # x11 -> &sAllKextLoadIdentifiers
code.append(('RENDER_ADRP',)); code.append(('RENDER_ADD',))    # x12 -> RENDER
code.append(('LIT_ADRP',)); code.append(('LIT_ADD',))          # x13 -> "SALLKLID" literal
code += enc("ldr x14,[x13]\nstr x14,[x12]\n")
code += enc(body)
code.append(('BL',))
code.append(0x14000000)   # b .  (always hangs -- this runs exactly once)

for i,x in enumerate(code):
    va=CAVE+4*i
    if x==('GLOBAL_ADRP',): x=adrp(11,va,GLOBAL)
    elif x==('GLOBAL_ADD',): x=0x91000000|((GLOBAL&0xfff)<<10)|(11<<5)|11
    elif x==('RENDER_ADRP',): x=adrp(12,va,RENDER)
    elif x==('RENDER_ADD',): x=0x91000000|((RENDER&0xfff)<<10)|(12<<5)|12
    elif x==('LIT_ADRP',): pass   # resolved in the second pass below (needs LIT_IDX first)
    elif x==('LIT_ADD',): pass
    elif x==('BL',): x=bl(va,IOLOG)
    if x!=('LIT_ADRP',) and x!=('LIT_ADD',): put(va,x)
LIT_IDX=len(code)
code += list(struct.unpack('<2I', b'LKCSTART'))
LIT_ADDR=CAVE+4*LIT_IDX
for i,x in enumerate(code[:LIT_IDX]):
    va=CAVE+4*i
    if x==('LIT_ADRP',): put(va,adrp(13,va,LIT_ADDR))
    elif x==('LIT_ADD',): put(va,0x91000000|((LIT_ADDR&0xfff)<<10)|(13<<5)|13)
for i in range(LIT_IDX,len(code)): put(CAVE+4*i,code[i])
assert 4*len(code) < 0x280, ('cave overflow', 4*len(code))
put(SITE,bl(SITE,CAVE))
open(k,'wb').write(d)
print('initprobe',hex(SITE),'-> cave',hex(CAVE),len(code),'insns, global',hex(GLOBAL))
