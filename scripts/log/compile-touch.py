#!/usr/bin/env python3
"""Compile the Amethyst touch sources against the recorded XNU recipes.

Kept separate from build-log.py so the log pipeline and the touch pipeline have
independent entry points. The recipe is chosen by language, not by preference:
the recorded C recipes pass -xc, so a C++ source compiled with one is parsed as
C and dies on `class`/`template` in the IOKit headers. Object files land in
.scratch/ufs-audit so a failed compile never touches the link inputs.
"""
import importlib.util
import json
from pathlib import Path

KIT = Path(__file__).resolve().parent
PROJECT = KIT.parents[2]       # SmallProject, which holds the UniXNU checkout
TREE = PROJECT / 'UniXNU/out/amethyst/ipados-17.7.11-21H461/amethyst/tree'
OBJ = TREE / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
# startup.o.json is kern/startup.c: a C++ source would be parsed as C.
RECIPE_C = OBJ / 'osfmk/DEVELOPMENT/startup.o.json'
# IOPlatformExpert.cpo.json is the Objective-C++/IOKit recipe the display
# provider already builds with, so it carries the C++ headers and search paths.
RECIPE_CPP = OBJ / 'iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
OUT = PROJECT / '.scratch/ufs-audit'
GENI = PROJECT / 'UniXNU/drivers/QcomGeniSpi'
STRICT = ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']
FLAGS = STRICT + [
    '-I', str(TREE / '.cache/xnu-work/osfmk'), '-I', str(KIT), '-I', str(GENI),
    # AmethystGpuProbe.cpp mode 5 (compile-gpu.py builds these sources)
    '-I', str(PROJECT / '.scratch/gpu-src/NativeAdreno'), '-I', str(PROJECT / '.scratch/gpu-src/ArmSmmu4K'),
    '-I', str(PROJECT / '.scratch/sm7635-reference/kits/Sm7635Platform'), '-I', str(PROJECT / '.scratch/gmu-gen/build/sheng-gmu'),
    '-I', str(PROJECT / 'UniXNU-amethyst/kits/AdrenoFirmware')]

spec = importlib.util.spec_from_file_location('build_log', KIT / 'build-log.py')
build_log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_log)

SOURCES = [
    'AmethystTouchSnapshot.cpp',
    'AmethystTouchDma.cpp',
    'AmethystTouchLoopbackStage.cpp',
    'AmethystTouchLoopback.c',
    'GoodixBerlinGeni.c',
    'AmethystTouchGoodixStage.cpp',
    'AmethystTouchEvents.cpp',
    'CpuFreqProbe.cpp',
    'QcomRpmh.c',
]
# The GENI SPI driver is a shared component, not a board file, but the loopback
# stage and the Goodix reader both call into it, so it is compiled here as a C
# object and linked alongside the rest.
SHARED_C = ['GeniSpi.c']
# Board sources whose object keeps its historical name in the link command (no .touch suffix).
PLAIN = ['AmethystButtonsHid.cpp', 'AmethystPanel.cpp', 'AmethystBacklight.cpp']
# Board IOService classes whose static initializer must leave __mod_init_func: AmethystPanel.cpp registers them under the kext lock
# (OSKextRegisterBoardClasses) and calls the globalized initializer itself.
LATE = ['AmethystBacklight.cpp']


def load_recipe(path):
    record = json.loads(path.read_text().strip().rstrip(','))
    return record['jobs'][0] if 'jobs' in record else record


def compile_with(path, src, obj):
    record = load_recipe(path)
    # The replayer strips -W flags, which removes XNU's own suppressions. Restore
    # them, then let them WIN over our -Werror: these warnings come from XNU's
    # exported user/kernel and IOKit headers, which the real build tolerates. Our
    # sources are still held to -Wall -Wextra -Werror.
    kept = [arg for arg in record['arguments'] if arg.startswith('-Wno-')]
    build_log.compile_command(record, src, obj, FLAGS + kept, False)


def main():
    recipes = {'.c': RECIPE_C, '.cpp': RECIPE_CPP}
    for name in SOURCES:
        src = KIT / name
        if not src.exists():
            raise SystemExit(f'missing source {name}')
        obj = OUT / (Path(name).stem + '.touch.o')
        compile_with(recipes[src.suffix], src, obj)
        print('PASS: compiled', name)
    for name in PLAIN:
        src = KIT / name
        compile_with(recipes[src.suffix], src, OUT / (Path(name).stem + '.o'))
        print('PASS: compiled', name)
    import subprocess
    for name in LATE:
        stem = Path(name).stem
        subprocess.run(['llvm-objcopy', '--remove-section=__DATA,__mod_init_func', f'--globalize-symbol=__GLOBAL__sub_I_{name}',
                        str(OUT / f'{stem}.o'), str(OUT / f'{stem}.late.o')], check=True)
        print('PASS: late object', f'{stem}.late.o')
    for name in SHARED_C:
        src = GENI / name
        if not src.exists():
            raise SystemExit(f'missing shared source {name}')
        obj = OUT / (Path(name).stem + '.touch.o')
        compile_with(RECIPE_C, src, obj)
        print('PASS: compiled shared', name)


if __name__ == '__main__':
    main()
