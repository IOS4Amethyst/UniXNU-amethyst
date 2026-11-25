#!/usr/bin/env python3
"""Static checks for the iPadOS 17 touch + side-button port into the D94 iOS 27 link (build-gop-legacy-display-27.py)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOUCH = ROOT / 'package/touch'
builder = (ROOT / 'scripts/build-gop-legacy-display-27.py').read_text()
bootlog = (ROOT / 'package/BootLog.c').read_text()
for name in ('AmethystTouchSnapshot.cpp', 'AmethystTouchDma.cpp', 'AmethystTouchLoopbackStage.cpp', 'AmethystTouchGoodixStage.cpp',
             'AmethystTouchEvents.cpp', 'AmethystButtonsHid.cpp', 'AmethystButtons.cpp', 'AmethystTouchLoopback.c',
             'GoodixBerlinGeni.c', 'QcomRpmh.c', 'GeniSpi.c', 'GeniSpi.h', 'NativeHidInput.cpp'):
    assert (TOUCH / name).is_file(), name
    assert name.endswith('.h') or name in builder, f'{name} compiled by the builder'
abi = json.loads((ROOT / 'scripts/hid-device-24A446-d94ap.json').read_text())
assert int(abi['instance_size'], 16) == 0xd0
assert len(abi['slots']) == 207
assert 'getClassSize()!=208' in (TOUCH / 'AmethystButtonsHid.cpp').read_text(), 'IOHIDDevice size matches the D94 fixture'
assert 'input_objects = [hid_late' in builder and 'input_objects = []' not in builder
assert '&amethyst_touch_snapshot) amethyst_touch_snapshot();' in bootlog, 'BootLog round loop starts touch'
assert 'amethyst_buttons_hid_start)amethyst_buttons_hid_start();' in (TOUCH / 'AmethystTouchSnapshot.cpp').read_text()
print('PASS: d94 touch/buttons port wiring')
