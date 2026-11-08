"""Bring-up probe: replaces the exact faulting instruction in OSMetaClassBase::safeMetaCast
(ldr x16,[x0], which crashed with x0 == 0xdc008005 -- not null, not a valid kernel pointer; real
kernel pointers are always canonical high addresses, 0xfffffff0xxxxxxxx). Condition: only acts if
the pointer's upper 32 bits are zero (cannot be a real kernel pointer); otherwise replays the
original instruction untouched, so the many legitimate safeMetaCast calls elsewhere in the kernel
(IOKit matching etc.) are unaffected.

REVISION HISTORY, why this version renders hex text itself instead of using IOLog conversions:
  - %llx conversions of pointer-shaped values printed the literal string "<ptr>" (confirmed by
    direct photo -- see ceprobe.py/HANDOFF). Masking to 32 bits first did not stop it.
  - A four-conversion attempt here came back completely scrambled: three '<ptr>', and the one
    chunk that printed a real number showed the *object pointer's* value in the *identifier's*
    argument slot -- i.e. redaction here doesn't just blank a value, argument correspondence
    itself becomes unreliable once any argument triggers it.
  - Every purely-literal format string used throughout this bring-up (zero %-conversions, e.g.
    ceprobe.py's "CEFAIL A walker-null") has printed correctly on every boot, no exceptions.
  So: render the kext identifier as ASCII hex digits at runtime into a buffer, and call IOLog with
  that buffer as the sole, conversion-free argument. The conversion loop is fully unrolled (no
  backward branches/labels): llvm-mc's single-shot -show-encoding mode leaves local-label branch
  targets as unresolved relocations rather than concrete bytes, so every branch here uses a
  hand-computed literal immediate displacement instead, verified against llvm-mc individually.

On the bad-pointer path: hex-renders the stashed 20-byte kext identifier (written by
kextnameprobe.py) into a template buffer in __DATA (writable at runtime -- the panic cave is
__TEXT_EXEC, r-x only; writing there faulted on the first attempt of this probe, see git history),
calls IOLog with zero conversions, then hangs (b .).
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
STASH=0xfffffff00b0d7fc0    # last 64 (zero-fill) bytes of __DATA: raw 20-byte identifier (kextnameprobe.py)
RENDER=0xfffffff00b0d6fc0   # a second, separate zero-fill 128-byte region: hex-text render buffer
SITE=0xfffffff00a8d4e2c; CAVE=0xfffffff00ab12d00
assert W(SITE)==0xf9400010, ('site not ldr x16,[x0]',hex(W(SITE)))
assert not any(d[off(CAVE):off(CAVE)+0x600]), 'cave not empty'
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
    # w8 = the source byte (set up by load_text); emits 2 ASCII hex chars to [x6], advancing x6.
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

ID_OFF = 8   # "KEXTNAME" (no trailing space) is 8 bytes; hex digits start right after

# The RENDER buffer in __DATA must stay entirely zero at build time (like STASH) -- a prior
# revision pre-filled its literal text at build time and the boot hung before even FIPSPOST,
# consistent with an early boot-time check over __DATA not tolerating pre-set content there.
# So "KEXTNAME" and the trailing newline are written by the cave itself at runtime too, copied
# from a literal embedded in the cave's own (read-only, build-time-baked, never written-to)
# __TEXT_EXEC bytes -- the same thing every format string in this whole bring-up already does.

body = "mov x5,x11\nadd x6,x12,#{}\n".format(ID_OFF)
for _ in range(20):
    body += hex_byte_block("ldrb w8,[x5],#1\n")
body += "mov x0,#0x0a\nstrb w0,[x12,#{}]\n".format(ID_OFF+40)   # trailing '\n'
body += "mov x0,x12\n"

code = enc("lsr x9,x0,#32\n")
code.append(('CBNZ_REPLAY',))
code.append(('STASH_ADRP',)); code.append(('STASH_ADD',))      # -> x11
code.append(('RENDER_ADRP',)); code.append(('RENDER_ADD',))    # -> x12
code.append(('LIT_ADRP',)); code.append(('LIT_ADD',))          # -> x13, points at "KEXTNAME" literal
code += enc("ldr x14,[x13]\nstr x14,[x12]\n")                 # copy the 8-byte literal into RENDER
code += enc(body)
code.append(('BL',))
code.append(0x14000000)   # b .  (hang here on the bad-pointer path)
REPLAY_IDX=len(code)
code.append(0xf9400010)   # replay: ldr x16,[x0]
code.append(bl(0,0))      # placeholder -> plain b SITE+4
LIT_IDX=len(code)
code += list(struct.unpack('<2I', b'KEXTNAME'))   # the literal itself, as two raw words appended to the cave

LIT_ADDR=CAVE+4*LIT_IDX
for i,x in enumerate(code):
    va=CAVE+4*i
    if x==('CBNZ_REPLAY',):
        target=CAVE+4*REPLAY_IDX
        x=0xb5000000|(((target-va)>>2&0x7ffff)<<5)|9   # cbnz x9,REPLAY (64-bit form; verified against llvm-mc)
    elif x==('STASH_ADRP',): x=adrp(11,va,STASH)
    elif x==('STASH_ADD',): x=0x91000000|((STASH&0xfff)<<10)|(11<<5)|11
    elif x==('RENDER_ADRP',): x=adrp(12,va,RENDER)
    elif x==('RENDER_ADD',): x=0x91000000|((RENDER&0xfff)<<10)|(12<<5)|12
    elif x==('LIT_ADRP',): x=adrp(13,va,LIT_ADDR)
    elif x==('LIT_ADD',): x=0x91000000|((LIT_ADDR&0xfff)<<10)|(13<<5)|13
    elif x==('BL',): x=bl(va,IOLOG)
    put(va,x)
tailva=CAVE+4*(LIT_IDX-1)
put(tailva,(bl(tailva,SITE+4) & ~0xfc000000) | 0x14000000)
assert 4*len(code) < 0x600, ('cave overflow', 4*len(code))
put(SITE,bl(SITE,CAVE))
open(k,'wb').write(d)
print('badcastfreeze',hex(SITE),'-> cave',hex(CAVE),len(code),'insns ('+hex(4*len(code))+' bytes), render buf',hex(RENDER))
