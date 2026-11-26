#!/usr/bin/env python3
"""Which stage of the D94 GPU display path is late, from one current-boot decoded slots.txt.

Joins the presenter's swap timeline (W rows: validated -> offered to the GPU copy -> retired after it) across slots by swap number, never
by printed seconds (the W clock and the slot clock differ). For every slot interval it reports the swaps accepted, their validate-to-retire
latency, and how long ring fences stayed at the head ([gpu pending] deltas). With the a810-slow-job-trace overlay it also summarizes the
[gpu slow job] records by client and IB shape. All numbers are sampled telemetry: none is a visible frame or a panel refresh.

usage: analyze-swap-stages.py slots.txt [--slots]
"""
import re
import sys
from collections import defaultdict

W_ROW = re.compile(r'W(\d+) t=\S+ \+(\d+)ms offer=(-?\d+)ms done=(-?\d+)ms')
SLOW_JOB = re.compile(r'\[gpu slow job\] #(\d+) f=(\d+) (\S+) head=(\d+)ms sub=(\d+)ms ibs=(\d+) dw=(\d+) st=([0-9a-f]+)')


def parse(path):
    swaps, slots, jobs, cur = {}, [], {}, None
    with open(path, errors='replace') as f:
        for line in f:
            m = W_ROW.match(line)
            if m:
                swaps[int(m[1])] = (int(m[2]), int(m[3]), int(m[4]))
                continue
            m = re.match(r'#slot (\d+) t=(\d+)s', line)
            if m:
                cur = {'t': int(m[2])}
                slots.append(cur)
                continue
            if cur is None:
                continue
            m = re.match(r'\[swap stats\] validated (\d+)', line)
            if m:
                cur['validated'] = int(m[1])
            m = re.match(r'\[gpu pending\] (.*)', line)
            if m:
                cur['pending'] = [int(x.split('=')[1]) for x in m[1].split()]
            m = SLOW_JOB.match(line)
            if m:
                jobs[int(m[1])] = (int(m[2]), m[3], int(m[4]), int(m[5]), int(m[6]), int(m[7]), int(m[8], 16))
    return swaps, slots, jobs


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * q))] if values else 0


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    swaps, slots, jobs = parse(argv[1])
    show = '--slots' in argv
    classes = {'slow': [0] * 4, 'fast': [0] * 4}
    totals = {'slow': 0, 'fast': 0}
    previous = None
    if show:
        print(' t(s) swaps done_p50 done_max | fences <4ms 4-16 16-64 >=64')
    for slot in slots:
        if 'validated' not in slot or 'pending' not in slot:
            continue
        if previous:
            first, last = previous['validated'], slot['validated']
            done = [swaps[k][2] for k in range(first + 1, last + 1) if k in swaps]
            delta = [b - a for a, b in zip(previous['pending'], slot['pending'])]
            buckets = [sum(delta[:3]), sum(delta[3:5]), sum(delta[5:7]), delta[7]]
            if last > first and done:
                if show:
                    print(f"{slot['t']:5d} {last - first:5d} {pct(done, .5):8d} {max(done):8d} | "
                          f"{buckets[0]:11d} {buckets[1]:4d} {buckets[2]:5d} {buckets[3]:4d}")
                kind = 'slow' if pct(done, .5) >= 40 else 'fast' if pct(done, .5) <= 12 else None
                if kind and last - first >= 10:
                    classes[kind] = [x + y for x, y in zip(classes[kind], buckets)]
                    totals[kind] += last - first
        previous = slot
    print(f'swaps joined: {len(swaps)} (W rows keep the last 40 per slot; gaps mean a slot was missed)')
    if swaps:
        done = [v[2] for v in swaps.values() if v[2] >= 0]
        print(f'validate->retire ms: p50 {pct(done, .5)} p90 {pct(done, .9)} p99 {pct(done, .99)} max {max(done)}')
    for kind in ('slow', 'fast'):
        c = classes[kind]
        n = sum(c) or 1
        print(f'{kind} slots (median done {">=40" if kind == "slow" else "<=12"} ms): swaps {totals[kind]}, head fences '
              f'<4ms {100 * c[0] // n}% 4-16 {100 * c[1] // n}% 16-64 {100 * c[2] // n}% >=64 {100 * c[3] // n}% (n={sum(c)})')
    if jobs:
        print(f'[gpu slow job] records joined: {len(jobs)} of {max(jobs)} recorded (eight printed per slot)')
        by = defaultdict(list)
        for fence, client, head, sub, ibs, dwords, stage in jobs.values():
            by[(client, 'single IB' if ibs == 1 else 'multi IB')].append((head, sub, dwords, stage))
        for (client, shape), rows in sorted(by.items(), key=lambda kv: -len(kv[1])):
            heads = [r[0] for r in rows]
            queued = [r[1] - r[0] for r in rows if r[1]]
            kicked = sum(1 for r in rows if r[3] & 0x7f)
            print(f'  {client:20s} {shape:9s} n={len(rows):4d} head p50 {pct(heads, .5)} p90 {pct(heads, .9)} ms; '
                  f'submit-head p50 {pct(queued, .5)} ms; IB dwords p50 {pct([r[2] for r in rows], .5)}; stall stage run {kicked}')
    else:
        print('no [gpu slow job] rows (image without the a810-slow-job-trace overlay, or no job stayed 16 ms at the head)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
