#!/usr/bin/env python3
"""Sign and C-verify pinned iPhone15,4 raw OS images using upstream VerifiedRoot tools."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--verified-root', type=Path, required=True)
    parser.add_argument('--library', type=Path, required=True, help='Compiled VerifiedRoot host C/OpenSSL provider')
    parser.add_argument('--private-key', type=Path, required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--os-image', type=Path, required=True, help='Raw disk bytes, not compressed DMG')
    parser.add_argument('--systemos-image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    profile = tomllib.loads(args.profile.read_text())
    if profile['ipsw']['product'] not in ('iPhone15,4', 'iPhone17,2') or profile['ipsw']['build'] != '24A446':
        parser.error('Require a qualified iPhone15,4 or iPhone17,2 24A446 profile')
    # Validate both inputs before creating any policies; never overwrite a prior bundle.
    if args.output.exists():
        parser.error('Output already exists; choose a new directory')
    for name, path in (('os', args.os_image), ('systemos', args.systemos_image)):
        if not path.is_file():
            parser.error(f'{path}: expected a regular raw image')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != profile['root'][name + '_image_sha256']:
            parser.error(f'{name}: raw image differs from profile SHA-256 ({digest})')
    tools = args.verified_root / 'tools'
    for name, path in (('OS', args.os_image), ('SystemOS', args.systemos_image)):
        out = args.output / name
        identity = profile['root'][name.lower() + '_volume_uuid']
        subprocess.run([sys.executable, str(tools / 'manifest.py'), '--image', str(path),
                        '--private-key', str(args.private_key), '--public-key', str(args.public_key),
                        '--volume-uuid', identity, '--generation', '1', '--output', str(out)], check=True)
        subprocess.run([sys.executable, str(tools / 'verify_image.py'), '--library', str(args.library),
                        '--manifest', str(out / 'manifest.bin'), '--trusted-key', str(out / 'trusted-key.bin'),
                        '--image', str(path), '--volume-uuid', identity, '--minimum-generation', '1',
                        '--output', str(out / 'verification.json')], check=True)
        report = json.loads((out / 'verification.json').read_text())
        if not report['real_c_signature_and_block_verification'] or report['image_sha256'] != profile['root'][name.lower() + '_image_sha256']:
            raise SystemExit(f'{name}: verification does not match selected root')
        with (out / 'manifest.bin').open('rb') as stream:
            header = stream.read(544)
        if len(header) != 544:
            raise SystemExit(f'{name}: truncated signed header')
        (out / 'manifest.header.bin').write_bytes(header)
    print('PASS: both pinned iPhone images signed and fully C-verified; runtime unverified')


if __name__ == '__main__':
    main()
