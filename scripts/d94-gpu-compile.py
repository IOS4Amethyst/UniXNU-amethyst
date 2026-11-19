#!/usr/bin/env python3
"""Isolated live-XNU object qualification; never links, packages or enables GPU."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, **kw):
    return subprocess.run(list(map(str, args)), check=True, **kw)


def stage(archive, output):
    require(not output.exists(), 'staging directory must be new')
    # Python data filter rejects traversal, escaping links and special files.
    with tarfile.open(archive) as bundle:
        bundle.extractall(output, filter='data')
    builder = load('archive_recipe', Path(__file__).with_name('build-amethyst-adreno-archive.py'))
    pins = {'NativeAdreno': builder.PIN, 'NativeMetal': builder.HELPER_PINS['OriginalAcceleratorABI.h'],
            'AdrenoPM4': builder.HELPER_PINS['AdrenoPM4.c'],
            'ArmSmmu4K': builder.HELPER_PINS['ArmSmmu4K.c'],
            'AdrenoFirmware': builder.HELPER_PINS['AdrenoHfi.c'],
            'Sm7635Platform': builder.HELPER_PINS['ShengGmu.c']}
    for name, pin in pins.items():
        root = output / name
        actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        require(actual == pin, name + ': incorrect source revision')
        run(['git', '-C', root, 'diff', '--exit-code', pin, '--'])
        require(not subprocess.check_output(['git', '-C', str(root), 'ls-files', '--others']),
                name + ': untracked/ignored include inputs')
    require(sha(output / 'kernelcache') == builder.DONOR, 'incorrect D94 donor')
    generated = output / 'generated'
    for name, digest in {
        'ShengGmuSource.h': '2e6bb4352cffdf762e5ae6ab41a1f075a46e6f036b57e5719b5c117624b6820d',
        'ShengGmuVotesSource.h': '3a50210441c9fe0ba9efd7b71ee86f18d1ac9c777fd979a22f8fe4c357f7517c',
        'ShengGmuCommandDbSource.h': 'a42a76f427ab41cbc6c10fe3ca1f593e22cafbbe95cd2ca1f08416de97e374c5',
    }.items():
        require(sha(generated / name) == digest, name + ': incorrect amethyst generation')
    firmware = load('firmware', Path(__file__).with_name('prepare-amethyst-gpu-firmware.py'))
    for name, (header, _, digest) in firmware.FIRMWARE.items():
        blob = output / 'firmware' / name
        require(sha(blob) == digest, name + ': firmware mismatch')
        require((generated / header).read_text() == firmware.header(name, blob.read_bytes()),
                header + ': firmware array mismatch')
    board = output / 'board'
    board.mkdir()
    for name in ('GpuPowerProbe.cpp', 'GpuPowerBoard.h', 'BoardClasses.h'):
        content = subprocess.check_output(['git', '-C', str(Path(__file__).resolve().parents[1]),
                                          'show', builder.BOARD_PIN + ':scripts/log/' + name])
        (board / name).write_bytes(content)
    allowed = set(pins) | {'kernelcache', 'generated', 'firmware', 'board'}
    require({p.name for p in output.iterdir()} == allowed, 'unexpected staged top-level inputs')
    require({p.name for p in generated.iterdir()} == {
        'ShengGmuSource.h', 'ShengGmuVotesSource.h', 'ShengGmuCommandDbSource.h',
        'ShengGmuFirmware.h', 'ShengSqeFirmware.h', 'ShengZapFirmware.h'}, 'unexpected generated includes')


def compile_objects(core, device, inputs, output):
    require(sys.platform == 'darwin' and os.uname().machine == 'arm64', 'requires macOS ARM')
    require(not output.exists(), 'output must be new')
    output.mkdir(parents=True)
    sys.path.insert(0, str(core / 'scripts'))
    from unixnu.cli import parser, Context, prepare
    from unixnu import patches
    from unixnu.kernel import xnu_env, version_env
    ctx = Context(parser().parse_args(['build', 'kernel', '--os', 'ios-27.0.1-24A446-d94ap', '-D', str(device)]))
    ctx.refuse_example()
    ctx.need_config()
    modules = prepare(ctx, ['kernel'])
    env = dict(os.environ, **xnu_env(ctx))
    env.update(version_env(modules, ctx))
    env.update(UNIXNU_PATCH_LIST=str(patches.prepare(ctx, modules)),
               UNIXNU_GENERATED_DIR=str(ctx.tree / 'build/generated'), JOBS=str(ctx.jobs),
               XNU_BOOT_PROGRESS='0', XNU_BOOTSTRAP='1')
    Path(env['UNIXNU_GENERATED_DIR']).mkdir(parents=True, exist_ok=True)
    # Use the same bootstrap as kernel.build, stop before archive builders/prelink.
    with (output / 'bootstrap.log').open('w') as log:
        result = subprocess.run(['bash', str(ctx.tree / 'scripts/build-xnu-4k-baseline.sh')],
                                cwd=ctx.tree, env=env, stdout=log, stderr=subprocess.STDOUT)
    (output / 'bootstrap-status.json').write_text(json.dumps({'exit_code': result.returncode}))
    recipe = ctx.tree / 'build/xnu/ios-obj/RELEASE_ARM64_BCM2837/iokit/RELEASE/IOPlatformExpert.cpo.json'
    require(recipe.is_file(), f'bootstrap exit {result.returncode}: missing live recipe; see bootstrap.log')
    record = json.loads(recipe.read_text().strip().rstrip(','))
    args = record['arguments']
    require(any(a in ('--target=arm64e-apple-ios27.0.0', '--target=arm64e-apple-ios27.0.1',
                      'arm64e-apple-ios27.0.1', 'arm64e-apple-ios27.0') for a in args), 'wrong recipe target')
    definitions = {a[2:].split('=')[0] for a in args if a.startswith('-D') and len(a) > 2}
    definitions.update(args[i + 1].split('=')[0] for i, a in enumerate(args[:-1]) if a == '-D')
    require({'KERNEL_PRIVATE', 'XNU_KERNEL_PRIVATE', 'IOKIT_KERNEL_PRIVATE'} <= definitions,
            'missing private ABI flags')
    exports = ctx.tree / 'build/xnu/ios-obj/EXPORT_HDRS'
    require((exports / 'iokit/IOKit/IOUserClient.h').is_file(), 'missing actual exported IOUserClient')
    with tarfile.open(output / 'live-exported-headers.tar.gz', 'w:gz', dereference=True) as bundle:
        bundle.add(exports, arcname='EXPORT_HDRS')
    builder = load('object_recipe', device / 'scripts/build-amethyst-adreno-archive.py')
    flags = ['-mgeneral-regs-only', '-DSHENG_GMU_POOL_BASE=UINT64_C(0xe6440000)',
             '-DSHENG_GMU_POOL_BYTES=0x2000000u', '-fno-lto']
    for directory in sorted(p for p in inputs.iterdir() if p.is_dir()):
        flags += ['-I', str(directory)]
    flags += ['-I', str(ctx.tree / '.cache/xnu-work/osfmk')]
    sources = [inputs / 'NativeAdreno' / ('NativeAdreno' + n + '.cpp')
               for n in ('UserClient', 'Service')]
    sources += [inputs / 'board/GpuPowerProbe.cpp']
    report = {'qualification': 'objects only; ABI review pending', 'runtime_qualified': False,
              'bootstrap_exit': result.returncode, 'recipe': record, 'objects': {},
              'headers': {str(p.relative_to(exports)): sha(p) for p in exports.rglob('*') if p.is_file()}}
    for source in sources:
        obj = output / (source.stem + '.o')
        command = builder.compile_command(record, source, obj, flags + ['-MD', '-MF', str(obj.with_suffix('.d'))])
        (output / (source.stem + '.command.json')).write_text(json.dumps(command, indent=2))
        with (output / (source.stem + '.log')).open('w') as log:
            run(command, cwd=record['directory'], env=env, stdout=log, stderr=subprocess.STDOUT)
        builder.symbols_and_sections(obj)  # Actual arm64e MH_OBJECT, not LTO bitcode.
        with (output / (source.stem + '.nm')).open('w') as log:
            run(['xcrun', 'nm', '-m', obj], stdout=log)
        with (output / (source.stem + '.disassembly')).open('w') as log:
            run(['llvm-objdump', '--disassemble', '--reloc', obj], stdout=log)
        report['objects'][obj.name] = {'sha256': sha(obj), 'source_sha256': sha(source)}
    (output / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['stage', 'compile', 'self-check'])
    p.add_argument('--archive', type=Path)
    p.add_argument('--inputs', type=Path)
    p.add_argument('--core', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.mode == 'stage':
        stage(a.archive, a.inputs)
    elif a.mode == 'compile':
        compile_objects(a.core.resolve(), Path(__file__).resolve().parents[1], a.inputs.resolve(), a.output.resolve())
    else:
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.tar'
            with tarfile.open(archive, 'w') as bundle:
                entry = tarfile.TarInfo('../escaped')
                entry.size = 1
                bundle.addfile(entry, io.BytesIO(b'x'))
            try:
                stage(archive, Path(tmp) / 'output')
            except tarfile.FilterError:
                pass
            else:
                raise AssertionError('traversal accepted')
            assert not (Path(tmp) / 'escaped').exists()
        builder = load('recipe_check', Path(__file__).with_name('build-amethyst-adreno-archive.py'))
        record = {'file': 'old.cpp', 'arguments': ['clang', '-DKERNEL_PRIVATE', '-flto', '-c', 'old.cpp', '-o', 'old.o', '-MJ', 'old.json']}
        assert builder.compile_command(record, Path('new.cpp'), Path('new.o'), ['-fno-lto']) == [
            'clang', '-DKERNEL_PRIVATE', '-flto', '-c', 'new.cpp', '-o', 'new.o', '-fno-lto']
        print('PASS: extraction boundary and private-flag-preserving recipe substitution')


if __name__ == '__main__':
    main()
