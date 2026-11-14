#!/usr/bin/env python3
"""Fail-closed A810 input audit; archive generation is blocked on the D94 accelerator ABI.

Usage: UNIXNU_XNU_ARCH=arm64e UNIXNU_IOS_VERSION=27.0.1 \
  UNIXNU_XNU_CONFIG_LEVEL=RELEASE python3 scripts/build-amethyst-adreno.py \
  --core /absolute/resolved/core/tree --manifest /absolute/inputs.json --validate-only

The caller-reviewed JSON manifest maps the logical names in required_inputs() to
{"path": "/absolute/file", "sha256": "<64 hex digits>"}. No files are fetched,
generated, compiled or packaged. Manifest hashes establish input identity, NOT
amethyst hardware provenance or D94 ABI compatibility. No .scratch defaults.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess


ADRENO_PIN = '26c1da780d602c7bfe2fcd62bff41f9009ead6a3'
D94_SHA256 = 'fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95'
CPP = '''Memory SmmuMemory Vm GmuMemory SmmuContext GmuContext Cpu Sha256
GpuContext Ring CpInit Pwrup Submit Timer Profile UserClient Publish Accelerator'''.split()
BLOCKER = ('Archive disabled: D94 IOAcceleratorES superclass/vtable/PAC bindings and '
           'late constructor registration are not reviewed; main assumes size 136. '
           'Verify amethyst firmware/votes/command-db provenance and loader GMU pool '
           'reservation before enabling hardware. Weak accelerator stand-ins are not a substitute.')


def required_inputs():
    names = ['kernelcache', 'recipe-c', 'recipe-cxx', 'OriginalAcceleratorABI.h']
    names += ['NativeAdreno' + name + '.cpp' for name in CPP]
    names += ['NativeAdrenoA810HwInit.h']
    names += ['GpuPowerProbe.cpp', 'GpuPowerBoard.h', 'BoardClasses.h',
              'AmethystSmc.c', 'ShengGmuVotes.c', 'ShengGmuVotes.h',
              'ShengGmu.c', 'ShengGmu.h', 'ShengGmuCommandDb.c', 'ShengGmuCommandDb.h']
    names += [name + '.h' for name in ('ShengGmuSource', 'ShengGmuFirmware',
              'ShengSqeFirmware', 'ShengZapFirmware', 'ShengGmuCommandDbSource')]
    names += ['gen80300_gmu.bin', 'gen80300_sqe.fw', 'gen80300_zap.mbn']
    names += [name + '.c' for name in ('AdrenoHfiVotes', 'AdrenoHfiStartup',
              'AdrenoHfi', 'AdrenoFirmware', 'AdrenoPM4')]
    names += ['ArmSmmu4K' + suffix + '.c' for suffix in
              ('', 'TableMemory', 'Mmio', 'Program', 'Live', 'Audit', 'Streams', 'Context')]
    return names


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_files(manifest):
    require(isinstance(manifest, dict), 'manifest must be a JSON object')
    missing = sorted(set(required_inputs()) - manifest.keys())
    require(not missing, 'missing manifest inputs: ' + ', '.join(missing))
    paths = {}
    for name, entry in manifest.items():
        require(isinstance(entry, dict), f'{name}: expected path/sha256 object')
        path, digest = entry.get('path'), entry.get('sha256')
        require(isinstance(path, str) and Path(path).is_absolute(), f'{name}: path must be absolute')
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), f'{name}: invalid sha256')
        path = Path(path)
        require(path.is_file(), f'{name}: missing file: {path}')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == digest, f'{name}: SHA-256 mismatch: expected {digest}, actual {actual}')
        paths[name] = path
    require(manifest['kernelcache']['sha256'] == D94_SHA256, 'requires the reviewed D94 24A446 kernelcache: ' + D94_SHA256)
    return paths


def check_recipe(path, expected, core, target):
    require(path.resolve() == (core / expected).resolve(), f'recipe must be selected from {expected}')
    record = json.loads(path.read_text().strip().rstrip(','))
    args = record.get('arguments')
    require(isinstance(args, list) and all(isinstance(x, str) for x in args), f'{path}: missing recipe arguments')
    require(any(args[i:i + 2] == ['-target', target] for i in range(len(args))), f'{path}: requires -target {target}')
    require('RELEASE' in str(record.get('output', '')) + ' '.join(args), f'{path}: missing RELEASE output')
    require(Path(record.get('directory', '')).is_absolute() and Path(record['directory']).is_dir(), f'{path}: recipe directory unavailable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--validate-only', action='store_true', help='audit identities only, not build readiness')
    args = parser.parse_args()
    # ponytail: input audit only; add compilation once the D94 accelerator ABI is reviewed.
    require(args.core.is_absolute() and args.core.is_dir(), '--core must be an absolute resolved core tree')
    require(args.manifest.is_absolute(), '--manifest must be absolute')
    require(os.environ.get('UNIXNU_XNU_ARCH') == 'arm64e' and
            os.environ.get('UNIXNU_IOS_VERSION') == '27.0.1' and
            os.environ.get('UNIXNU_XNU_CONFIG_LEVEL') == 'RELEASE',
            'set UNIXNU_XNU_ARCH=arm64e UNIXNU_IOS_VERSION=27.0.1 UNIXNU_XNU_CONFIG_LEVEL=RELEASE')
    spec = importlib.util.spec_from_file_location('amethyst_kernel_target', args.core / 'scripts/kernel_target.py')
    target = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(target)
    require((target.ARCH, target.CONFIG_LEVEL, target.TRIPLE) ==
            ('arm64e', 'RELEASE', 'arm64e-apple-ios27.0.1'), 'kernel_target disagrees with required target')
    paths = checked_files(json.loads(args.manifest.read_text()))
    for name, relative in [('recipe-c', 'bsd/RELEASE/bsd_init.o.json'),
                           ('recipe-cxx', 'iokit/RELEASE/IOPlatformExpert.cpo.json')]:
        check_recipe(paths[name], target.OBJECT_TREE + '/' + relative, args.core, target.TRIPLE)
    adreno = paths['NativeAdrenoAccelerator.cpp'].parent
    head = subprocess.check_output(['git', '-C', str(adreno), 'rev-parse', 'HEAD'], text=True).strip()
    require(head == ADRENO_PIN, f'IOS4Amethyst/NativeAdreno requires {ADRENO_PIN}, got {head}')
    for name in required_inputs():
        if name.startswith('NativeAdreno'):
            require(paths[name].resolve() == (adreno / name).resolve(), f'{name}: must come from pinned NativeAdreno checkout')
            pinned = subprocess.check_output(['git', '-C', str(adreno), 'show', ADRENO_PIN + ':' + name])
            require(paths[name].read_bytes() == pinned, f'{name}: differs from pinned source')
    print(f'Input identities validated: {len(paths)} files; {target.TRIPLE} RELEASE; NativeAdreno {ADRENO_PIN}.')
    print(BLOCKER)
    return 0 if args.validate_only else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
