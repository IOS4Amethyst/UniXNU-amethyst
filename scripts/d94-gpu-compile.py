#!/usr/bin/env python3
"""Isolated live-XNU archive qualification; never links, packages or enables GPU."""
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
    for name in ('GpuPowerProbe.cpp', 'GpuPowerBoard.h', 'BoardClasses.h',
                 'AmethystSmc.c', 'ShengGmuVotes.c'):
        content = subprocess.check_output(['git', '-C', str(Path(__file__).resolve().parents[1]),
                                          'show', builder.BOARD_PIN + ':scripts/log/' + name])
        (board / name).write_bytes(content)
    allowed = set(pins) | {'kernelcache', 'generated', 'firmware', 'board'}
    require({p.name for p in output.iterdir()} == allowed, 'unexpected staged top-level inputs')
    require({p.name for p in generated.iterdir()} == {
        'ShengGmuSource.h', 'ShengGmuVotesSource.h', 'ShengGmuCommandDbSource.h',
        'ShengGmuFirmware.h', 'ShengSqeFirmware.h', 'ShengZapFirmware.h'}, 'unexpected generated includes')
    # Patch the staged BOARD_PIN copy, not scripts/log in the device checkout.
    patches = Path(__file__).resolve().parents[1] / 'patches'
    run(['git', 'apply', '--whitespace=error', patches / 'gpu-board/a810-reset-check-mmio.patch'],
        cwd=board, env=dict(os.environ, GIT_CEILING_DIRECTORIES=str(board.parent.resolve())))
    for name in ('a810-recovery-isolate-vm.patch', 'a810-no-replay-on-context-fault.patch',
                 'a810-capture-hung-job.patch', 'a810-optional-hung-replay.patch'):
        run(['git', '-C', output / 'NativeAdreno', 'apply', '--whitespace=error', patches / 'native-adreno' / name])


def source_inventory(inputs, device):
    validator = load('input_inventory', device / 'scripts/build-amethyst-adreno.py')
    sources = []
    for name in validator.required_inputs():
        if name.endswith(('.c', '.cpp')):
            matches = list(inputs.rglob(name))
            require(len(matches) == 1, f'{name}: expected exactly one pinned source, got {matches}')
            sources.append(matches[0])
    sources.append(device / 'package/AmethystAdrenoDiagnostics.c')
    require(all(p.is_file() for p in sources), 'missing archive source')
    require(len({p.stem for p in sources}) == len(sources), 'duplicate object names')
    return sources


def import_closure(tables, native):
    definitions = {}
    imports = {}
    for owner, symbols in tables.items():
        for name, value, kind, desc in symbols:
            if kind & 0xe0 or not kind & 1:
                continue
            if kind & 0xe or value:
                definitions.setdefault(name, []).append(owner)
            else:
                imports.setdefault(name, []).append({'object': owner, 'weak': bool(desc & 0x40)})
    native_names = {n for n, value, kind, _ in native
                    if not kind & 0xe0 and kind & 1 and (kind & 0xe or value)}
    return {'imports': imports, 'definitions': definitions,
            'supplied_by_objects': sorted(imports.keys() & definitions.keys()),
            'available_in_native': sorted((imports.keys() - definitions.keys()) & native_names),
            'unresolved': sorted(imports.keys() - definitions.keys() - native_names),
            'native_availability_is_final_link_proof': False}


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
    c_recipe = recipe.parents[2] / 'bsd/RELEASE/bsd_init.o.json'
    require(c_recipe.is_file(), 'missing live C recipe: ' + str(c_recipe))
    records = {'cxx': record, 'c': json.loads(c_recipe.read_text().strip().rstrip(','))}
    for language, live in records.items():
        arguments = live['arguments']
        require(any('arm64e-apple-ios27.0' in a for a in arguments), language + ': wrong recipe target')
        private = {a[2:].split('=')[0] for a in arguments if a.startswith('-D') and len(a) > 2}
        private.update(arguments[i + 1].split('=')[0] for i, a in enumerate(arguments[:-1]) if a == '-D')
        require({'KERNEL_PRIVATE', 'XNU_KERNEL_PRIVATE'} <= private, language + ': missing private flags')
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
             '-DSHENG_GMU_POOL_BYTES=0x2000000u']
    for directory in sorted(p for p in inputs.iterdir() if p.is_dir()):
        flags += ['-I', str(directory)]
    gmu_headers = inputs / 'Sm7635Platform/kits/Sm7635Platform'
    require((gmu_headers / 'ShengGmu.h').is_file(), 'missing pinned SM7635 GMU header')
    flags += ['-I', str(gmu_headers)]
    flags += ['-I', str(ctx.tree / '.cache/xnu-work/osfmk')]
    sources = source_inventory(inputs, device)
    for directory in sorted({p.parent for p in sources}):
        flags += ['-I', str(directory)]
    abi = builder.audit_kernel(core, inputs / 'kernelcache')
    report = {'qualification': 'isolated full inventory; not runtime-builder contract approval',
              'runtime_qualified': False, 'prelink_qualified': False, 'hardware_qualified': False,
              'bootstrap_exit': result.returncode, 'recipe': record, 'recipes': records, 'objects': {},
              'headers': {str(p.relative_to(exports)): sha(p) for p in exports.rglob('*') if p.is_file()}}
    objects, tables = [], {}
    def evidence(obj):
        tables[obj.name] = builder.symbols_and_sections(obj)[1]
        with (output / (obj.stem + '.nm')).open('w') as log:
            run(['xcrun', 'nm', '-m', obj], stdout=log)
        with (output / (obj.stem + '.disassembly')).open('w') as log:
            run(['llvm-objdump', '--disassemble', '--reloc', obj], stdout=log)
    def save():
        (output / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')
    save()
    for source in sources:
        obj = output / (source.stem + '.o')
        record = records['c' if source.suffix == '.c' else 'cxx']
        command = builder.compile_command(record, source, obj, flags + ['-MD', '-MF', str(obj.with_suffix('.d'))])
        (output / (source.stem + '.command.json')).write_text(json.dumps(command, indent=2))
        with (output / (source.stem + '.log')).open('w') as log:
            run(command, cwd=record['directory'], env=env, stdout=log, stderr=subprocess.STDOUT)
        evidence(obj)  # Actual arm64e MH_OBJECT, not LTO bitcode.
        report['objects'][obj.name] = {'sha256': sha(obj), 'source_sha256': sha(source)}
        save()
        if source.name == 'NativeAdrenoAccelerator.cpp':
            builder.audit_accelerator(obj, abi)
            late = obj.with_suffix('.late.o')
            run(['llvm-objcopy', '--remove-section=__DATA,__mod_init_func',
                 '--globalize-symbol=__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp', obj, late])
            raw, _, sections = builder.symbols_and_sections(obj)
            late_raw, symbols, late_sections = builder.symbols_and_sections(late)
            require(not any(n == '__mod_init_func' for n, *_ in late_sections), 'early constructor survived')
            require(any(n == '__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp' and k & 1
                        for n, _, k, _ in symbols), 'late constructor not exported')
            def text(data, secs):
                return b''.join(data[o:o + size] for n, _, size, o, _, _ in secs if n == '__text')
            require(text(raw, sections) == text(late_raw, late_sections), 'late staging changed instructions')
            del tables[obj.name]
            obj = late
            evidence(obj)
            report['objects'][obj.name] = {'sha256': sha(obj), 'source_sha256': sha(source)}
            report['accelerator_audit'] = {k: v for k, v in abi.items() if k != 'slots'}
            save()
        objects.append(obj)
    # Exact reviewed donor bindings, identical to the runtime builder; no fake fills.
    text, addresses = '.text\n.p2align 2\n', ''
    for name, address in builder.BINDINGS.items():
        if name.endswith('gMetaClassE'):
            addresses += f'.globl {name}\n.set {name}, {address:#x}\n'
        else:
            addresses += f'.globl {name}.original\n.set {name}.original, {address:#x}\n'
            text += f'.globl {name}\n{name}:\n bti c\n b {name}.original\n'
    for name, content in [('original-bindings', text), ('original-addresses', addresses)]:
        source = output / (name + '.S')
        source.write_text(content)
        obj = source.with_suffix('.o')
        run(['xcrun', 'clang', '-target', 'arm64e-apple-ios27.0.1', '-c', source, '-o', obj])
        evidence(obj)
        objects.append(obj)
        report['objects'][obj.name] = {'sha256': sha(obj), 'source_sha256': sha(source)}
    native = []
    report['native_comparators'] = []
    for path in sorted(recipe.parents[2].glob('mach.*.noctf')):
        nm = output / (path.name + '.nm')
        with nm.open('w') as log:
            run(['xcrun', 'nm', '-g', '-U', '-j', path], stdout=log)
        native += [(name, 0, 0xf, 0) for name in nm.read_text().splitlines() if name]
        report['native_comparators'].append({'path': str(path), 'sha256': sha(path)})
    report['import_closure'] = import_closure(tables, native)
    archive = output / 'libAmethystAdreno.qualification.late.a'
    run(['xcrun', 'ar', 'rcs', archive, *objects])
    members = subprocess.check_output(['xcrun', 'ar', 't', str(archive)], text=True).splitlines()
    # Apple ar exposes its symbol index; it is metadata, not a compiled member.
    members = [name for name in members if name not in ('__.SYMDEF', '__.SYMDEF SORTED')]
    require(members == [p.name for p in objects], 'archive member inventory mismatch')
    report['archive'] = {'sha256': sha(archive), 'members': members}
    save()


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
        assert builder.compile_command(record, Path('new.cpp'), Path('new.o'), []) == [
            'clang', '-DKERNEL_PRIVATE', '-flto', '-c', 'new.cpp', '-o', 'new.o', '-fno-lto']
        closure = import_closure({'a.o': [('local', 0, 1, 0), ('native', 0, 1, 0),
                                        ('missing', 0, 1, 0x40)],
                                  'b.o': [('local', 4, 0xf, 0)]}, [('native', 8, 0xf, 0)])
        assert closure['supplied_by_objects'] == ['local']
        assert closure['available_in_native'] == ['native']
        assert closure['unresolved'] == ['missing'] and closure['imports']['missing'][0]['weak']
        print('PASS: extraction boundary, recipe preservation, full-object import closure')


if __name__ == '__main__':
    main()
