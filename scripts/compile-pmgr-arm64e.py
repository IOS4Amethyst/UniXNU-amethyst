#!/usr/bin/env python3
"""Two-pass PMGR compilation and optional late-registration archive for iPhone15,4."""
import argparse
import importlib.util
import json
import hashlib
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--kernelcache', type=Path, required=True)
    parser.add_argument('--archive', action='store_true', help='Emit the qualified-profile late-registration archive')
    args = parser.parse_args()
    tree = args.tree.resolve()
    out = tree / 'build/sheng-platform-power-arm64e'
    out.mkdir(parents=True, exist_ok=True)
    audit_path = Path(__file__).with_name('audit-pmgr-arm64e.py')
    spec = importlib.util.spec_from_file_location('audit', audit_path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    header, guards = audit.abi_headers()
    (out / 'OriginalPMGRABI.h').write_text(header)
    (out / 'GuardedPMGRSlots.inc').write_text(guards)
    pac = out / 'OriginalPMGRPAC.h'
    # ponytail: first pass is audit input only and must never be linked or archived.
    pac.write_text('static void installOriginalPMGRTable(ShengPlatformPower *) { panic("PMGR audit-only object"); }\n')
    # The compile records sit under whichever configuration level the board selected, so read the
    # tree's own kernel_target rather than assuming the bring-up level.
    target_spec = importlib.util.spec_from_file_location('kernel_target', tree / 'scripts/kernel_target.py')
    target = importlib.util.module_from_spec(target_spec)
    target_spec.loader.exec_module(target)
    records = tree / target.OBJECT_TREE / 'iokit' / target.CONFIG_LEVEL
    record = json.loads((records / 'IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
    source = tree / 'kits/amethyst/ShengPlatformPower.cpp'
    if not source.is_file():
        raise SystemExit(f'{source} not found; the build tree must link the device package to kits/amethyst')
    obj = out / 'ShengPlatformPower.audit.o'
    command, i = [], 0
    arguments = record['arguments']
    while i < len(arguments):
        arg = arguments[i]
        if arg == record['file']:
            command.append(str(source))
        elif arg == '-o':
            command += ['-o', str(obj)]
            i += 1
        elif arg == '-Xclang' and i + 1 < len(arguments) and arguments[i + 1].startswith('-W'):
            i += 1
        elif not arg.startswith('-W'):
            command.append(arg)
        i += 1
    command += ['-DAMETHYST_PMGR_ARM64E=1', '-Wall', '-Wextra', '-Werror',
                '-Wno-unused-parameter', '-Wno-c99-designator', '-Wno-reorder-init-list',
                '-Wno-deprecated-enum-enum-conversion', '-Wno-unnecessary-virtual-specifier',
                '-I', str(out), '-I', str(tree / '.cache/xnu-work/osfmk')]
    subprocess.run(command, cwd=record['directory'], check=True)
    audit_command = [sys.executable, str(audit_path), '--prelink', str(tree / 'kits/XnuPrelink'),
                     '--kernelcache', str(args.kernelcache.resolve()),
                     '--iopmgr', str(records / 'IOPMGR.cpo'), '--provider', str(obj)]
    report = subprocess.check_output([*audit_command, '--emit-pac', str(pac)], text=True)
    first = audit.object_table(obj, '__ZTV18ShengPlatformPower')
    final = out / 'ShengPlatformPower.o'
    command[command.index('-o') + 1] = str(final)
    subprocess.run(command, cwd=record['directory'], check=True)
    second = audit.object_table(final, '__ZTV18ShengPlatformPower')
    audit.require(first == second, 'Second-pass provider symbols or PAC metadata changed')
    audit_command[-1] = str(final)
    report = subprocess.check_output(audit_command, text=True)
    (out / 'object-audit.json').write_text(report)
    if args.archive:
        sys.path.insert(0, str(tree / 'kits/XnuPrelink'))
        from kc_image import Image
        data = args.kernelcache.read_bytes()
        digest = audit.qualified_image(data)
        undefined = set(subprocess.check_output(['llvm-nm', '--undefined-only', str(final)], text=True).split())
        slide = 0 if digest == audit.D94['kernelcache_sha256'] else -0x1000000
        bindings = audit.lifecycle_bindings(Image(data), undefined, slide)
        assembly = out / 'original-bindings.S'
        lines = []
        for name, address in bindings.items():
            if digest == audit.D94['kernelcache_sha256'] and name != '__ZN9ApplePMGR10gMetaClassE':
                # Existing XnuPrelink relocation adapter: ld's direct N_ABS
                # branch has no split-seg record and otherwise moves with text.
                lines.append(f'.text\n.p2align 2\n.globl {name}\n{name}:\nbti c\nb {name}.original\n'
                             f'.globl {name}.original\n.set {name}.original, {address:#x}\n')
            else:
                lines.append(f'.globl {name}\n.set {name}, {address:#x}\n')
        assembly.write_text(''.join(lines))
        binding_object = out / 'original-bindings.o'
        subprocess.run(['xcrun', 'clang', '-target', 'arm64e-apple-ios27.0', '-c', str(assembly),
                        '-o', str(binding_object)], check=True)
        late = out / 'ShengPlatformPower.late.o'
        subprocess.run(['llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
                        '--globalize-symbol=__GLOBAL__sub_I_ShengPlatformPower.cpp', str(final), str(late)], check=True)
        audit.require(audit.object_table(late, '__ZTV18ShengPlatformPower') == second,
                      'Late-registration rewrite changed vtable symbols or PAC metadata')
        for object_path, dump in ((final, out / 'before.text'), (late, out / 'after.text')):
            subprocess.run(['llvm-objcopy', f'--dump-section=__TEXT,__text={dump}', str(object_path)], check=True)
        audit.require((out / 'before.text').read_bytes() == (out / 'after.text').read_bytes(),
                      'Late-registration rewrite changed instructions')
        # Publish only after both compilation and object rewriting succeed.
        archive = out / 'libShengPlatformPower.a'
        temporary = out / 'libShengPlatformPower.pending.a'
        temporary.unlink(missing_ok=True)
        subprocess.run(['xcrun', 'ar', 'rcs', str(temporary), str(late), str(binding_object)], check=True)
        temporary.replace(archive)
        (out / 'bindings.json').write_text(json.dumps({n: hex(v) for n, v in bindings.items()}, indent=2) + '\n')
        print(f'PASS: late-registration archive emitted: {archive}; link/runtime unverified')
        return
    print(f'PASS: two-pass 439-slot object audited: {final}; lifecycle bindings/archive still pending')


if __name__ == '__main__':
    main()
