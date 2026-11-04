#!/usr/bin/env python3
"""Study the Goodix host-computing frames the V20 recorder exported (export.bin).

Blob (AmethystTouchEvents.cpp ringExport): "AMTFRM01", u32 drv, sen, entry bytes,
count, newest sequence, frames/s*100, 0; then entries oldest first: u32 ms, u32
sequence, drv*sen LE16 values.

Prints the frame rate and sequence gaps, the per-node noise of the quietest second,
and every interval where one area of the grid departs from the per-node median:
its time, strongest node (as index, drv-major row/col and sen-major row/col), sign
and size. With --dump N it prints the difference map of frame N as a grid.
"""
import argparse
import struct
import sys
from pathlib import Path


def load(path):
    blob = Path(path).read_bytes()
    magic, drv, sen, entry, count, newest, rate, _ = struct.unpack_from('<8s7I', blob)
    if magic != b'AMTFRM01':
        sys.exit(f'{path}: not a frame export ({magic!r})')
    nodes = drv * sen
    if entry != 8 + 2 * nodes or len(blob) != 36 + count * entry:
        sys.exit(f'{path}: size mismatch (entry {entry}, count {count}, {len(blob)} bytes)')
    frames = []
    for i in range(count):
        at = 36 + i * entry
        ms, seq = struct.unpack_from('<II', blob, at)
        frames.append((ms, seq, struct.unpack_from(f'<{nodes}h', blob, at + 8)))
    return drv, sen, rate / 100, frames


def median(values):
    s = sorted(values)
    return s[len(s) // 2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('export')
    ap.add_argument('--threshold', type=int, default=0, help='press threshold (default: 6x the quiet noise)')
    ap.add_argument('--dump', type=int, action='append', default=[], help='print the difference map of frame N')
    a = ap.parse_args()
    drv, sen, rate, frames = load(a.export)
    nodes = drv * sen
    gaps = sum(1 for p, q in zip(frames, frames[1:]) if q[1] != p[1] + 1)
    span = (frames[-1][0] - frames[0][0]) / 1000
    print(f'{len(frames)} frames, grid {drv}x{sen}, {span:.1f} s, {len(frames) / span if span else 0:.1f} fps '
          f'(device average {rate:.1f}), sequence gaps {gaps}')
    base = [median(f[2][i] for f in frames) for i in range(nodes)]
    diffs = [[f[2][i] - base[i] for i in range(nodes)] for f in frames]
    peak = [max(range(nodes), key=lambda i: abs(d[i])) for d in diffs]
    strength = [abs(d[p]) for d, p in zip(diffs, peak)]
    window = max(1, int(len(frames) / span)) if span else 1
    quiet = min(range(0, max(1, len(frames) - window)), key=lambda s: sum(strength[s:s + window]))
    noise = sorted(strength[quiet:quiet + window])[len(strength[quiet:quiet + window]) // 2]
    threshold = a.threshold or max(60, 6 * noise)
    print(f'baseline: per-node median; raw {min(base)}..{max(base)}; quiet second at frame {quiet}: '
          f'median peak |diff| {noise}; press threshold {threshold}')
    pressed, start, best = False, 0, None
    for n, (d, p, s) in enumerate(zip(diffs, peak, strength)):
        if not pressed and s >= threshold:
            pressed, start, best = True, n, (s, p, d[p])
        elif pressed and s >= threshold and s > best[0]:
            best = (s, p, d[p])
        if pressed and (s < threshold // 2 or n == len(diffs) - 1):
            s0, p0, v0 = best
            print(f'press t={frames[start][0] / 1000:7.2f}s..{frames[n][0] / 1000:7.2f}s frames {start}-{n} '
                  f'node {p0} (drv-major r{p0 // sen} c{p0 % sen}, sen-major r{p0 // drv} c{p0 % drv}) diff {v0:+d}')
            pressed = False
    for n in a.dump:
        d = diffs[n]
        print(f'frame {n} t={frames[n][0] / 1000:.2f}s, rows = {drv} (drv-major), columns = {sen}')
        for r in range(drv):
            print(' '.join(f'{d[r * sen + c]:5d}' for c in range(sen)))


if __name__ == '__main__':
    main()
