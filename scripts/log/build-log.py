#!/usr/bin/env python3
"""Build the UFS diagnostic log (sheng-log=1) for Amethyst.

BootLog.c is sheng's writer: every 2 s it stores the board page text, the kernel
message buffer and any caught panic into NativeUfs's log window, so a boot can be
read back afterwards over ADB instead of photographed.

Why this matters here: Amethyst has no usable serial console (no Apple UART node
in its device tree), so the screen was the only output. This restores a real log
path without any hardware.

Adapted from UniXNU-asteroids scripts/build-boot-progress.py, which builds these
two files with -DXNU_BOOT_PROGRESS=1 against the osfmk startup recipe. We build
only the log, not sheng's console renderer: BootLog.c reads the renderer's
buffers through weak symbols, so it links and simply logs empty rows.
"""
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
KIT = Path(__file__).resolve().parent
# The built XNU tree lives beside this repository, not inside it.
TREE = ROOT.parent / 'UniXNU/out/amethyst/ipados-17.7.11-21H461/amethyst/tree'

RECIPE = TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json'
STRICT = ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']
FLAGS = ['-DXNU_BOOT_PROGRESS=1'] + STRICT + [
    '-I', str(TREE / '.cache/xnu-work/osfmk'), '-I', str(KIT)]


def compile_command(record, source, obj, extra, drop_own_define):
    """Replay a recorded recipe with our source and flags substituted."""
    args = list(record['arguments'])
    command, i, own = [], 0, drop_own_define
    while i < len(args):
        arg = args[i]
        if arg == record['file'] or arg.endswith('.c') or arg.endswith('.cpp'):
            command.append(str(source))
        elif arg in ('-o', '-MF', '-MT', '-MJ'):
            if arg == '-o':
                command += ['-o', str(obj)]
            i += 1
        elif arg == '-Xclang' and i + 1 < len(args) and args[i + 1].startswith('-W'):
            i += 1
        elif own and arg == '-D' and i + 1 < len(args) and args[i + 1].startswith('XNU_BOOT_PROGRESS='):
            i += 1
        elif not arg.startswith('-W') and not (own and arg.startswith('-DXNU_BOOT_PROGRESS=')):
            command.append(arg)
        i += 1
    result = subprocess.run(command + extra, cwd=record['directory'], capture_output=True, text=True)
    (KIT / (obj.stem + '-compile.log')).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(f'{obj.stem} failed:\n' + '\n'.join(
            line for line in (result.stdout + result.stderr).splitlines() if 'error:' in line)[:4000])
    return obj


if __name__ == '__main__':
    record = json.loads(RECIPE.read_text().strip().rstrip(','))
    # The recipe record wraps the real command; support both shapes.
    if 'arguments' not in record:
        record = record['jobs'][0] if 'jobs' in record else record
    objects = []
    for name in ('BootLog.c', 'BootLogStandins.c', 'AmethystCpuIdle.c'):
        obj = KIT / (Path(name).stem + '.o')
        objects.append(compile_command(record, KIT / name, obj, FLAGS, True))
        print('PASS: compiled', name)
    # Both must resolve their XNU symbols; check the log object's own imports.
    undefined = subprocess.check_output(['llvm-nm', '-u', str(KIT / 'BootLog.o')], text=True)
    (KIT / 'BootLog-undefined.txt').write_text(undefined)
    print('BootLog.o imports', len(undefined.splitlines()), 'symbols')
