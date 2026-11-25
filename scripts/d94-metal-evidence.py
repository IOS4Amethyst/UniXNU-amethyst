#!/usr/bin/env python3
"""Verify signed ios27 outputs; retain logs/provenance without a donor compatibility claim."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile


def arm64e(header):
    magic, cpu, subtype = struct.unpack('<III', header[:12])
    if (magic, cpu, subtype & 0xffffff) != (0xfeedfacf, 0x100000c, 2):
        raise ValueError('expected thin arm64e Mach-O')


def verify(core, out):
    plugin = core / 'build/adreno-metal-plugin27'
    bundle = plugin / 'NativeAdrenoMetal.bundle'
    trust = json.loads((plugin / 'trust.json').read_text())
    report = {}
    for name, key in [('NativeAdrenoMetal', 'cdhash'), ('libvulkan_freedreno.dylib', 'turnip_cdhash')]:
        binary = bundle / name
        arm64e(binary.read_bytes()[:12])
        subprocess.run(['codesign', '--verify', '--strict', str(binary)], check=True)
        detail = subprocess.check_output(['codesign', '-d', '--verbose=4', str(binary)], stderr=subprocess.STDOUT, text=True)
        (out / (name + '.codesign.txt')).write_text(detail)
        cdhash = re.search(r'^CDHash=([a-f0-9]{40})$', detail, re.M)
        if not cdhash or cdhash[1] != trust[key] or not re.search(r'^Page size=4096$', detail, re.M):
            raise ValueError('signature differs from builder trust.json: ' + name)
        report[key] = cdhash[1]
        # Named ordinals/weak imports are retained for the parent's exact D94 export audit.
        for suffix, command in [('imports', ['nm', '-m', '-u']), ('loads', ['otool', '-l'])]:
            (out / (name + '.' + suffix + '.txt')).write_bytes(subprocess.check_output(command + [str(binary)]))
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
    log = (out / 'plugin.log').read_text()
    pac = re.search(r'PASS: Rust indirect branches routed through the PAC trampolines: (\d+) blr, (\d+) br, (\d+) x30 left \((\d+) objects\)', log)
    if not pac or int(pac[1]) + int(pac[2]) == 0 or int(pac[4]) == 0:
        raise ValueError('missing/non-substantive PAC rewrite report')
    (out / 'pac-report.json').write_text(json.dumps(dict(zip(['blr', 'br', 'x30_left', 'objects'], map(int, pac.groups()))), indent=2) + '\n')
    report.update(bundle=bundle.name, target='arm64e-apple-ios27.0', donor_import_audit='pending', runtime='untested')
    (out / 'trust.json').write_text(json.dumps(report, indent=2) + '\n')
    shutil.copytree(bundle, out / bundle.name)


def collect(core, out):
    hashes = {}
    for directory in ('scripts', 'patches', 'drivers/AdrenoMetal/bridge/patches'):
        for path in sorted((core / directory).rglob('*')):
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as stream:
                    hashes[str(path.relative_to(core))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    for root in [core / 'build', core / '.cache']:
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(core)
            # All generated binaries, including failed builds, have reproducible byte identities.
            with path.open('rb') as stream:
                magic = stream.read(8)
                stream.seek(0)
                if magic[:4] in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\x7fELF') or magic == b'!<arch>\n':
                    hashes[str(relative)] = hashlib.file_digest(stream, 'sha256').hexdigest()
            if path.suffix in ('.log', '.map') or 'log' in path.name.lower() and path.suffix == '.txt' or path.name in ('Cargo.lock', 'Cargo.toml', 'builtin-build-profile.json', 'trust.json'):
                dest = out / 'build-evidence' / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
    for relative in ('scripts/data/adreno-compiler-inputs.json', 'scripts/data/gpu-metal-vulkan-inputs.json', 'scripts/requirements-adreno-compiler.txt'):
        path = core / relative
        if path.is_file():
            shutil.copy2(path, out / path.name)
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.name != 'sha256.json':
            with path.open('rb') as stream:
                hashes['artifact/' + str(path.relative_to(out))] = hashlib.file_digest(stream, 'sha256').hexdigest()
    (out / 'sha256.json').write_text(json.dumps(hashes, indent=2, sort_keys=True) + '\n')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        arm64e(struct.pack('<III', 0xfeedfacf, 0x100000c, 0x80000002))
        for cpu, subtype in [(0x100000c, 0), (0x1000007, 2)]:
            try:
                arm64e(struct.pack('<III', 0xfeedfacf, cpu, subtype))
            except ValueError:
                pass
            else:
                raise AssertionError('accepted wrong architecture')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core, out = root / 'core', root / 'output'
            (core / '.cache').mkdir(parents=True)
            (core / 'build').mkdir()
            out.mkdir()
            (core / '.cache/failure.log').write_text('fatal error: preserved\n')
            binary = struct.pack('<III', 0xfeedfacf, 0x100000c, 2)
            (core / 'build/partial.o').write_bytes(binary)
            collect(core, out)
            assert (out / 'build-evidence/.cache/failure.log').read_text() == 'fatal error: preserved\n'
            assert json.loads((out / 'sha256.json').read_text())['build/partial.o'] == hashlib.sha256(binary).hexdigest()
        print('PASS: arm64e capability bits; wrong architectures rejected; failure log and binary hash retained')
    elif len(sys.argv) == 4 and sys.argv[1] in ('verify', 'collect'):
        {'verify': verify, 'collect': collect}[sys.argv[1]](Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        sys.exit('usage: d94-metal-evidence.py {verify|collect} CORE OUTPUT | --self-test')
