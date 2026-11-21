#!/usr/bin/env python3
"""Build the board's real A810 archive, never the SM8550 catalog driver.

Run --audit-kernel DONOR --core CORE for the independently runnable D94 ABI audit.
Build additionally requires --manifest INPUTS --output NEW_DIRECTORY. INPUTS uses
build-amethyst-adreno.py's absolute path/SHA256 inventory, plus ShengGmuVotesSource.h,
and a contracts object (see CONTRACTS below). No downloads, default scratch inputs,
config changes, force-loading, or hardware operations. Run in the live macOS XNU
RELEASE build tree; a serialized CI compiler recipe is not a portable SDK.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

PIN = '1722d15371343d564bf622e91ac2ec5089284087'
BOARD_PIN = '9a9a7cb628209d79a81d63e6e936c3e5735d9a59'
DONOR = 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95'
CONTRACTS = ('amethyst_generated_inputs_review', 'gmu_pool_loader_reservation_review',
             'ios27_userclient_iosurface_import_review', 'release_late_probe_hook_review')
HELPER_PINS = {
    'AdrenoPM4.c': '282606b528b2595bc4274ea7490b62b323f35a5f',
    'ArmSmmu4K.c': '83969cd874503e65997ff7639f7dcf3d0a92cd17',
    'AdrenoHfi.c': '96028a543979c2443619b1f2a8dcdd6cc996ba09',
    'OriginalAcceleratorABI.h': '243d63cf43305a85c8119b66b49cb7df4b30b019',
    'ShengGmu.c': '47dc6aa0ba67a0ea581a1e3e7269361f1e2e5619',
}
# Each contract is an absolute path/SHA256 record of an independently reviewed
# report, not an inferred approval from a successful compile. Runtime remains unqualified.
ENTRY_HASHES = {
    0xfffffff00a0533e8: (84, '321d0c0473aa0df8791ab4a0dc39404936a40af1c308b23f921791ea1ca77ee2'),
    0xfffffff00a04e610: (56, '1255e74f8d050dbee418832c84ff16970650feaafaba41d803976af82b7f9171'),
    0xfffffff00a04e648: (56, '465ec745eecebeff7e987377522f727c43557e7ab45c2685ddb30fcd272c0f4d'),
    0xfffffff00a04e680: (8, '377b337464419f07e0188b1cbef7b10d48dc5643e35355726814be400f0aa220'),
    0xfffffff00a04e6f0: (16, '89716be6cd0729e80a9af76d36e70abf4e0056a7353d04bf822556c1516bd670'),
}
BINDINGS = {'__ZN15IOAcceleratorESC2EPK11OSMetaClass': 0xfffffff00a04e610,
            '__ZN15IOAcceleratorESD2Ev': 0xfffffff00a04e680,
            '__ZN15IOAcceleratorES10gMetaClassE': 0xfffffff00b952818}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit_kernel(core, path):
    require(sha(path) == DONOR, 'requires exact D94 donor SHA256 ' + DONOR)
    sys.path.insert(0, str(core / 'kits/XnuPrelink'))
    from kc_image import Image
    image = Image(path.read_bytes())
    fixups = image.fixups()
    for address, (length, digest) in ENTRY_HASHES.items():
        require(hashlib.sha256(image.read(address, length)).hexdigest() == digest,
                f'D94 reviewed instruction mismatch at {address:#x}')
    base, derived = 0xfffffff007e89630, 0xfffffff0082984a0
    slots = [fixups[derived + 8 * i] for i in range(168)]
    require([i for i, value in enumerate(slots) if value != fixups[base + 8 * i]] == [0, 1, 7],
            'IOAcceleratorES has unexpected operational/PAC overrides')
    require(all(value[1:3] == (1, 0) and value[4] == 1 for value in slots),
            'requires IA/address-diversified original vtable')
    require(image.read(derived + 168 * 8, 24) == bytes(24), 'unexpected base vtable extent')
    # These identities are proved by the hashed static initializer and constructor:
    # name IOAcceleratorES, size 0x88, parent IOService; C2 installs DA/0xcda1 vptr.
    require(image.read(0xfffffff00799448d, 16) == b'IOAcceleratorES\0', 'original class identity')
    return {'kernelcache_sha256': DONOR, 'instance_bytes': 136, 'virtual_entries': 168,
            'vtable': hex(derived), 'bindings': {n: hex(v) for n, v in BINDINGS.items()},
            'slots': slots, 'runtime_qualified': False}


def compile_command(record, source, output, flags):
    args, command, i = record['arguments'], [], 0
    require(args.count(record['file']) == 1 and args.count('-o') == 1,
            'recipe needs exactly one source and output')
    while i < len(args):
        value = args[i]
        if value in ('-MF', '-MJ', '-MT', '-MQ'):
            i += 2
            continue
        if value == '-o':
            command += ['-o', str(output)]
            i += 2
            continue
        if value not in ('-MD', '-MMD', '-MP'):
            command.append(str(source) if value == record['file'] else value)
        i += 1
    # PAC/section inspection and late-constructor removal require Mach-O, not bitcode.
    return command + flags + ['-fno-lto']


def symbols_and_sections(path):
    raw = path.read_bytes()
    require(struct.unpack_from('<II', raw) == (0xfeedfacf, 0x100000c) and
            struct.unpack_from('<I', raw, 8)[0] & 0xff == 2 and
            struct.unpack_from('<I', raw, 12)[0] == 1, f'{path}: requires arm64e MH_OBJECT')
    sections, table, offset = [], None, 32
    for _ in range(struct.unpack_from('<I', raw, 16)[0]):
        cmd, size = struct.unpack_from('<II', raw, offset)
        if cmd == 0x19:
            for i in range(struct.unpack_from('<I', raw, offset + 64)[0]):
                at = offset + 72 + 80 * i
                name = raw[at:at + 16].rstrip(b'\0').decode()
                va, length, fileoff = struct.unpack_from('<QQI', raw, at + 32)
                ro, rc = struct.unpack_from('<II', raw, at + 56)
                sections.append((name, va, length, fileoff, ro, rc))
        if cmd == 2:
            table = struct.unpack_from('<4I', raw, offset + 8)
        offset += size
    require(table is not None, f'{path}: missing symbol table')
    at, count, strings, _ = table
    symbols = []
    for i in range(count):
        ix, kind, _, desc, va = struct.unpack_from('<IBBHQ', raw, at + i * 16)
        symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), va, kind, desc))
    return raw, symbols, sections


def audit_accelerator(path, abi):
    raw, symbols, sections = symbols_and_sections(path)
    vtable = next(v for n, v, k, _ in symbols if n == '__ZTV14AGXAccelerator' and k & 0xe == 0xe)
    entries = {}
    for _, va, _, fileoff, ro, rc in sections:
        for i in range(rc):
            offset, bits = struct.unpack_from('<II', raw, ro + i * 8)
            if bits >> 27 & 1 and bits >> 25 & 3 == 3 and bits >> 28 == 11:
                entries[va + offset] = struct.unpack_from('<Q', raw, fileoff + offset)[0]
    require(all(vtable + 16 + i * 8 in entries for i in range(168)) and
            vtable + 16 + 168 * 8 not in entries, 'accelerator must have exactly 168 authenticated slots')
    for i, slot in enumerate(abi['slots']):
        word = entries[vtable + 16 + 8 * i]
        require(word >> 63 and word >> 48 & 1 and word >> 49 & 3 == 0 and
                word >> 32 & 0xffff == slot[3], f'accelerator PAC mismatch in slot {i}')
    imports = {n for n, _, k, _ in symbols if k == 1 and 'IOAcceleratorES' in n}
    require(imports == set(BINDINGS), 'unexpected accelerator imports: ' + repr(imports))


def run(command, **kwargs):
    subprocess.run([str(x) for x in command], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--audit-kernel', required=True, type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    require(args.core.is_absolute(), '--core must be absolute')
    abi = audit_kernel(args.core, args.audit_kernel)
    if args.manifest is None:
        require(args.output is None, '--output requires --manifest')
        print(json.dumps({k: v for k, v in abi.items() if k != 'slots'}, indent=2))
        return
    require(args.output is not None and args.output.is_absolute() and not args.output.exists(),
            '--output must be an absolute NEW directory (no stale archives)')
    data = json.loads(args.manifest.read_text())
    validator = load_module('amethyst_input_audit', Path(__file__).with_name('build-amethyst-adreno.py'))
    paths = validator.checked_files(data['files'])
    firmware = load_module('amethyst_firmware', Path(__file__).with_name('prepare-amethyst-gpu-firmware.py'))
    for name, (header_name, _, _) in firmware.FIRMWARE.items():
        require(paths[header_name].read_text() == firmware.header(name, paths[name].read_bytes()),
                header_name + ': regenerate with prepare-amethyst-gpu-firmware.py; blob/header mismatch')
    require(paths['kernelcache'].resolve() == args.audit_kernel.resolve(), 'manifest donor differs')
    for name in ('ShengGmuVotesSource.h', *CONTRACTS):
        entry = data.get('contracts', {}).get(name) if name in CONTRACTS else data['files'].get(name)
        require(isinstance(entry, dict), 'unresolved contract/input: ' + name)
        p = Path(entry['path'])
        require(p.is_absolute() and p.is_file() and sha(p) == entry['sha256'], 'unverified input: ' + name)
        paths[name] = p
    target = load_module('amethyst_archive_target', args.core / 'scripts/kernel_target.py')
    require(target.ARCH == 'arm64e' and target.CONFIG_LEVEL == 'RELEASE' and
            target.IOS_VERSION in ('27.0', '27.0.1'), 'requires arm64e iOS27 RELEASE recipes')
    records = {}
    for language, relative in [('c', 'bsd/RELEASE/bsd_init.o.json'),
                               ('cxx', 'iokit/RELEASE/IOPlatformExpert.cpo.json')]:
        p = paths['recipe-' + language]
        validator.check_recipe(p, target.OBJECT_TREE + '/' + relative, args.core, target.TRIPLE)
        records[language] = json.loads(p.read_text().strip().rstrip(','))
    adreno = paths['NativeAdrenoAccelerator.cpp'].parent
    require(subprocess.check_output(['git', '-C', str(adreno), 'rev-parse', 'HEAD'], text=True).strip() == PIN,
            'requires NativeAdreno ' + PIN)
    for name in validator.required_inputs():
        if name.startswith('NativeAdreno'):
            require(paths[name].resolve() == (adreno / name).resolve(), name + ': must use pinned NativeAdreno')
    # Verify the entire tracked include closure, not only the .cpp manifest inventory.
    run(['git', '-C', adreno, 'diff', '--exit-code', PIN, '--'])
    require(not subprocess.check_output(['git', '-C', str(adreno), 'ls-files', '--others', '--exclude-standard']),
            'NativeAdreno has untracked include inputs')
    for name, pin in HELPER_PINS.items():
        directory = paths[name].parent
        actual = subprocess.check_output(['git', '-C', str(directory), 'rev-parse', 'HEAD'], text=True).strip()
        require(actual == pin, f'{name}: requires helper pin {pin}, got {actual}')
        run(['git', '-C', directory, 'diff', '--exit-code', pin, '--'])
        require(not subprocess.check_output(['git', '-C', str(directory), 'ls-files', '--others', '--exclude-standard']),
                name + ': helper checkout has untracked include inputs')
    for prefix, anchor in [('ArmSmmu4K', 'ArmSmmu4K.c'), ('AdrenoHfi', 'AdrenoHfi.c'),
                            ('AdrenoFirmware', 'AdrenoHfi.c'), ('ShengGmuCommandDb', 'ShengGmu.c')]:
        for name in validator.required_inputs():
            if name.startswith(prefix) and name.endswith(('.c', '.h')) and 'Source' not in name:
                require(paths[name].resolve() == (paths[anchor].parent / name).resolve(),
                         name + ': must use the pinned helper source tree')
    require(paths['ShengGmuVotes.h'].resolve() ==
            (paths['ShengGmu.c'].parent / 'ShengGmuVotes.h').resolve(),
            'ShengGmuVotes.h: must use the pinned SM7635 helper source tree')
    board = Path(__file__).resolve().parents[1]
    for name in ('GpuPowerProbe.cpp', 'GpuPowerBoard.h', 'BoardClasses.h', 'AmethystSmc.c',
                  'ShengGmuVotes.c'):
        expected = subprocess.check_output(['git', '-C', str(board), 'show', BOARD_PIN + ':scripts/log/' + name])
        require(paths[name].read_bytes() == expected, name + ': differs from reviewed board main')
    # The pinned accelerator's relative include is ../NativeMetal, NOT ../AdrenoMetal.
    require((adreno.parent / 'NativeMetal/OriginalAcceleratorABI.h').resolve() ==
            paths['OriginalAcceleratorABI.h'].resolve(), 'stage pinned ABI header beside NativeAdreno as NativeMetal')
    require(shutil.which('xcrun') is not None, 'live Xcode kernel build environment required; no archive emitted')
    objcopy = shutil.which('llvm-objcopy')
    require(objcopy is not None, 'llvm-objcopy required for late metaclass staging')
    args.output.mkdir(parents=True)
    flags = ['-mgeneral-regs-only', '-DSHENG_GMU_POOL_BASE=UINT64_C(0xe6440000)',
             '-DSHENG_GMU_POOL_BYTES=0x2000000u']
    for directory in sorted({p.parent for n, p in paths.items() if n not in CONTRACTS and n != 'kernelcache'}):
        flags += ['-I', str(directory)]
    flags += ['-I', str(args.core / '.cache/xnu-work/osfmk')]
    sources = [paths[n] for n in validator.required_inputs() if n.endswith(('.c', '.cpp'))]
    sources.append(board / 'package/AmethystAdrenoDiagnostics.c')
    objects = []
    for source in sources:
        output = args.output / (source.stem + '.o')
        record = records['c' if source.suffix == '.c' else 'cxx']
        run(compile_command(record, source, output, flags), cwd=record['directory'])
        target.check_object(output.read_bytes(), str(output))
        raw, _, sections = symbols_and_sections(output)
        if source.name == 'NativeAdrenoAccelerator.cpp':
            audit_accelerator(output, abi)
            late = output.with_name(source.stem + '.late.o')
            run([objcopy, '--remove-section=__DATA,__mod_init_func',
                 '--globalize-symbol=__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp', output, late])
            late_raw, late_symbols, late_sections = symbols_and_sections(late)
            require(not any(n == '__mod_init_func' for n, *_ in late_sections), 'early accelerator constructor survived')
            require(any(n == '__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp' and k & 1
                        for n, _, k, _ in late_symbols), 'late constructor not exported')
            def text(data, secs):
                return b''.join(data[o:o + size] for n, _, size, o, _, _ in secs if n == '__text')
            require(text(raw, sections) == text(late_raw, late_sections), 'late staging changed instructions')
            output = late
        objects.append(output)
    text, addresses = '.text\n.p2align 2\n', ''
    for name, address in BINDINGS.items():
        if name.endswith('gMetaClassE'):
            addresses += f'.globl {name}\n.set {name}, {address:#x}\n'
        else:
            addresses += f'.globl {name}.original\n.set {name}.original, {address:#x}\n'
            text += f'.globl {name}\n{name}:\n bti c\n b {name}.original\n'
    for name, content in [('original-bindings', text), ('original-addresses', addresses)]:
        source = args.output / (name + '.S')
        source.write_text(content)
        output = source.with_suffix('.o')
        run(['xcrun', 'clang', '-target', target.TRIPLE, '-c', source, '-o', output])
        target.check_object(output.read_bytes(), str(output))
        objects.append(output)
    all_symbols = [s for obj in objects for s in symbols_and_sections(obj)[1]]
    for name in ('_native_adreno_accelerator_allocate', '_native_adreno_accelerator_instance_size',
                 '__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp', '_sheng_gpu_power_probe',
                 '_NativeAdrenoGmuContextBringup', '_AdrenoPM4Type7', '_ArmSmmu4KAttachStreams'):
        definitions = [s for s in all_symbols if s[0] == name and s[2] & 0xe == 0xe]
        require(len(definitions) == 1 and not definitions[0][3] & 0x80, 'missing/weak/duplicate real definition: ' + name)
    # ponytail: no archive is published until every compile/ABI/late-init check passes;
    # full retained-kext prelink and hardware acceptance belong to parent integration.
    archive = args.output / 'libAmethystAdreno.late.a'
    run(['xcrun', 'ar', 'rcs', archive, *objects])
    report = dict(abi, archive_sha256=sha(archive), target=target.TRIPLE,
                  source_sha256={str(p): sha(p) for p in sources},
                  inputs=data, native_adreno_pin=PIN, board_pin=BOARD_PIN,
                  prelink_qualified=False, hardware_qualified=False)
    (args.output / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Built ' + str(archive) + '; NOT prelink/runtime/userspace qualified')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, StopIteration, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
