#!/usr/bin/env python3
"""Embed the amethyst V38–V43 A810 firmware, without fetching or hardware I/O.

Pins: board main 9a9a7cb scripts/log/WORKING-BOOT.md GPU modes 5/6 and
GPU-BRINGUP.md V42; full identities checked against the retained fallback blobs.
The ZAP is Xiaomi-signed, not the Nothing 3a image. This prepares build inputs,
not a D94 runtime qualification or a generated-votes/loader approval.
"""
import argparse
import hashlib
import json
from pathlib import Path

FIRMWARE = {
    'gen80300_gmu.bin': ('ShengGmuFirmware.h', 'sheng_gmu_firmware',
        '6adcbf43b162fb4bdc00154824f44c5f96d82a223cfbe7012d5904098f5bf2fc'),
    'gen80300_sqe.fw': ('ShengSqeFirmware.h', 'sheng_sqe_firmware',
        '046f5aea37fd8ec09af247edb07b9266bfecdbf6220ed5b2b56ed988fc6f5d5f'),
    'gen80300_zap.mbn': ('ShengZapFirmware.h', 'sheng_zap_firmware',
        '9a0a7c63f46f444cff8d73b9de50a0bd47a94e7e7fccaa26007da11e30212888'),
}


def header(name, blob):
    _, symbol, digest = FIRMWARE[name]
    if hashlib.sha256(blob).hexdigest() != digest:
        raise ValueError(name + ': requires retained amethyst firmware SHA256 ' + digest)
    return (f'/* amethyst A810: {name}, SHA256 {digest}. Generated; never commit. */\n'
            '#include <stdint.h>\n'
            f'static const uint8_t {symbol}[{len(blob)}] __attribute__((aligned(16)))={{\n' +
            '\n'.join(','.join(str(b) for b in blob[i:i + 32]) + ','
                      for i in range(0, len(blob), 32)) + '\n};\n')


def prepare(source, output):
    # Validate all three before creating output; do not leave a partial firmware set.
    contents = {name: header(name, (source / name).read_bytes()) for name in FIRMWARE}
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, text in contents.items():
        path = output / FIRMWARE[name][0]
        path.write_text(text)
        for p in (source / name, path):
            files[p.name] = {'path': str(p.resolve()), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    (output / 'firmware-inputs.json').write_text(json.dumps({'files': files}, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory')
    args = parser.parse_args()
    try:
        prepare(args.source, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
