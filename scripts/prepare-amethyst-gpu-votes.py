#!/usr/bin/env python3
"""Offline amethyst GMU headers; reuse pinned generators, write only a new output directory."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DTB_SHA = '4c159b308b09f0196e2b00c0b03a7a915e1d09779d3593e9c9cfb44ec5c19700'
PINS_SHA = '75877fe766290f50fb5c8c22dd83c1b52f510e5f7fda9163d33752929b5f66aa'
GENERATORS = {
    'prepare-asteroids-gmu-votes.py': 'b0c0296e4fe5248dec8814072d600ab77d49e48f899f36707dbcca740656d973',
    'prepare-asteroids-gmu.py': '7aee11f6dc91b03907fc6972e3a95965b290d893fbe9aaf8dedcd105a9309841',
}
FIRMWARE = {
    'gen80300_gmu.bin': '6adcbf43b162fb4bdc00154824f44c5f96d82a223cfbe7012d5904098f5bf2fc',
    'gen80300_sqe.fw': '046f5aea37fd8ec09af247edb07b9266bfecdbf6220ed5b2b56ed988fc6f5d5f',
    'gen80300_zap.mbn': '9a0a7c63f46f444cff8d73b9de50a0bd47a94e7e7fccaa26007da11e30212888',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked(path, digest):
    if sha(path) != digest:
        raise ValueError(f'{path}: SHA256 mismatch; expected {digest}')


def generate(source, dtb, output):
    if output.exists():
        raise ValueError(f'{output}: output already exists')
    checked(dtb, DTB_SHA)
    pins_path = source / 'scripts/data/a810-gmu-inputs.json'
    checked(pins_path, PINS_SHA)
    pins = json.loads(pins_path.read_text())
    for name, digest in GENERATORS.items():
        checked(source / 'scripts' / name, digest)
    for name, digest in pins['linux']['files'].items():
        checked(source / '.cache/linux-a8xx' / name, digest)
    for name, digest in FIRMWARE.items():
        checked(source / '.cache/firmware' / name, digest)
    # ponytail: pinned scripts call main unconditionally; drop only that AST call.
    # Switch to a normal import when upstream provides an import-safe entry point.
    script = source / 'scripts/prepare-asteroids-gmu-votes.py'
    tree = ast.parse(script.read_text())
    if ast.dump(tree.body[-1]) != ast.dump(ast.parse('main()').body[0]):
        raise ValueError('unexpected generator entry point')
    tree.body.pop()
    ns = {'__file__': str(script)}
    exec(compile(tree, str(script), 'exec'), ns)
    ns['DTB'] = dtb
    ns['PINS']['device_tree'] = {'sha256': DTB_SHA}
    facts = ns['derive']()
    nodes = ns['parse_fdt'](dtb.read_bytes())
    windows = {}
    for node in (ns['GPU'], '/soc/qcom,gmu@3d37000'):
        parent = nodes[node.rsplit('/', 1)[0]]
        if ns['cells'](parent['#address-cells']) != [1] or ns['cells'](parent['#size-cells']) != [1]:
            raise ValueError('unsupported DT register cell geometry')
        names = nodes[node]['reg-names'].rstrip(b'\0').decode().split('\0')
        regs = ns['cells'](nodes[node]['reg'])
        if len(regs) != 2 * len(names):
            raise ValueError('register names/count mismatch')
        windows.update({name: regs[2*i:2*i+2] for i, name in enumerate(names)})
    with tempfile.TemporaryDirectory(prefix='amethyst-votes-', dir=output.parent) as tmp:
        root = Path(tmp)
        (root / 'scripts/data').mkdir(parents=True)
        (root / '.cache').mkdir()
        shutil.copyfile(pins_path, root / 'scripts/data/a810-gmu-inputs.json')
        shutil.copyfile(source / 'scripts/prepare-asteroids-gmu.py', root / 'scripts/prepare-asteroids-gmu.py')
        for name in ('linux-a8xx', 'firmware'):
            (root / '.cache' / name).symlink_to(source / '.cache' / name, target_is_directory=True)
        (root / 'proprietary-files.txt').write_text(''.join(f'{n}|{h}\n' for n, h in FIRMWARE.items()))
        board = '[gpu]\n'
        for key, name in [('gpu', 'kgsl_3d0_reg_memory'), ('gmu', 'gmu'), ('rscc', 'rscc'), ('cx_misc', 'cx_misc')]:
            base, size = windows[name]
            board += f'{key}_base = "{base:#x}"\n{key}_size = "{size:#x}"\n'
        (root / 'board.toml').write_text(board)
        subprocess.run([sys.executable, str(root / 'scripts/prepare-asteroids-gmu.py')], check=True)
        generated = root / 'build/sheng-gmu'
        ns['OUT'] = generated
        for path, text in ns['render'](facts).items():
            path.write_text(text.replace('pinned 3a device tree', 'pinned amethyst device tree').replace('Pinned 3a DT', 'Pinned amethyst DT'))
        report = dict(facts, dtb_path=str(dtb), linux=pins['linux'], generators=GENERATORS,
                      input_pins_sha256=PINS_SHA, windows_from_dtb=windows,
                      selection='first bin in DT order listing the runtime SKU or zero',
                      physical_gpu_revision_observed=False, actual_command_db_available=False,
                      hardware_ready=False)
        (generated / 'votes-source-report.json').write_text(json.dumps(report, indent=2) + '\n')
        # Firmware arrays have their own dedicated generator/manifest; publish only these prerequisites.
        names = ['ShengGmuVotesSource.h', 'ShengGmuCommandDbSource.h', 'ShengGmuSource.h',
                 'source-report.json', 'votes-source-report.json']
        output.mkdir()
        for name in names:
            shutil.copyfile(generated / name, output / name)
        manifest = {'files': {name: {'path': str(output / name), 'sha256': sha(output / name)}
                              for name in names if name.endswith('.h')}}
        (output / 'votes-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('PASS: amethyst DT-derived GMU votes/windows and pinned Linux source headers:', output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args.source.resolve(), args.dtb.resolve(), args.output.resolve())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
