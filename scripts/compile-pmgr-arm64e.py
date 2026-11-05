#!/usr/bin/env python3
"""Two-pass PMGR compilation in an assembled iOS 27 tree; does not publish an archive."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--kernelcache', type=Path, required=True)
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
    records = tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT'
    record = json.loads((records / 'IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
    source = tree / 'kits/amethyst/ShengPlatformPower.cpp'
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
    print(f'PASS: two-pass 439-slot object audited: {final}; lifecycle bindings/archive still pending')


if __name__ == '__main__':
    main()
