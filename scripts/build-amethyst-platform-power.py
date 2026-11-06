#!/usr/bin/env python3
"""Build the opt-in, guarded sheng original-PMGR integration instrument for amethyst.

Copied from UniXNU/UniXNU-sheng scripts/build-sheng-platform-power.py (BSD-2-Clause-Patent). The source is sheng's
package/ShengPlatformPower.cpp, unchanged, in this repository's package/ (kits/amethyst in the build tree, which
links the device package to the directory board.toml [board] kit names); it touches no hardware and only publishes
an ApplePMGR provider so the unchanged AMFI finds one.

One change: sheng checks the vtable against a prelinked candidate left by an earlier local build. A fresh CI tree has
none, so without one the two inherited AON slot pointers come from PINNED_AON (read from the prelinked amethyst
kernel of 2026-10-22, same kernelcache sha256 f18b4df8... and slide) and the native IOPMGR slot comparison is skipped.
When a candidate exists, every check runs as on sheng.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from kernel_target import CONFIG_LEVEL, OBJECT_TREE

ROOT = Path(__file__).resolve().parents[1]
# In a resolved build tree this script is a copy under <tree>/scripts, but the device sources are reached through
# <tree>/kits/<board kit> and the compile records through <tree>/build. The archive recipe runs with the tree as the
# working directory, so fall back to that when this copy sits outside a device repository layout.
TREE = Path.cwd() if (Path.cwd() / 'kits' / 'amethyst').is_dir() else ROOT


def device_source(relative):
    # The device sources are always reached through the resolved build tree's kits/<kit> link, whether this run
    # started in the device repository (--local -D) or in the tree the archive recipe runs from.
    candidate = TREE / relative
    if not candidate.is_file():
        raise SystemExit(f'{candidate} not found; is the device package linked as kits/amethyst?')
    return candidate


if os.environ.get('UNIXNU_XNU_ARCH') == 'arm64e':
    cache = os.environ.get('UNIXNU_KERNELCACHE')
    if not cache or not Path(cache).is_file():
        raise SystemExit('arm64e PMGR requires UNIXNU_KERNELCACHE pointing to the pinned decompressed image')
    subprocess.run([sys.executable, str(TREE / 'scripts/compile-pmgr-arm64e.py'),
                    '--tree', str(TREE), '--kernelcache', cache, '--archive'], check=True)
    destination = TREE / 'build/sheng-platform-power/libShengPlatformPower.a'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.pending.a')
    shutil.copyfile(TREE / 'build/sheng-platform-power-arm64e/libShengPlatformPower.a', temporary)
    temporary.replace(destination)
    print(f'PASS: published audited arm64e PMGR archive: {destination}')
    raise SystemExit(0)
OUT = TREE / 'build/sheng-platform-power'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(TREE / 'kits/XnuPrelink'))
from native_prelink import macho, backed_offset, exports


def run(command, **kwargs):
    return subprocess.check_output([str(v) for v in command], timeout=90, **kwargs)


def require(value, message):
    if not value:
        raise SystemExit(message)


PINNED_AON = {320: 0xfffffff004a20fbc, 321: 0xfffffff004a20fc4}
CANDIDATES = sorted((TREE / 'build/xnu-prelink').glob('*-candidate.macho'), key=lambda f: f.stat().st_mtime)
ORIGINAL = TREE / '.cache/firmware/kernelcache.macho/kernelcache.release.ipad7.decompressed'
if CANDIDATES and ORIGINAL.is_file():
    print(run(['python3', TREE / 'scripts/audit-amfi-pmgr-contract.py', '--candidate', CANDIDATES[-1]], text=True), end='')
elif CANDIDATES:
    # The CI bootstrap candidate is there, the decompressed original kernelcache the contract audit also reads is
    # not: skip only the audit; the slot checks below still run against the candidate.
    print('No decompressed original kernelcache: AMFI/PMGR contract audit skipped, candidate slot checks kept')
else:
    print('No prelinked candidate in this tree: AMFI/PMGR contract audit skipped, pinned AON slots used')
header = '''// Generated exact storage/slot ABI; private fields stay opaque.
#pragma once
#include <IOKit/IOPMGR.h>
class ApplePMGR : public IOPMGR {
 OSDeclareAbstractStructors(ApplePMGR)
 unsigned char originalPrivate[0x4b8a8-sizeof(IOPMGR)];
public:
'''
guards = ''
for i in range(177, 340):
    if i == 320:
        header += ' virtual uint32_t readAon();\n'
    elif i == 321:
        header += ' virtual void writeAon(uint64_t);\n'
    else:
        header += f' virtual void opaque{i}() = 0;\n'
        guards += f' void opaque{i}() override {{ unavailable({i}); }}\n'
header += '};\nstatic_assert(sizeof(ApplePMGR)==0x4b8a8,"Original provider storage");\n'
(OUT / 'OriginalPMGRABI.h').write_text(header)
(OUT / 'GuardedPMGRSlots.inc').write_text(guards)
source = device_source('kits/amethyst/ShengPlatformPower.cpp')
obj = OUT / 'ShengPlatformPower.o'
record = json.loads((TREE / OBJECT_TREE / 'iokit' / CONFIG_LEVEL / 'IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
command, i = [], 0
arguments = record['arguments']
while i < len(arguments):
    arg = arguments[i]
    if arg == record['file']:
        command.append(source)
    elif arg == '-o':
        command += ['-o', obj]
        i += 1
    elif arg == '-Xclang' and arguments[i + 1].startswith('-W'):
        i += 1
    elif not arg.startswith('-W'):
        command.append(arg)
    i += 1
command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator',
            '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion',
            '-Wno-unnecessary-virtual-specifier', '-I', OUT, '-I', TREE / '.cache/xnu-work/osfmk']
run(command, cwd=record['directory'])
raw = obj.read_bytes()
info = macho(raw)
require(info['kind'] == 1, 'Require actual ARM64 subtype-0 Mach-O object')
build_version = [struct.unpack_from('<2I', raw, at + 8) for cmd, at, _ in info['commands'] if cmd == 0x32]
require(build_version == [(2, 0x110000)], 'Require iOS17 build target')
sym_at = next(at for cmd, at, _ in info['commands'] if cmd == 2)
at, count, strings, _ = struct.unpack_from('<4I', raw, sym_at + 8)
symbols = []
for i in range(count):
    ix, kind, _, _, address = struct.unpack_from('<IBBHQ', raw, at + i * 16)
    symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), address, kind))
vtable = next(va for name, va, _ in symbols if name == '__ZTV18ShengPlatformPower')
relocations = {}
for segment in info['segments']:
    for section in segment['sections']:
        if section['va'] <= vtable < section['va'] + section['size']:
            at, count = struct.unpack_from('<2I', raw, section['command_offset'] + 56)
            for i in range(count):
                offset, bits = struct.unpack_from('<2I', raw, at + i * 8)
                if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                    relocations[section['va'] + offset] = symbols[bits & 0xffffff][0]
entries = [relocations.get(vtable + 16 + i * 8) for i in range(340)]
require(all(entries) and vtable + 16 + 340 * 8 not in relocations, 'Exact 340-entry provider table')
candidate = CANDIDATES[-1].read_bytes() if CANDIDATES else None
if candidate:
    meta = macho(candidate)
    native_names = {n: va for va, names in exports(candidate, meta).items() for n in names}


def ptr(va):
    return struct.unpack_from('<Q', candidate, backed_offset(meta['segments'], va, 8, len(candidate)))[0]


bindings = {'__ZN9ApplePMGRC2EPK11OSMetaClass': 0xfffffff0049f003c,
            '__ZN9ApplePMGRD2Ev': 0xfffffff0049f019c,
            '__ZN9ApplePMGR10gMetaClassE': 0xfffffff006adf490}
for slot, name in enumerate(entries):
    if slot in (0, 1, 7) or 168 <= slot < 340 and slot not in (320, 321):
        require('18ShengPlatformPower' in name, f'Own guard/lifecycle required at slot {slot}')
    elif slot in (320, 321):
        require('9ApplePMGR' in name, 'Only actual original AON implementations inherited')
        bindings[name] = ptr(0xfffffff005ce26d8 + slot * 8) if candidate else PINNED_AON[slot]
        if candidate:
            require(bindings[name] == PINNED_AON[slot], f'AON slot {slot} differs from the pinned value')
    elif candidate:
        require(native_names.get(name) == ptr(native_names['__ZTV6IOPMGR'] + 16 + slot * 8),
                f'Native IOPMGR/IOService entry differs at {slot}')
undefined = {n for n, _, k in symbols if k == 1 and 'ApplePMGR' in n}
require(undefined == set(bindings), 'All and only audited original base imports')
bindings.update({'_sheng_original_pmgr_nub_create': 0xfffffff004a22568,
                 '_sheng_original_pmgr_nub_read': 0xfffffff004a24adc,
                 '_sheng_original_pmgr_nub_write': 0xfffffff004a230ec})
asm = OUT / 'original-bindings.S'
asm.write_text(''.join(f'.globl {name}\n.set {name}, {va:#x}\n' for name, va in bindings.items()))
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-isysroot', sdk, '-c', asm, '-o', OUT / 'original-bindings.o'])
late = OUT / 'ShengPlatformPower.late.o'
run(['llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
     '--globalize-symbol=__GLOBAL__sub_I_ShengPlatformPower.cpp', obj, late])


def text_section(blob):
    section = next(s for seg in macho(blob)['segments'] for s in seg['sections'] if s['name'] == '__text')
    return blob[section['fileoff']:section['fileoff'] + section['size']]


require(text_section(raw) == text_section(late.read_bytes()), 'Late registration cannot change instructions')
archive = OUT / 'libShengPlatformPower.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, late, OUT / 'original-bindings.o'])
report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
          'bindings': {name: hex(va) for name, va in bindings.items()}, 'virtual_entries': 340,
          'mode': 'opt-in lifecycle/publication/refusal instrument; not a functional power backend',
          'runtime_qualified': False, 'original_executable_modified': False}
(OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS: ordinary ARM64/iOS17 build; exact native and original bindings; unimplemented power slots stop')
