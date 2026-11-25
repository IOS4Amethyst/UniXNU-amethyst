#!/usr/bin/env python3
"""Audit PMGR object PAC metadata against the pinned iPhone15,4 24A446 image."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

PIN = 'b508b34e17ded04ea54c51399bc486592fb75b8d4629b9bf0c30fbc36d0071b2'
D94 = json.loads(Path(__file__).with_name('pmgr-d94-pins.json').read_text())


def qualified_image(data):
    digest = hashlib.sha256(data).hexdigest()
    require(digest in (PIN, D94['kernelcache_sha256']), 'Kernelcache SHA-256 mismatch')
    return digest


def lifecycle_bindings(image, undefined, slide):
    if qualified_image(image.data) == D94['kernelcache_sha256']:
        # Fileset emitter uses native.base - cache.base, not --slide. Both D94
        # and this profile's native link base are 0xfffffff007004000.
        require(image.base == 0xfffffff007004000 and slide == 0, 'Unexpected D94 collection slide')
        bindings = {name: int(address, 16) for name, address in D94['bindings'].items()}
        imports = {name for name in undefined if 'ApplePMGR' in name or name.startswith('_sheng_original_pmgr_')}
        require(imports == set(bindings), f'Unexpected PMGR imports: {sorted(imports ^ set(bindings))}')
        for anchor in D94['anchors'].values():
            require(hashlib.sha256(image.read(int(anchor['address'], 16), anchor['size'])).hexdigest() == anchor['sha256'],
                    f'D94 PMGR fingerprint mismatch at {anchor["address"]}')
        return bindings
    require(slide == -0x1000000, 'Only the qualified iPhone profile slide is supported')
    bindings = {
        '__ZN9ApplePMGRC2EPK11OSMetaClass': 0xfffffff008feb7bc,
        '__ZN9ApplePMGRD2Ev': 0xfffffff008feb98c,
        '__ZN9ApplePMGR10gMetaClassE': 0xfffffff00b03a960,
        '_sheng_original_pmgr_nub_create': 0xfffffff00903da68,
        '_sheng_original_pmgr_nub_read': 0xfffffff009041284,
        '_sheng_original_pmgr_nub_write': 0xfffffff00903ecd4,
    }
    imports = {name for name in undefined if 'ApplePMGR' in name or name.startswith('_sheng_original_pmgr_')}
    require(imports == set(bindings), f'Unexpected PMGR imports: {sorted(imports ^ set(bindings))}')
    for start, end, digest in (
        (0xfffffff008feb7bc, 0xfffffff008feb98c, '97b185ada8331c2d25e00e15fd0942738f96c3a8201725c1ec9642db17f2d55d'),
        (0xfffffff00903da68, 0xfffffff00903db1c, 'ae99db3dd5b6738184e0ca2c9b92d0a54765f38176f89202397f337ce05ed7af'),
        (0xfffffff00903db1c, 0xfffffff00903e428, 'd0094f0e8d802d8e93c395d72f8c127177747eb20ace0b4dcb256ad67d112951'),
    ):
        require(hashlib.sha256(image.read(start, end - start)).hexdigest() == digest,
                f'PMGR lifecycle fingerprint mismatch at {start:#x}')
    require(image.read(0xfffffff008feb98c, 8) == struct.pack('<2I', 0xd503245f, 0x14014227),
            'Nonvirtual destructor must tail-call ApplePMGRBase destructor')
    return {name: address + slide for name, address in bindings.items()}


def abi_headers():
    """Opaque declarations preserve the original inheritance and vtable order.

    Opaque names deliberately do not claim Apple's method signatures. Their PAC
    discriminators must be replaced when installing the guarded provider table.
    """
    header = ['// Generated for iPhone15,4 24A446; not a functional power backend.',
              '#pragma once', '#include <IOKit/IOPMGR.h>',
              'class ApplePMGRBase : public IOPMGR {',
              ' OSDeclareAbstractStructors(ApplePMGRBase)', 'public:']
    guards = []
    for slot in range(177, 439):
        if slot == 301:
            header += ['};', 'static_assert(sizeof(ApplePMGRBase) == 0x88, "Original PMGR base storage");',
                       'class ApplePMGR : public ApplePMGRBase {',
                       ' OSDeclareAbstractStructors(ApplePMGR)',
                       ' unsigned char originalPrivate[0x738d8 - sizeof(ApplePMGRBase)];', 'public:']
        if slot == 266:
            header.append(' virtual bool originalProviderFlag() = 0;')
            guards += [' bool originalProviderFlag() override {',
                       '   return (reinterpret_cast<const unsigned char *>(this)[0x10c] & 1) != 0;', ' }']
        elif slot == 280:
            header.append(' virtual uint32_t readAon() = 0;')
            guards.append(' uint32_t readAon() override { return 0xffffffffU; }')
        elif slot == 281:
            header.append(' virtual void writeAon(uint64_t) = 0;')
            guards.append(' void writeAon(uint64_t) override {}')
        else:
            header.append(f' virtual void opaque{slot}() = 0;')
            guards.append(f' void opaque{slot}() override {{ unavailable({slot}); }}')
    header += ['};', 'static_assert(sizeof(ApplePMGR) == 0x738d8, "Original PMGR storage");']
    return '\n'.join(header) + '\n', '\n'.join(guards) + '\n'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pac_header(provider, original):
    require(len(provider) == len(original) == 439, 'PAC installer requires the rebuilt 439-slot provider')
    require(all(value[1] == 1 and value[2] == 0 and value[4] == 1 for value in original),
            'Expected address-diverse IA original provider slots')
    require(all(metadata[0] == 0 and metadata[2] == 1 for _, metadata in provider),
            'Expected address-diverse IA compiled provider slots')
    native = ','.join(str(metadata[1]) for _, metadata in provider)
    target = ','.join(str(value[3]) for value in original)
    return '''// Generated from audited object relocations and the pinned original image.
#pragma once
#include <ptrauth.h>
#if !__has_feature(ptrauth_calls)
#error PMGR PAC installation requires arm64e
#endif
static void installOriginalPMGRTable(ShengPlatformPower *provider)
{
  // ponytail: one provider for the lifetime of this bring-up kernel; no teardown/reuse.
  static void *copy[439];
  static bool installed;
  static const uint16_t nativeDiversity[439] = {''' + native + '''};
  static const uint16_t originalDiversity[439] = {''' + target + '''};
  if (!provider || installed) panic("ShengPlatformPower: duplicate or null PAC installation");
  void **table = reinterpret_cast<void **>(ptrauth_auth_data(
      *reinterpret_cast<void **>(provider), ptrauth_key_process_independent_data,
      ptrauth_blend_discriminator(provider, 0xcda1)));
  for (unsigned i = 0; i < 439; ++i) {
    void *entry = table[i];
    void *function = ptrauth_strip(entry, ptrauth_key_function_pointer);
    if (!entry || ptrauth_sign_unauthenticated(function, ptrauth_key_function_pointer,
        ptrauth_blend_discriminator(&table[i], nativeDiversity[i])) != entry)
      panic("ShengPlatformPower: compiled PAC slot %u differs", i);
    copy[i] = ptrauth_sign_unauthenticated(function, ptrauth_key_function_pointer,
        ptrauth_blend_discriminator(&copy[i], originalDiversity[i]));
  }
  installed = true;
  __asm__ volatile("dmb ish" ::: "memory");
  *reinterpret_cast<void **>(provider) = ptrauth_sign_unauthenticated(
      &copy[0], ptrauth_key_process_independent_data,
      ptrauth_blend_discriminator(provider, 0xcda1));
}
'''


def object_table(path, name):
    data = path.read_bytes()
    magic, cpu, subtype, kind, count = struct.unpack_from('<5I', data)
    require((magic, cpu, subtype & 0xff, kind) == (0xfeedfacf, 0x100000c, 2, 1),
            f'{path}: expected arm64e MH_OBJECT')
    at, sections, symbols, deployment = 32, [], [], []
    for _ in range(count):
        cmd, size = struct.unpack_from('<2I', data, at)
        require(size >= 8 and at + size <= len(data), 'Invalid load-command size')
        if cmd == 0x19:
            nsects, = struct.unpack_from('<I', data, at + 64)
            require(size >= 72 + 80 * nsects, 'Truncated segment sections')
            for i in range(nsects):
                sections.append(struct.unpack_from('<QQ4I', data, at + 72 + i * 80 + 32))
        elif cmd == 0x32:
            deployment.append(struct.unpack_from('<2I', data, at + 8))
        elif cmd == 2:
            symoff, nsyms, strings, stringsize = struct.unpack_from('<4I', data, at + 8)
            require(symoff + nsyms * 16 <= len(data) and strings + stringsize <= len(data),
                    'Truncated symbol table')
            for i in range(nsyms):
                ix, symbol_kind, _, _, value = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
                require(ix < stringsize, 'Invalid symbol string offset')
                end = data.index(b'\0', strings + ix, strings + stringsize)
                symbols.append((data[strings + ix:end].decode(), value, symbol_kind))
        at += size
    require(deployment == [(2, 0x1b0000)], f'Expected iOS 27.0 deployment, got {deployment}')
    addresses = [v + 16 for n, v, kind in symbols if n == name and kind & 0x0e == 0x0e]
    require(len(addresses) == 1, f'Expected one defined {name}')
    address = addresses[0]
    section, = [s for s in sections if s[0] <= address < s[0] + s[1]]
    va, length, offset, _, reloc, nreloc = section
    require(offset + length <= len(data) and reloc + nreloc * 8 <= len(data), 'Truncated vtable section')
    entries = {}
    for i in range(nreloc):
        site, bits = struct.unpack_from('<2I', data, reloc + 8 * i)
        if va + site < address:
            continue
        require(site + 8 <= length, 'Relocation outside section')
        if bits >> 28 != 11:
            continue
        require(bits >> 27 & 1 and (bits >> 25 & 3) == 3 and not bits >> 24 & 1,
                'Expected external, absolute, 8-byte authenticated relocation')
        require((va + site - address) % 8 == 0, 'Unaligned vtable relocation')
        index = bits & 0xffffff
        require(index < len(symbols), 'Invalid relocation symbol')
        raw, = struct.unpack_from('<Q', data, offset + site)
        require(raw >> 63 == 1 and raw & 0xffffffff == 0, 'Unexpected authenticated pointer addend')
        slot = (va + site - address) // 8
        require(slot not in entries, 'Duplicate vtable relocation')
        entries[slot] = (symbols[index][0], (raw >> 49 & 3, raw >> 32 & 0xffff, raw >> 48 & 1))
    result = []
    while len(result) in entries:
        result.append(entries[len(result)])
    require(bool(result), 'Missing authenticated vtable entries')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prelink', type=Path, required=True, help='XnuPrelink checkout containing kc_image.py')
    parser.add_argument('--kernelcache', type=Path, required=True)
    parser.add_argument('--iopmgr', type=Path, required=True)
    parser.add_argument('--provider', type=Path, required=True)
    parser.add_argument('--emit-abi', type=Path, help='Write declarations and guards after the audit passes')
    parser.add_argument('--emit-pac', type=Path, help='Write installer header; requires a rebuilt 439-slot provider')
    args = parser.parse_args()
    data = args.kernelcache.read_bytes()
    digest = qualified_image(data)
    sys.path.insert(0, str(args.prelink.resolve()))
    from kc_image import Image
    fixups = Image(data).fixups()
    d94 = digest == D94['kernelcache_sha256']
    iopmgr_table = int(D94['tables']['IOPMGR']['address'], 16) if d94 else 0xfffffff007c8ca40
    pmgr_table = int(D94['tables']['ApplePMGR']['address'], 16) if d94 else 0xfffffff007eff258
    if d94:
        image = Image(data)
        for table in D94['tables'].values():
            require(hashlib.sha256(image.read(int(table['address'], 16), table['size'])).hexdigest() == table['sha256'],
                    'D94 PMGR table fingerprint mismatch')
    native = object_table(args.iopmgr, '__ZTV6IOPMGR')
    provider = object_table(args.provider, '__ZTV18ShengPlatformPower')
    require(len(native) == 177, 'Expected 177 native IOPMGR slots')
    # ponytail: accepts the diagnostic old bridge too; require 439 in the new builder.
    require(len(provider) in (340, 439), 'Unexpected provider table length')
    for label, entries in (('IOPMGR', native), ('ShengPlatformPower', provider)):
        for slot, (_, metadata) in enumerate(entries[:177]):
            require(metadata == fixups[iopmgr_table + slot * 8][2:],
                    f'{label} slot {slot}: PAC metadata differs from original IOPMGR')
    original = [fixups[pmgr_table + i * 8] for i in range(439)]
    require(all(v[1] == 1 and v[2] == 0 and v[4] == 1 for v in original),
            'Expected address-diverse IA original provider slots')
    if args.emit_pac:
        args.emit_pac.write_text(pac_header(provider, original))
    if args.emit_abi:
        # ponytail: generation only; the builder must audit and re-sign the compiled 439-slot table.
        header, guards = abi_headers()
        args.emit_abi.mkdir(parents=True, exist_ok=True)
        (args.emit_abi / 'OriginalPMGRABI.h').write_text(header)
        (args.emit_abi / 'GuardedPMGRSlots.inc').write_text(guards)
    print(json.dumps({'kernelcache_sha256': digest, 'native_slots': len(native),
                      'provider_slots': len(provider), 'shared_prefix_pac_matches': True,
                      'native_provider_diversity': [v[1][1] for v in provider],
                      'original_provider_diversity': [v[3] for v in original],
                      'runtime_qualified': False}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error, KeyError) as error:
        raise SystemExit(f'PMGR ABI audit: {error}') from error
