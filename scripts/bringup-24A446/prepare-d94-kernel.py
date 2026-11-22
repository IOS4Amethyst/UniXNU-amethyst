#!/usr/bin/env python3
"""Carry the D94 post-link kernel patches from a booted build onto a new CI kernel (RAM-boot images only).

The CI kernel (ios27-kernel.yml, kernel.macho) boots only with five post-link changes the old /tmp prepare-bNN.py scripts made:
  1 _arm_vm_prot_init+0x6ac -> b trampoline: the trampoline (ldr x8,[sp,#0x10]; ldr x8,[x8,#8]; ldr x1,[x25,#0x168]) recovers
    the trust cache base/size and resumes at _arm_vm_prot_init+0x1d4
  2 _panic+4 -> b renderer, 3 _panic_trap_to_debugger+4 -> b renderer+8: the panic-screen renderer (2864 bytes)
  4 the renderer in the zero page padding before _LowResetVectorBase (LRVB-0xc00), 5 the trampoline at LRVB-0xc0
The native kernel's segments land at a fixed offset each (__TEXT_EXEC at the end of the collection's __TEXT_EXEC, __DATA at the
collection's __DATA), so every site is the native symbol plus its segment's delta. The renderer's two absolute data addresses
(_boot_progress_panic_text, _vinfo) are MOVN/MOVK sequences and are re-encoded for the new layout.

Usage: prepare-d94-kernel.py --ref-ci <dir> --ref-shipped <kernel.macho> --new-ci <dir> --out <kernel.macho>
A <dir> is a decrypted unixnu-ios27-build artifact. Run with --new-ci = --ref-ci first: the output must equal --ref-shipped.
"""
import argparse
import hashlib
import struct
import subprocess
from pathlib import Path

SUB = 'unixnu-core/out/amethyst/ios-27.0.1-24A446-d94ap/ios27_data'
NATIVE = 'tree/build/xnu/ios-obj/RELEASE_ARM64_BCM2837/mach.release.bcm2837.unstripped.noctf'
NAMES = ['_arm_vm_prot_init', '_arm_vm_prot_finalize', '_panic', '_panic_trap_to_debugger', '_LowResetVectorBase',
         '_boot_progress_panic_text', '_vinfo']


def sha(b):
    return hashlib.sha256(b).hexdigest()


def segments(b):
    out = {}
    count, = struct.unpack_from('<I', b, 16)
    o = 32
    for _ in range(count):
        cmd, size = struct.unpack_from('<II', b, o)
        if cmd == 0x19:
            name = b[o + 8:o + 24].split(b'\0')[0].decode()
            va, vs, fo, fs = struct.unpack_from('<QQQQ', b, o + 24)
            out.setdefault(name, (va, vs, fo, fs))
        o += size
    return out


class Build:
    def __init__(self, root):
        base = Path(root) / SUB
        self.kernel = (base / 'kernel/kernel.macho').read_bytes()
        native_path = base / NATIVE
        native = native_path.read_bytes()
        nm = subprocess.check_output(['llvm-nm', str(native_path)]).decode().split('\n')
        self.ns = {}
        for line in nm:
            parts = line.split()
            if len(parts) == 3 and parts[2] in NAMES:
                self.ns[parts[2]] = int(parts[0], 16)
        assert set(self.ns) == set(NAMES), set(NAMES) - set(self.ns)
        self.nseg, self.kseg = segments(native), segments(self.kernel)
        # __DATA: the native segment starts the collection's __DATA.
        self.data_delta = self.kseg['__DATA'][0] - self.nseg['__DATA'][0]
        # __TEXT_EXEC: the native segment is placed against the end of the collection's, with one page after it.
        kva, kvs = self.kseg['__TEXT_EXEC'][:2]
        nva, nvs = self.nseg['__TEXT_EXEC'][:2]
        self.text_delta = kva + kvs - 0x4000 - nvs - nva
        self.native, self.native_bytes = native_path, native

    def emit(self, name):
        v = self.ns[name]
        for seg, delta in (('__TEXT_EXEC', self.text_delta), ('__DATA', self.data_delta)):
            va, vs = self.nseg[seg][:2]
            if va <= v < va + vs:
                return v + delta
        raise AssertionError(name)

    def off(self, a, n=1):
        for va, vs, fo, fs in self.kseg.values():
            if va <= a and a + n <= va + min(vs, fs):
                return fo + (a - va)
        raise AssertionError(hex(a))

    def executable(self, a, n):
        va, vs, fo, fs = self.kseg['__TEXT_EXEC']
        return va <= a and a + n <= va + vs

    def read(self, a, n):
        z = self.off(a, n)
        return self.kernel[z:z + n]

    def word(self, a):
        return struct.unpack('<I', self.read(a, 4))[0]

    def native_read(self, name, n):
        v = self.ns[name]
        for va, vs, fo, fs in self.nseg.values():
            if va <= v and v + n <= va + min(vs, fs):
                return self.native_bytes[fo + v - va:fo + v - va + n]
        raise AssertionError(name)


def signed(v, bits):
    return v - (1 << bits) if v >> (bits - 1) else v


def target(w, a):
    return a + signed(w & 0x3ffffff, 26) * 4


def branch(a, t):
    d = t - a
    assert d % 4 == 0 and -(1 << 27) <= d < 1 << 27
    return struct.pack('<I', 0x14000000 | ((d // 4) & 0x3ffffff))


def masked(code):
    """Words with B/BL and ADRP immediates cleared: the emitter re-encodes calls and page addresses, nothing else."""
    out = []
    for k in range(0, len(code), 4):
        w = struct.unpack_from('<I', code, k)[0]
        if w & 0x7c000000 == 0x14000000:
            w &= 0xfc000000
        elif w & 0x9f000000 == 0x90000000:
            w &= 0x9f00001f
        out.append(w)
    return out


def movabs(code, at):
    """The 64-bit value a MOVN/MOVZ + MOVK x16 + MOVK x32 triple builds (instructions may be interleaved: the first
    three writes to the register after `at`)."""
    w0 = struct.unpack_from('<I', code, at)[0]
    rd = w0 & 31
    assert w0 >> 23 in (0x125, 0x1a5), hex(w0)    # MOVN/MOVZ x, hw 0
    value = (~((w0 >> 5) & 0xffff)) & 0xffffffffffffffff if w0 >> 23 == 0x125 else (w0 >> 5) & 0xffff
    found, k, places = 0, at + 4, [at]
    while found < 2:
        w = struct.unpack_from('<I', code, k)[0]
        if w >> 23 == 0x1e5 and w & 31 == rd:   # MOVK x
            hw = (w >> 21) & 3
            value = (value & ~(0xffff << (16 * hw))) | (((w >> 5) & 0xffff) << (16 * hw))
            found += 1
            places.append(k)
        k += 4
    return value, places


def encode_movabs(code, places, value):
    """Re-encode a triple found by movabs for `value` (MOVN with the low half inverted, MOVK hw 1 and 2)."""
    rd = struct.unpack_from('<I', code, places[0])[0] & 31
    assert value >> 48 == 0xffff
    struct.pack_into('<I', code, places[0], 0x92800000 | ((~value & 0xffff) << 5) | rd)
    for p in places[1:]:
        w = struct.unpack_from('<I', code, p)[0]
        hw = (w >> 21) & 3
        struct.pack_into('<I', code, p, (w & ~(0xffff << 5)) | (((value >> (16 * hw)) & 0xffff) << 5))


def sites(build):
    trust, trap, panic = build.emit('_arm_vm_prot_init'), build.emit('_panic_trap_to_debugger'), build.emit('_panic')
    lrvb = build.emit('_LowResetVectorBase')
    return dict(trust=trust, trap=trap, panic=panic, renderer=lrvb - 0xc00, trampoline=lrvb - 0xc0)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ref-ci', required=True)
    p.add_argument('--ref-shipped', required=True, type=Path)
    p.add_argument('--new-ci', required=True)
    p.add_argument('--out', required=True, type=Path)
    a = p.parse_args()
    ref, new = Build(a.ref_ci), Build(a.new_ci)
    shipped = a.ref_shipped.read_bytes()
    assert len(shipped) == len(ref.kernel)
    rs, ns = sites(ref), sites(new)

    # The reference: exactly the five changes, at the sites this script derives.
    want = {ref.off(rs['trust'] + 0x6ac): 4, ref.off(rs['trap'] + 4): 4, ref.off(rs['panic'] + 4): 4,
            ref.off(rs['renderer']): 2864, ref.off(rs['trampoline']): 16}
    pos = 0
    for start in sorted(want):
        assert ref.kernel[pos:start] == shipped[pos:start], f'unexpected change before {start:#x}'
        pos = start + want[start]
    assert ref.kernel[pos:] == shipped[pos:], 'unexpected change after the last site'
    sh = lambda addr, n: shipped[ref.off(addr, n):ref.off(addr, n) + n]
    shw = lambda addr: struct.unpack('<I', sh(addr, 4))[0]
    assert target(shw(rs['trust'] + 0x6ac), rs['trust'] + 0x6ac) == rs['trampoline']
    assert target(shw(rs['trap'] + 4), rs['trap'] + 4) == rs['renderer'] + 8
    assert target(shw(rs['panic'] + 4), rs['panic'] + 4) == rs['renderer']
    tramp = sh(rs['trampoline'], 16)
    assert tramp[:12].hex() == 'e80b40f9080540f921b740f9', tramp.hex()
    assert target(struct.unpack_from('<I', tramp, 12)[0], rs['trampoline'] + 12) == rs['trust'] + 0x1d4
    renderer = bytearray(sh(rs['renderer'], 2864))
    data = {}
    for at, name in ((0x368, '_boot_progress_panic_text'), (0x3e0, '_vinfo')):
        value, places = movabs(renderer, at)
        assert value == ref.emit(name), (name, hex(value), hex(ref.emit(name)))
        data[name] = places
    # Renderer code (before its font/data at +0x800): direct branches stay inside it, so it moves as a block.
    for k in range(0, 0x800, 4):
        w = struct.unpack_from('<I', renderer, k)[0]
        if w & 0x7c000000 == 0x14000000:
            assert 0 <= target(w, k) < 0x800, hex(k)

    # The new kernel: the same code at every site (calls re-encoded only), zero caves, executable.
    trust_size = new.ns['_arm_vm_prot_finalize'] - new.ns['_arm_vm_prot_init']
    assert trust_size == ref.ns['_arm_vm_prot_finalize'] - ref.ns['_arm_vm_prot_init'] == 0x6b8
    for name, size in (('_arm_vm_prot_init', trust_size), ('_panic', 0x80), ('_panic_trap_to_debugger', 0x80)):
        old_code, new_code = ref.read(ref.emit(name), size), new.read(new.emit(name), size)
        assert masked(old_code) == masked(new_code), name
        assert masked(new_code) == masked(new.native_read(name, size)), name + ': not the native code at the derived address'
    assert new.word(ns['trap'] + 4) == 0xd106c3ff and new.word(ns['panic'] + 4) == 0xd10083ff
    assert new.read(ns['trust'] + 0x1d4, 8).hex() == '40bb00f968bf00f9'
    assert new.read(ns['renderer'], 0xc00) == bytes(0xc00), 'renderer/trampoline padding is not zero'
    assert new.executable(ns['renderer'], 0xc00)
    for name in ('_boot_progress_panic_text', '_vinfo'):
        assert new.emit(name) - new.data_delta == new.ns[name]
    for name, places in data.items():
        encode_movabs(renderer, places, new.emit(name))
        assert movabs(renderer, places[0])[0] == new.emit(name)
    trampoline = tramp[:12] + branch(ns['trampoline'] + 12, ns['trust'] + 0x1d4)
    out = bytearray(new.kernel)
    for addr, blob in ((ns['trust'] + 0x6ac, branch(ns['trust'] + 0x6ac, ns['trampoline'])),
                       (ns['trap'] + 4, branch(ns['trap'] + 4, ns['renderer'] + 8)),
                       (ns['panic'] + 4, branch(ns['panic'] + 4, ns['renderer'])),
                       (ns['renderer'], bytes(renderer)), (ns['trampoline'], trampoline)):
        z = new.off(addr, len(blob))
        out[z:z + len(blob)] = blob
    a.out.write_bytes(out)
    print('text delta %#x data delta %#x renderer %#x trampoline %#x' % (new.text_delta, new.data_delta, ns['renderer'],
                                                                         ns['trampoline']))
    print('PASS', sha(new.kernel)[:16], '->', sha(out))


if __name__ == '__main__':
    main()
