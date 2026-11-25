#!/usr/bin/env python3
"""Apply the default-off GPU hook AFTER native-d94-release-display.patch.

No config/bootarg changes. Parent integration must link libAmethystAdreno.late.a
and qualify its dependencies before defining XNU_NATIVE_AMETHYST_GPU=1.
"""
import argparse
from pathlib import Path
import subprocess


def apply(source, check=False):
    patch = Path(__file__).resolve().parents[1] / 'patches/xnu/native-d94-release-gpu.patch'
    # Reject duplicate registration implementations rather than overriding one.
    kext = (source / 'libkern/c++/OSKext.cpp').read_text()
    if 'OSKextRegisterBoardClasses' in kext:
        raise ValueError('board-class registration already present; review patch ordering')
    bsd = (source / 'bsd/kern/bsd_init.c').read_text()
    anchor = ('\tkern_return_t result = IOKitBSDInit();\n'
              '#if defined(XNU_NATIVE_LEGACY_DISPLAY) && XNU_NATIVE_LEGACY_DISPLAY\n')
    if bsd.count(anchor) != 1:
        raise ValueError('requires exact late RELEASE display hook; apply native-d94-release-display.patch first')
    command = ['git', 'apply', '--directory=' + str(source.resolve()), str(patch)]
    # git apply is atomic across files (no --reject); never accept fuzz/reverse.
    subprocess.run(command[:2] + ['--check', '--unsafe-paths'] + command[2:], check=True)
    if not check:
        subprocess.run(command[:2] + ['--unsafe-paths'] + command[2:], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        apply(args.source, args.check)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')
