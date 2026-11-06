#!/usr/bin/env python3
"""Pull the Amethyst diagnostic log (sheng-log=1) off the phone and decode it.

Why this exists: Amethyst has no usable serial console, so a boot could only be
inspected by photographing the screen. With BootLog.c linked in and sheng-log=1
on the boot command line, the kernel now stores its message buffer into the log
window, and this script reads it back over ADB in LineageOS Recovery.

The window is the last 32 MiB of the *unixnu* partition -- not userdata, so
Android's data is never touched. Unlike asteroids' puller, the partition is
found by name from the recovery shell rather than hardcoded, because this device
keeps its root image in a split partition.

Nothing is written to the phone: the 32 MiB copy goes to /tmp (RAM) and comes
back with `adb pull`.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
DECODER = KIT / 'sheng' / 'read-sheng-log.py'
BLOCK = 4096
SECTORS_PER_BLOCK = 8  # sysfs reports 512-byte sectors; dd works in 4096-byte blocks
LOG_BLOCKS = 8192  # NativeUfs kLogReserveBlocks
PARTITION = 'unixnu'


def adb(*args, check=True):
    """Run adb and return its stdout.

    A failing adb call is reported as the tool's own message rather than a
    CalledProcessError traceback: the usual causes (no device selected, several
    devices attached, recovery not booted) are recoverable by the operator, and
    the traceback hid which one it was.
    """
    result = subprocess.run(['adb', *args], capture_output=True)
    if result.returncode and check:
        detail = (result.stderr or result.stdout).decode(errors='replace').strip()
        message = f'adb {" ".join(args)} failed (exit {result.returncode})'
        if 'more than one device' in detail or 'no devices' in detail:
            message += ': select the phone with ANDROID_SERIAL=<serial>'
        raise SystemExit(f'{message}\n{detail}' if detail else message)
    return result.stdout


def use_serial(serial):
    """Pin adb to one device.

    adb selects a device from -s, else ANDROID_SERIAL, else the only attached
    one. This host often has more than one, so pinning explicitly avoids
    `adb shell` failing with "more than one device/emulator".
    """
    if serial:
        os.environ['ANDROID_SERIAL'] = serial


def partition_bounds(name):
    """First LBA and block count of a partition, in 4096-byte blocks.

    Recovery exposes /dev/block/by-name/<name>, but sysfs has no directory under
    the GPT name: the kernel's entry is the partition node itself (`sda34`).
    That entry reports start and size in **512-byte sectors**, not 4096-byte
    blocks, so both are scaled here -- reading them as blocks would place the
    window eight times too far into the disk.
    """
    device = adb('shell', f'readlink -f /dev/block/by-name/{name}').decode().strip()
    node = device.rsplit('/', 1)[-1]
    if not node:
        sys.exit(f'{name} did not resolve to a device node')
    sysfs = f'/sys/class/block/{node}'
    raw_start = adb('shell', f'cat {sysfs}/start 2>/dev/null').decode().strip()
    raw_size = adb('shell', f'cat {sysfs}/size 2>/dev/null').decode().strip()
    try:
        start_sectors, size_sectors = int(raw_start), int(raw_size)
    except ValueError:
        sys.exit(f'{device} ({node}): could not read sysfs start/size '
                 f'(got start={raw_start!r} size={raw_size!r})')
    # sysfs always reports whole 512-byte sectors, whatever the device's logical
    # sector size, so convert to the 4096-byte blocks dd will use.
    if start_sectors % SECTORS_PER_BLOCK or size_sectors % SECTORS_PER_BLOCK:
        sys.exit(f'{name}: {node} start={start_sectors} size={size_sectors} '
                 f'is not a whole number of 4096-byte blocks')
    return start_sectors // SECTORS_PER_BLOCK, size_sectors // SECTORS_PER_BLOCK, device


def pull_export(log, region, skip, out):
    """Copy the newest complete export (BootLog version 3+) beside log.bin as export.bin.

    The export region is the export_below blocks directly under the log window;
    the header names the blob's first block in it and its length. Read-only, like
    the window itself. Amethyst's touch poller (V20) exports its frame recorder here.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location('read_sheng_log', DECODER)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    h = reader.header(log.read_bytes())
    size, below, first = h.get('export_bytes', 0), h.get('export_below', 0), h.get('export_first', 0)
    if not size or not below or skip is None:
        return None
    if first * BLOCK + size > below * BLOCK:
        sys.exit(f'export header out of range: first={first} bytes={size} region={below} blocks')
    count = (size + BLOCK - 1) // BLOCK
    remote = '/tmp/amethyst-export.bin'
    adb('shell', f'dd if={region} of={remote} bs={BLOCK} skip={skip - below + first} count={count}')
    target = out / 'export.bin'
    adb('pull', remote, str(target))
    adb('shell', f'rm -f {remote}', check=False)
    data = target.read_bytes()
    if len(data) < size:
        sys.exit(f'export short read: {len(data)} of {size} bytes')
    target.write_bytes(data[:size])
    print(f'export: generation {h.get("export_generation")} {size} bytes -> {target}')
    return target


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True, type=Path,
                    help='directory for log.bin and the decoded text')
    ap.add_argument('--partition', default=PARTITION)
    ap.add_argument('--serial', default=None,
                    help='adb serial to read from (else ANDROID_SERIAL, else the '
                         'only attached device)')
    ap.add_argument('--device', default=None,
                    help='block device holding the 32 MiB window at its end, read '
                         'with dd (only for a node that starts at the window; the '
                         'default path uses tail -c on the partition)')
    ap.add_argument('--expect-first', type=int, default=90660608,
                    help='expected first LBA in 4096-byte blocks; guards against '
                         'reading the wrong region (default: unixnu first LBA per '
                         'the partition plan). Pass 0 to disable.')
    args = ap.parse_args()
    use_serial(args.serial)
    args.out.mkdir(parents=True, exist_ok=True)

    if adb('shell', 'id -u').decode().strip() != '0':
        sys.exit('adb shell is not root: enable ADB in LineageOS Recovery')

    if args.device:
        skip = None
        region = args.device
    else:
        start, blocks, device = partition_bounds(args.partition)
        if args.expect_first and start != args.expect_first:
            sys.exit(f'{args.partition} starts at LBA {start}, not the expected '
                     f'{args.expect_first}: this is not the layout the log window '
                     f'was written against. Re-check the GPT before reading '
                     f'(pass --expect-first 0 to override).')
        if blocks < LOG_BLOCKS:
            sys.exit(f'{args.partition} has {blocks} blocks, smaller than the '
                     f'{LOG_BLOCKS}-block log window')
        # The window is the last LOG_BLOCKS blocks of the partition.
        skip = blocks - LOG_BLOCKS
        region = device
        print(f'{args.partition}: {device} start={start} blocks={blocks} '
              f'({blocks * BLOCK / 2**30:.1f} GiB)')
        print(f'log window: LBA {start + skip}..{start + blocks - 1} '
              f'({LOG_BLOCKS * BLOCK // 2**20} MiB)')

    remote = '/tmp/amethyst-log.bin'
    # Offsets on a partition node are relative to that partition, not the disk.
    # Do not use tail: block devices may report a zero file length to it.
    if skip is not None:
        command = f'dd if={region} of={remote} bs={BLOCK} skip={skip} count={LOG_BLOCKS}'
    else:
        command = f'dd if={region} of={remote} bs={BLOCK} count={LOG_BLOCKS}'
    adb('shell', command)
    copied = adb('shell', f'stat -c %s {remote} 2>/dev/null', check=False).decode().strip()
    if copied != str(LOG_BLOCKS * BLOCK):
        sys.exit(f'the copy in {remote} has {copied or "no"} bytes, '
                 f'not {LOG_BLOCKS * BLOCK}')
    log = args.out / 'log.bin'
    adb('pull', remote, str(log))
    adb('shell', f'rm -f {remote}', check=False)
    if log.stat().st_size != LOG_BLOCKS * BLOCK:
        sys.exit(f'short read: {log.stat().st_size} of {LOG_BLOCKS * BLOCK} bytes')

    pull_export(log, region, skip, args.out)
    result = subprocess.run([sys.executable, str(DECODER), '--decode', str(log),
                             '--out', str(args.out)])
    if result.returncode:
        sys.exit(result.returncode)
    latest = args.out / 'latest.txt'
    if latest.exists():
        print(f'\n--- {latest} (last lines) ---')
        print('\n'.join(latest.read_text(errors='replace').splitlines()[-40:]))


if __name__ == '__main__':
    main()
