// SPDX-License-Identifier: BSD-2-Clause-Patent
// Host check of GoodixBerlinFrame.h: layout, head checksum, grid extremes and bounds.
#include <stdio.h>
#include <string.h>
#include "GoodixBerlinFrame.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

int main(void) {
    struct GoodixBerlinFrameLayout l = {16, 32, 4, 36, 16};
    CHECK(goodix_berlin_frame_grid_offset(&l) == 72);
    CHECK(goodix_berlin_frame_bytes(&l) == 72 + 2 * 576);
    static uint8_t f[GoodixBerlinFrameMax];
    memset(f, 0, sizeof f);
    f[0] = 0x80; f[3] = 0x34; f[4] = 0x12;
    uint16_t sum = 0;
    for (unsigned i = 0; i < 14; ++i) sum = (uint16_t)(sum + f[i]);
    f[14] = (uint8_t)sum; f[15] = (uint8_t)(sum >> 8);
    uint8_t *g = f + 72;                                  // grid after 12 bytes past attr+log+8 (V26 taps)
    g[2 * 100] = 0x2c; g[2 * 100 + 1] = 0x01;               // +300 at node 100
    g[2 * 7] = 0x9c; g[2 * 7 + 1] = 0xff;                   // -100 at node 7
    struct GoodixBerlinFrameStats s;
    goodix_berlin_frame_analyze(f, goodix_berlin_frame_bytes(&l), &l, &s);
    CHECK(s.sync == 0x80 && s.length == 0x1234 && s.headOk && s.gridOk && s.nodes == 576);
    CHECK(s.max == 300 && s.maxIndex == 100 && s.min == -100 && s.minIndex == 7 && s.absSum == 400);
    f[1] = 1;
    goodix_berlin_frame_analyze(f, sizeof f, &l, &s);
    CHECK(!s.headOk && s.gridOk);
    goodix_berlin_frame_analyze(f, 100, &l, &s);              // short read: no grid
    CHECK(!s.gridOk && s.nodes == 0);
    struct GoodixBerlinFrameLayout big = {16, 2000, 0, 36, 16}; // past 2500 bytes
    CHECK(goodix_berlin_frame_bytes(&big) == 0);
    struct GoodixBerlinFrameLayout none = {16, 0, 0, 0, 16};
    CHECK(goodix_berlin_frame_bytes(&none) == 0);
    if (failures) return 1;
    puts("PASS: Goodix frame layout, head checksum, grid extremes, bounds");
    return 0;
}
