#!/usr/bin/env python3
"""Write the reviewed IOHIDDevice ABI fixture for the iOS 27 (24A446) iPhone17,2 kernelcache: scripts/hid-device-24A446-d94ap.json.

UniXNU-sheng's gen-hid27-abi.py records the iPad fileset. Here the class is found by content, not by address: the only vtable
whose 207 authenticated entries carry exactly the iPad IOHIDDevice diversities (diversities come from the method names, so they
are the same across builds), whose slot 180 is the pure-virtual stub (the abstract slot sheng's builder checks) and whose
208th word is not an authenticated pointer. Its getMetaClass (slot 7) gives gMetaClass; the constructor and destructor sit
directly before it as on the iPad (C2: IOService C2 then the vptr ZTV+16; D2: tail call); the metaclass initializer passes
"IOHIDDevice" and size 0xd0. Each identity is re-checked against the bytes below.
"""
import hashlib, json, struct, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else str(HERE.parents[1] / 'UniXNU/kits/XnuPrelink'))
from kc_image import Image
KC = Path(sys.argv[2] if len(sys.argv) > 2 else HERE.parents[1] / '.scratch/amethyst-d94ap-stage/core/out/ipsw/24A446/kernelcache.decompressed')
raw = KC.read_bytes()
assert hashlib.sha256(raw).hexdigest() == 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95'
BASE = 0xfffffff007004000
ipad = json.loads((HERE / 'hid-device-24A446.ipad.json').read_text())
display = json.loads((HERE / 'legacy-display-24A446.json').read_text())
idiv = [int(d, 16) for _, d in ipad['slots']]
auth = {a: v for a, v in Image(raw).fixups().items() if v[1] == 1}
pure = int(display['slots'][214][0], 16)
found = [a for a in sorted(auth) if all((a + 8 * i) in auth and auth[a + 8 * i][3] == idiv[i] for i in range(207))
         and auth[a + 8 * 180][0] == pure and not auth.get(a + 8 * 207)]
assert len(found) == 1, [hex(a) for a in found]
VT = found[0]
w = lambda a: struct.unpack_from('<I', raw, a - BASE)[0]
sx = lambda v, b: v - (1 << b) if v >> (b - 1) else v
def adrp(a):
    x = w(a); assert x & 0x9f000000 == 0x90000000
    return (a & ~0xfff) + sx(((x >> 29) & 3) | (((x >> 5) & 0x7ffff) << 2), 21) * 4096
def addimm(a):
    x = w(a); assert x & 0xff800000 == 0x91000000
    return (x >> 10) & 0xfff
meta = auth[VT + 8 * 7][0]
assert w(meta) == 0xd503245f and w(meta + 12) == 0xd65f03c0  # bti c; adrp; add; ret
gmeta = adrp(meta + 4) + addimm(meta + 8)
c2, d2 = meta - 0x40, meta - 0x8
assert w(c2) == 0xd503245f and w(c2 + 4) == 0xd503237f and w(c2 + 16) >> 26 == 0x25        # bti c; pacibsp; ...; bl IOService C2
assert adrp(c2 + 20) + addimm(c2 + 24) + addimm(c2 + 28) == VT                               # vptr = ZTV + 16
assert w(d2) == 0xd503245f and w(d2 + 4) >> 26 == 0x05                                         # bti c; b IOService D2
name = raw.find(b'\0IOHIDDevice\0') + 1 + BASE
init = [a for a in range(0xfffffff00a0bdc00, 0xfffffff00a0bdd00, 4) if w(a) & 0x9f000000 == 0x90000000 and w(a) & 0x1f == 1
        and adrp(a) + addimm(a + 4) == name]
assert len(init) == 1 and 0x52801a03 in [w(init[0] + 4 * k) for k in range(2, 8)]                # x1 = "IOHIDDevice", w3 = 0xd0
slots = [[hex(auth[VT + 8 * i][0]), hex(auth[VT + 8 * i][3])] for i in range(207)]
abi = {'kernelcache_sha256': hashlib.sha256(raw).hexdigest(), 'collection_slide': '0x0', 'vtable': hex(VT), 'instance_size': '0xd0',
       'slots': slots,
       'bindings': {'__ZN11IOHIDDeviceC2EPK11OSMetaClass': hex(c2), '__ZN11IOHIDDeviceD2Ev': hex(d2),
                    '__ZN11IOHIDDevice10gMetaClassE': hex(gmeta), '__ZTV11IOHIDDevice': hex(VT - 16)}}
out = HERE / 'hid-device-24A446-d94ap.json'
out.write_text(json.dumps(abi, indent=1) + '\n')
print('wrote', out, 'vtable', hex(VT), 'gMetaClass', hex(gmeta), 'C2', hex(c2), 'D2', hex(d2))
