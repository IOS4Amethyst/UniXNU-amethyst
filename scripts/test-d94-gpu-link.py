#!/usr/bin/env python3
"""Runnable preparation regression: optional --qualification checks real archive bytes."""
import argparse
import importlib.util
from pathlib import Path
import shutil
import tempfile
import tomllib
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('link', ROOT / 'scripts/prepare-d94-gpu-link.py')
link = importlib.util.module_from_spec(spec)
spec.loader.exec_module(link)
p = argparse.ArgumentParser()
p.add_argument('--qualification', type=Path)
a = p.parse_args()
if a.qualification:
    link.validate(a.qualification)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    core, device = root / 'core', root / 'device'
    for base, name in [(device, 'board.toml'), (device, 'configs/amethyst_defconfig'),
                       (device, 'configs/ios27_data_defconfig'),
                       (device, 'patches/xnu/series'),
                       (core, 'profiles/os/ios-27.0.1-24A446-d94ap/profile.toml')]:
        target = base / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    board = device / 'board.toml'
    board.write_text(board.read_text().replace('  "component:AppleKeyStoreState",',
                     '  "component:AppleKeyStoreState",\n  "build-gop-legacy-display-27.py",\n  "build-fileset-release-reference.py --tree . --lto",'))
    original_config = (device / 'configs/amethyst_defconfig').read_text()
    original_data = (device / 'configs/ios27_data_defconfig').read_bytes()
    original_board = tomllib.loads(board.read_text())
    series = device / 'patches/xnu/series'
    series.write_text(series.read_text() + '\nnative-d94-release-display.patch\n')
    script = core / 'scripts/build-xnu-4k-baseline.sh'
    script.parent.mkdir()
    script.write_text('EXTRA_CFLAGS="$EXTRA_CFLAGS -DXNU_BOOT_PROGRESS=$BOOT_PROGRESS"\n')
    args = SimpleNamespace(core=core, device=device, inputs=root / 'inputs', encrypted=root / 'encrypted')
    # Only transport is mocked; preparation operates on real board/config/profile files.
    with patch.object(link, 'sha', return_value=link.ENCRYPTED), patch.object(link, 'validate'), \
         patch.object(link.subprocess, 'run'), patch.object(link.tarfile, 'open'):
        link.prepare(args)
    b = tomllib.loads(board.read_text())
    assert any('prepare-d94-gpu-link.py stage' in x for x in b['kernel']['archives'])
    archives = b['kernel']['archives']
    stage = next(x for x in archives if 'prepare-d94-gpu-link.py stage' in x)
    assert archives.index('build-gop-legacy-display-27.py') < archives.index(stage)
    assert b['package'] == original_board['package']
    assert b['payload'] == original_board['payload']
    assert 'sheng-gpu-power=1' not in b['payload']['kernel_args']
    assert '-DXNU_NATIVE_AMETHYST_GPU=1' in script.read_text()
    assert series.read_text().index('native-d94-release-display.patch') < series.read_text().index('native-d94-release-gpu.patch')
    assert '--allow-missing' not in (core / 'profiles/os/ios-27.0.1-24A446-d94ap/profile.toml').read_text()
    config = (device / 'configs/amethyst_defconfig').read_text()
    assert config.count('-Wl,-force_load,') == 2 and link.NAME in config
    assert config == original_config.replace('CONFIG_XNU_NATIVE_LEGACY_DISPLAY=n\n',
                    'CONFIG_XNU_NATIVE_LEGACY_DISPLAY=y\n').replace('CONFIG_XNU_EXTRA_LD_ARCHIVES="',
                    'CONFIG_XNU_EXTRA_LD_ARCHIVES="-Wl,-force_load,{tree}/build/d94-gpu/' + link.NAME + ' ')
    assert (device / 'configs/ios27_data_defconfig').read_bytes() == original_data
    try:
        link.verify(SimpleNamespace(core=core))
    except ValueError as error:
        assert 'expected one retained' in str(error)
    else:
        raise AssertionError('missing output accepted')
print('PASS: GOP before GPU stage, display enabled, exact config/storage/Data/payload preservation, strict prelink, absent output rejected')
