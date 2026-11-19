#!/usr/bin/env python3
"""Stdlib host checks; optional --core CORE --kernelcache D94 runs real ABI audit."""
import argparse
import importlib.util
from pathlib import Path
import tempfile
import hashlib

spec = importlib.util.spec_from_file_location('archive', Path(__file__).with_name('build-amethyst-adreno-archive.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def rejected(call):
    try:
        call()
    except (ValueError, OSError):
        return
    raise AssertionError('unsafe input accepted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', type=Path)
    parser.add_argument('--kernelcache', type=Path)
    args = parser.parse_args()
    record = {'file': '/src/original.cpp', 'arguments': ['clang++', '-target', 'arm64e-apple-ios27.0.0',
              '-D', 'RELEASE', '-MD', '-MF', '/old.d', '-MJ', '/old.json', '-o', '/old.o',
              '-c', '/src/original.cpp', '-fapple-kext']}
    command = builder.compile_command(record, Path('/new.cpp'), Path('/new.o'), ['-mgeneral-regs-only'])
    assert command == ['clang++', '-target', 'arm64e-apple-ios27.0.0', '-D', 'RELEASE',
                       '-o', '/new.o', '-c', '/new.cpp', '-fapple-kext', '-mgeneral-regs-only', '-fno-lto']
    lto_record = dict(record, arguments=record['arguments'] + ['-flto=thin'])
    inspected = builder.compile_command(lto_record, Path('/new.cpp'), Path('/new.o'), [])
    assert inspected[-1] == '-fno-lto' and inspected.index('-flto=thin') < len(inspected) - 1
    rejected(lambda: builder.compile_command({'file': 'missing', 'arguments': ['clang']}, Path('a'), Path('b'), []))
    with tempfile.TemporaryDirectory(prefix='amethyst-adreno-') as temp:
        wrong = Path(temp) / 'wrong-kernel'
        wrong.write_bytes(b'not a D94 donor')
        rejected(lambda: builder.audit_kernel(Path(temp), wrong))
        firmware = builder.load_module('firmware', Path(__file__).with_name('prepare-amethyst-gpu-firmware.py'))
        rejected(lambda: firmware.header('gen80300_zap.mbn', b'foreign OEM zap'))
        # Exercise generation/validation with a small fixture without redistributing firmware.
        blob = bytes(range(256))
        firmware.FIRMWARE = {'test.bin': ('Test.h', 'test_firmware', hashlib.sha256(blob).hexdigest())}
        root = Path(temp)
        (root / 'test.bin').write_bytes(blob)
        firmware.prepare(root, root / 'generated')
        text = (root / 'generated/Test.h').read_text()
        array = text.split('={\n', 1)[1].split('};', 1)[0]
        assert bytes(int(v) for v in array.replace('\n', '').split(',') if v) == blob
        rejected(lambda: firmware.prepare(root, root / 'generated'))
        (root / 'test.bin').write_bytes(blob[:-1])
        rejected(lambda: firmware.prepare(root, root / 'invalid'))
        assert not (root / 'invalid').exists()
    diagnostics = Path(__file__).parents[1] / 'package/AmethystAdrenoDiagnostics.c'
    assert 'accelerator_allocate' not in diagnostics.read_text()
    assert 'accelerator_static_init' not in diagnostics.read_text()
    if args.kernelcache:
        assert args.core is not None, '--kernelcache requires --core'
        abi = builder.audit_kernel(args.core, args.kernelcache)
        assert abi['instance_bytes'] == 136 and abi['virtual_entries'] == 168
        assert abi['runtime_qualified'] is False
        print('PASS: exact D94 instruction identities; 168 original IA/address PAC slots; IOService overrides only 0/1/7')
    print('PASS: RELEASE recipe replay, dependency-output stripping, malformed recipe/wrong donor rejection; no fake accelerator')
    print('PASS: firmware byte round trip, foreign/corrupt blob rejection, no overwrite or partial invalid output')


if __name__ == '__main__':
    main()
