#!/usr/bin/env python3
"""Pinned, kernel-only D94 GPU relink; runtime pool contract remains unqualified."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tomllib

ENCRYPTED = '414126e1d072ffe7558c680bae18b10e9f2328c86e4c453fbc1d876162664f2f'
ARCHIVE = '70084d071b775357d3298420e6287e1fea2daf1f618c1bbac3b5dff02d0e1dde'
NAME = 'libAmethystAdreno.qualification.late.a'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def builder():
    spec = importlib.util.spec_from_file_location('gpu', Path(__file__).with_name('build-amethyst-adreno-archive.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate(directory):
    report = json.loads((directory / 'qualification.json').read_text())
    require(sha(directory / NAME) == ARCHIVE == report['archive']['sha256'], 'archive hash mismatch')
    members = subprocess.check_output(['llvm-ar', 't', str(directory / NAME)], text=True).splitlines()
    members = [n for n in members if not n.startswith('__.SYMDEF')]
    require(members == report['archive']['members'] and len(members) == 39, 'archive inventory mismatch')
    require('NativeAdrenoAccelerator.late.o' in members and 'NativeAdrenoAccelerator.o' not in members,
            'requires late constructor only')
    for name in members:
        raw = subprocess.check_output(['llvm-ar', 'p', str(directory / NAME), name])
        require(hashlib.sha256(raw).hexdigest() == report['objects'][name]['sha256'], 'member mismatch: ' + name)
    for record in report['recipes'].values():
        require('RELEASE_ARM64_BCM2837' in record['directory'] and
                '--target=arm64e-apple-ios27.0.0' in record['arguments'], 'wrong native recipe provenance')
    require(report['accelerator_audit']['kernelcache_sha256'] == builder().DONOR, 'wrong donor')
    require(report['headers'] and report['runtime_qualified'] is False, 'missing header/runtime provenance')
    return report


def prepare(a):
    require(sha(a.encrypted) == ENCRYPTED, 'encrypted artifact hash mismatch')
    a.inputs.mkdir(parents=True, exist_ok=False)
    tar = a.inputs / 'inputs.tar.gz'
    subprocess.run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-iter', '200000',
                    '-pass', 'env:ARTIFACT_PASSPHRASE', '-in', str(a.encrypted), '-out', str(tar)], check=True)
    with tarfile.open(tar) as stream:
        stream.extractall(a.inputs, filter='data')
    validate(a.inputs / 'qualification')
    board = a.device / 'board.toml'
    text = board.read_text()
    config = tomllib.loads(text)
    require(config['kernel']['config_level'] == 'RELEASE', 'requires RELEASE')
    require(not any(x.startswith('sheng-gpu-power=') and x != 'sheng-gpu-power=0'
                    for x in config['payload']['kernel_args']), 'GPU runtime must remain off')
    needle = '  "build-fileset-release-reference.py --tree . --lto",'
    require(text.count(needle) == 1, 'run prepare-d94-build first')
    archives = config['kernel']['archives']
    require('build-gop-legacy-display-27.py' in archives and
            archives.index('build-gop-legacy-display-27.py') <
            archives.index('build-fileset-release-reference.py --tree . --lto'),
            'D94 GOP archive must precede the release reference and GPU stage')
    command = 'prepare-d94-gpu-link.py stage --inputs ' + str(a.inputs.resolve()) + ' --core ' + str(a.core.resolve())
    board.write_text(text.replace(needle, needle + '\n  ' + json.dumps(command) + ','))
    series = a.device / 'patches/xnu/series'
    text = series.read_text()
    require('native-d94-release-display.patch' in text and 'native-d94-release-gpu.patch' not in text, 'patch ordering')
    series.write_text(text.rstrip() + '\nnative-d94-release-gpu.patch\n')
    # The Data-mount boot config (ios27_data_defconfig) takes the same GPU archive as the link audit config.
    selected = os.environ.get('DEFCONFIG', 'amethyst_defconfig')
    require(selected in ('amethyst_defconfig', 'ios27_data_defconfig'), 'unsupported GPU defconfig: ' + selected)
    config = a.device / 'configs' / selected
    text = config.read_text()
    needle = 'CONFIG_XNU_EXTRA_LD_ARCHIVES="'
    require(text.count(needle) == 1, 'ambiguous archive config')
    text = text.replace(needle, needle + '-Wl,-force_load,{tree}/build/d94-gpu/' + NAME + ' ')
    # The native builder owns both force-loading the GOP archive and display hook macros.
    # Building the archive alone leaves GPUProbe/SourceStorageBootLog imports unresolved.
    display = 'CONFIG_XNU_NATIVE_LEGACY_DISPLAY=n\n'
    enabled = 'CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y\n'
    require(text.count(display) + text.count(enabled) == 1, 'unexpected GPU audit display configuration')
    config.write_text(text.replace(display, enabled))
    # Preserve the platform warning flags; add only the hook macro in this disposable core.
    script = a.core / 'scripts/build-xnu-4k-baseline.sh'
    text = script.read_text()
    needle = 'EXTRA_CFLAGS="$EXTRA_CFLAGS -DXNU_BOOT_PROGRESS=$BOOT_PROGRESS"'
    require(text.count(needle) == 1, 'unknown native compile recipe')
    script.write_text(text.replace(needle, needle + '\nEXTRA_CFLAGS="$EXTRA_CFLAGS -DXNU_NATIVE_AMETHYST_GPU=1"'))
    profile = a.core / 'profiles/os/ios-27.0.1-24A446-d94ap/profile.toml'
    text = profile.read_text()
    require(text.count(', "--allow-missing"') == 1, 'unknown prelink policy')
    profile.write_text(text.replace(', "--allow-missing"', ''))


def stage(a):
    directory = a.inputs / 'qualification'
    report = validate(directory)
    exports = Path('build/xnu/ios-obj/EXPORT_HDRS')
    for name, digest in report['headers'].items():
        require(sha(exports / name) == digest, 'live private header drift: ' + name)
    builder().audit_kernel(a.core, Path(os.environ['UNIXNU_KERNELCACHE']))
    source = Path('.cache/xnu-work')
    require('OSKextRegisterBoardClasses' in (source / 'libkern/c++/OSKext.cpp').read_text(), 'registration patch absent')
    out = Path('build/d94-gpu')
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(directory / NAME, out / NAME)
    shutil.copy2(directory / 'qualification.json', out / 'input-qualification.json')


def verify(a):
    records = list(a.core.glob('out/amethyst/ios-27.0.1-24A446-d94ap/*/kernel/kernel.json'))
    require(len(records) == 1, 'expected one retained prelink output')
    record = json.loads(records[0].read_text())
    require(record.get('candidate_written') and records[0].with_suffix('.macho').is_file(), 'no shipped candidate')
    require(not any(record.get(key, {}).values() for key in ('missing', 'stubbed')), 'unresolved retained imports')
    bindings = builder().BINDINGS
    found = {x['symbol']: x for x in record.get('native_original_branches', [])}
    for name, address in bindings.items():
        if name.endswith('gMetaClassE'):
            continue
        require(name in found and int(found[name]['target'], 16) == address and found[name]['owner'],
                'retained accelerator binding absent: ' + name)
    natives = list(a.core.glob('out/**/tree/build/xnu/ios-obj/RELEASE_ARM64_BCM2837/mach.*.unstripped.noctf'))
    require(len(natives) == 1, 'expected one unstripped native kernel')
    sys_path = str(a.core / 'kits/XnuPrelink')
    import sys
    sys.path.insert(0, sys_path)
    from kc_image import Image
    absolute = Image(natives[0].read_bytes()).absolute_symbols()
    meta = '__ZN15IOAcceleratorES10gMetaClassE'
    require(absolute.get(meta) == bindings[meta], 'original metaclass binding drift')
    names = subprocess.check_output(['llvm-nm', '--defined-only', str(natives[0])], text=True)
    for name in ('_OSKextRegisterBoardClasses', '_native_adreno_accelerator_allocate',
                 '__GLOBAL__sub_I_NativeAdrenoAccelerator.cpp', '_native_legacy_display_probe',
                 '_native_legacy_display_allocate', '_native_legacy_transfer_calls'):
        require(name in names, 'native implementation absent: ' + name)
    print('PASS: full native link and retained accelerator branches; runtime/hardware unqualified')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['prepare', 'stage', 'verify', 'validate'])
    p.add_argument('--core', type=Path)
    p.add_argument('--device', type=Path)
    p.add_argument('--inputs', type=Path)
    p.add_argument('--encrypted', type=Path)
    a = p.parse_args()
    if a.mode == 'validate':
        validate(a.inputs)
        print('PASS: pinned 39-member late archive, native target, donor and header provenance')
    else:
        globals()[a.mode](a)


if __name__ == '__main__':
    main()
