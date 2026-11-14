#!/usr/bin/env python3
"""Apply the board patch and compile its actual conversion body; no kernel build.

Usage: python3 scripts/test-microuptime-precision.py /path/to/xnu-source
Requires a host C compiler (CC, default cc); uses only Python's standard library.
"""
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile


def main():
    source = Path(sys.argv[1]).resolve()
    patch = Path(__file__).resolve().parents[1] / "patches/xnu/native-microuptime-precision.patch"
    original = (source / "osfmk/arm/rtclock.c").read_text()
    assert re.search(r"uint32_t\s+rtc_sec_divisor;", (source / "osfmk/arm/rtclock.h").read_text())
    old = lambda t: (t // 19_200_000) * 1_000_000 + (t % 19_200_000) // 19
    boundary = 120 * 19_200_000
    assert old(boundary) - old(boundary - 1) == -10_526

    rng = random.Random(19200000)
    cases = []
    for frequency in (1, 999_999, 1_000_000, 19_200_000, 24_000_000, 100_000_000, 0xffffffff):
        # Include full uint64 tick range, not a potentially overflowing ns total.
        ticks = {0, 1, frequency - 1, frequency, frequency + 1, (1 << 64) - 1}
        for seconds in (1, 120, 1_000_000, ((1 << 64) - 1) // frequency):
            ticks.update(seconds * frequency + offset for offset in range(-32, 33))
        ticks.update(rng.randrange(1 << 64) for _ in range(1000))
        cases.extend((frequency, t) for t in sorted(ticks) if 0 <= t < 1 << 64)

    with tempfile.TemporaryDirectory(prefix="microuptime-") as directory:
        root = Path(directory)
        target = root / "osfmk/arm/rtclock.c"
        target.parent.mkdir(parents=True)
        target.write_text(original)
        for args in (("--check",), ()):
            subprocess.run(["git", "apply", *args, str(patch)], cwd=root, check=True)
        text = target.read_text()
        body = re.search(r"void\nabsolutetime_to_microtime\([^}]+^}", text, re.M).group()
        harness = '''#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
typedef unsigned long clock_sec_t;
typedef unsigned int clock_usec_t;
_Static_assert(sizeof(clock_sec_t) == 8 && sizeof(clock_usec_t) == 4, "arm64 clock ABI");
#define USEC_PER_SEC 1000000
static uint32_t rtclock_sec_divisor;
''' + body + '''
int main(void) {
    uint64_t ticks;
    while (scanf("%" SCNu32 " %" SCNu64, &rtclock_sec_divisor, &ticks) == 2) {
        clock_sec_t seconds; clock_usec_t usecs;
        absolutetime_to_microtime(ticks, &seconds, &usecs);
        printf("%lu %u\\n", seconds, usecs);
    }
    return ferror(stdin) ? 1 : 0;
}
'''
        (root / "check.c").write_text(harness)
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-fsanitize=undefined", "-fno-sanitize-recover=all", str(root / "check.c"),
                        "-o", str(root / "check")], check=True)
        result = subprocess.run([str(root / "check")], input="".join(f"{f} {t}\n" for f, t in cases),
                                text=True, capture_output=True, check=True)
        rows = result.stdout.splitlines()
        assert len(rows) == len(cases)
        previous = {}
        for (frequency, ticks), row in zip(cases, rows):
            seconds, usecs = map(int, row.split())
            expected_seconds, remainder = divmod(ticks, frequency)
            assert (seconds, usecs) == (expected_seconds, remainder * 1_000_000 // frequency)
            assert 0 <= usecs < 1_000_000
            flattened = seconds * 1_000_000 + usecs
            assert flattened >= previous.get(frequency, 0)
            previous[frequency] = flattened
    print(f"PASS: git apply --check/apply; compiled conversion + UBSan; {len(cases)} cases, "
          "7 frequencies, uint64 range, normalized and nondecreasing; old backstep = -10526 us")


if __name__ == "__main__":
    main()
