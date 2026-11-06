#!/usr/bin/env python3
"""Run the core source-storage builder with this board's device package and kit name.

Copied behaviour, not code: build-sheng-source-storage.py (UniXNU) searches <device>/package,
<device>/kits/sheng and <tree>/kits/sheng. A resolved build tree links this repository's package/
as kits/<board kit> (board.toml [board] kit = "amethyst"), so the core builder finds neither
BootLog.c nor the source-storage adapters unless it is told where the device is.

The tree links that directory from the device repository, so resolve the device from the tree's
own kits/amethyst link and pass it with --device. Everything else is the core builder's.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The archive recipe runs from the tree with this script copied under <tree>/scripts.
TREE = Path.cwd() if (Path.cwd() / 'kits' / 'amethyst').is_dir() else ROOT
LINK = TREE / 'kits' / 'amethyst'
CORE = TREE / 'scripts' / 'build-sheng-source-storage.py'


def main():
    if not LINK.is_dir():
        raise SystemExit(f'{LINK} not found; the build tree must link this repository as kits/amethyst')
    if not CORE.is_file():
        raise SystemExit(f'{CORE} not found; run through "unixnu build kernel"')
    device = Path(os.environ.get('UNIXNU_DEVICE') or LINK.resolve())
    if not (device / 'package').is_dir():
        raise SystemExit(f'{device}/package not found; is UNIXNU_DEVICE the device repository?')
    command = [sys.executable, str(CORE), '--tree', str(TREE), '--device', str(device), *sys.argv[1:]]
    print('source storage: device', device)
    sys.stdout.flush()
    raise SystemExit(subprocess.run(command, cwd=TREE).returncode)


if __name__ == '__main__':
    main()
