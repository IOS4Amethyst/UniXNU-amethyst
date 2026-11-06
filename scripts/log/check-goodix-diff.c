// SPDX-License-Identifier: BSD-2-Clause-Patent
// Host check of GoodixBerlinDiff.h with the hardware-measured shapes (V20): a
// -1400 finger blob held for several frames is a press with a centroid; single-frame
// +-250 spikes and a two-frame -700 spike are not; release hysteresis; drift.
#include <stdio.h>
#include "GoodixBerlinDiff.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

enum { R = 18, C = 40 };
static uint8_t grid[2 * R * C];
static void set(unsigned i, int16_t v) { grid[2 * i] = (uint8_t)v; grid[2 * i + 1] = (uint8_t)((uint16_t)v >> 8); }
static struct GoodixDiffState s;
static int lift(void) { int started = 0; for (unsigned f = 0; f < GoodixDiffReleaseFrames; ++f) started |= goodix_diff_frame(&s, grid); return started; }
static void fill(int16_t base) { for (unsigned i = 0; i < R * C; ++i) set(i, (int16_t)(base + (int16_t)(i % 5))); }
static void blob(unsigned r, unsigned c, int16_t depth) {     // 3x3, deepest at the centre
    for (int dr = -1; dr <= 1; ++dr)
        for (int dc = -1; dc <= 1; ++dc) {
            unsigned i = (r + dr) * C + (c + dc);
            set(i, (int16_t)(10600 + (int16_t)(i % 5) + (dr || dc ? depth / 2 : depth)));
        }
}

int main(void) {
    goodix_diff_reset(&s, R, C);
    fill(10600);
    for (unsigned f = 0; f < GoodixDiffLearnFrames; ++f) CHECK(goodix_diff_frame(&s, grid) == 0);
    CHECK(s.base[0] == 10600 && s.base[3] == 10603);

    set(20, 10600 + 0 - 250); CHECK(goodix_diff_frame(&s, grid) == 0);   // row-0 spike, one frame
    set(24, 10600 + 4 + 256); fill(10600); CHECK(goodix_diff_frame(&s, grid) == 0);
    set(30, 10600 + 0 - 700); CHECK(goodix_diff_frame(&s, grid) == 0);   // -700 for two frames only
    CHECK(goodix_diff_frame(&s, grid) == 0);
    fill(10600); CHECK(goodix_diff_frame(&s, grid) == 0 && s.presses == 0);

    blob(15, 38, -1400);                                                   // finger at r15 c38
    CHECK(goodix_diff_frame(&s, grid) == 0 && goodix_diff_frame(&s, grid) == 0);
    CHECK(goodix_diff_frame(&s, grid) == 1 && s.pressed && s.presses == 1);
    CHECK(s.current.index == 15 * C + 38 && s.current.value <= -1300);   // ~3% tracked away before the hold
    CHECK(s.current.row16 == 15 * 16 && s.current.col16 > 37 * 16 && s.current.col16 < 39 * 16);
    for (unsigned f = 0; f < 50; ++f) goodix_diff_frame(&s, grid);       // held: not absorbed
    CHECK(s.pressed && s.presses == 1 && s.base[15 * C + 38] < 10610);
    fill(10600); CHECK(lift() == 0 && !s.pressed);
    CHECK(s.ring[0].index == 15 * C + 38);

    blob(2, 24, -1450);
    for (unsigned f = 0; f < 3; ++f) goodix_diff_frame(&s, grid);
    CHECK(s.presses == 2 && s.ring[1].row16 == 2 * 16 && s.ring[1].col16 == 24 * 16);
    fill(10600); lift();

    fill(10640);                                                           // a panel-wide shift is
    for (unsigned f = 0; f < 200; ++f) goodix_diff_frame(&s, grid);       // removed line by line
    CHECK(!s.pressed && s.presses == 2 && s.d[0] > -10 && s.d[0] < 10);

    fill(10640); set(3, 10640 + 3 - 2200);                                 // V23: lone row-0 node stuck at -2200
    for (unsigned f = 0; f < 5; ++f) CHECK(goodix_diff_frame(&s, grid) == 0 && !s.pressed);
    for (unsigned f = 0; f < 60; ++f) goodix_diff_frame(&s, grid);
    CHECK(s.base[3] < 10640 - 1800 && s.isolated > 0);                     // absorbed
    blob(9, 20, -1400);                                                    // a real touch still registers
    for (unsigned f = 0; f < 3; ++f) goodix_diff_frame(&s, grid);
    CHECK(s.pressed && s.presses == 3 && s.current.index == 9 * C + 20);
    for (unsigned f = 0; f < GoodixDiffStuckFrames; ++f) goodix_diff_frame(&s, grid);   // held too long
    CHECK(!s.pressed && s.stuck == 1);
    fill(10640); set(3, 10640 + 3 - 2200); lift();                         // lifting after recalibration:
    CHECK(!s.pressed && s.presses == 3);                                   // a rise, never a press
    for (unsigned f = 0; f < 40; ++f) goodix_diff_frame(&s, grid);
    CHECK(s.base[9 * C + 20] < 10640 + 120);                               // recovered after the lift
    blob(9, 20, -1400);                                                    // same spot pressed again
    for (unsigned f = 0; f < 3; ++f) goodix_diff_frame(&s, grid);
    CHECK(s.pressed && s.presses == 4);
    fill(10640); set(3, 10640 + 3 - 2200); lift();
    blob(4, 10, -1400);
    for (unsigned f = 0; f < 3; ++f) goodix_diff_frame(&s, grid);
    CHECK(s.pressed && s.presses == 5);

    fill(10640); for (unsigned f = 0; f < 400; ++f) goodix_diff_frame(&s, grid);   // V24: slow power-on drift
    {
        unsigned before = s.presses;
        for (unsigned f = 0; f < 3000; ++f) {                               // a 5x5 area sinks 0.3 count/frame to -900
            int16_t sink = (int16_t)-(f * 3 / 10 > 900 ? 900 : f * 3 / 10);
            fill(10640);
            for (int dr = -2; dr <= 2; ++dr)
                for (int dc = -2; dc <= 2; ++dc) set((1 + 2 + dr) * C + 20 + dc, (int16_t)(10640 + ((1 + 2 + dr) * C + 20 + dc) % 5 + sink));
            goodix_diff_frame(&s, grid);
        }
        CHECK(s.presses == before && !s.pressed);
        blob(10, 30, -1300);                                                // a real finger on top still registers
        for (unsigned f = 0; f < 3; ++f) goodix_diff_frame(&s, grid);
        CHECK(s.pressed && s.presses == before + 1 && s.current.index == 10 * C + 30);
        for (unsigned f = 0; f < 200; ++f) goodix_diff_frame(&s, grid);   // held 200 frames: not tracked away
        CHECK(s.pressed);
    }

    {                                                                      // V28 line noise:
        unsigned before = s.presses;                                       // whole lines jump +-500,
        for (unsigned f = 0; f < 400; ++f) {                               // tapered, sometimes part-line
            fill(10640);
            for (unsigned r = 0; r < R; ++r) {
                int32_t jump = (int32_t)(((f * 7 + r * 13) % 11) * 100) - 500;
                unsigned split = (f + r) % 5 == 0 ? 16 : C;                 // jump changes part-way
                for (unsigned c = 0; c < C; ++c) {
                    int32_t j = c < split ? jump : -jump / 3;
                    set(r * C + c, (int16_t)(10640 + (r * C + c) % 5 + j * goodix_diff_profile40[c] / 64));
                }
            }
            goodix_diff_frame(&s, grid);
        }
        CHECK(s.presses == before && !s.pressed);
        for (unsigned f = 0; f < 5; ++f) {                                 // a finger in the same noise
            fill(10640);
            for (unsigned r = 0; r < R; ++r)
                for (unsigned c = 0; c < C; ++c)
                    set(r * C + c, (int16_t)(10640 + (r * C + c) % 5 + (int32_t)(((f * 7 + r * 13) % 11) * 100 - 500) * goodix_diff_profile40[c] / 64));
            for (int dr = -1; dr <= 1; ++dr)
                for (int dc = -1; dc <= 1; ++dc) {
                    unsigned i = (unsigned)(6 + dr) * C + (unsigned)(11 + dc);
                    set(i, (int16_t)(goodix_grid_value(grid, i) + (dr || dc ? -700 : -1400)));
                }
            goodix_diff_frame(&s, grid);
        }
        CHECK(s.pressed && s.presses == before + 1 && s.current.index == 6 * C + 11);
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
    }

    {                                                                      // live position follows a swipe
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
        unsigned before = s.presses; int lastY = -1, x = 0, y = 0;
        for (unsigned c = 30; c >= 8; c -= 2) {                            // bottom to top along the 40 axis
            fill(10640); blob(9, c, -1400);
            goodix_diff_frame(&s, grid);
            if (s.pressed) {
                goodix_diff_screen(&s, &s.live, &x, &y);
                CHECK(lastY < 0 || y < lastY); lastY = y;
                CHECK(x > 15240 - 1000 && x < 15240 + 1000);               // row 9 of 18: mid-screen
            }
        }
        CHECK(s.presses == before + 1 && lastY < 20320 * 10 / 40);
        fill(10640); lift(); CHECK(!s.pressed);
        struct GoodixDiffPress corner = {0, 0, 2 * 16, 2 * 16}, far = {0, 0, 17 * 16 + 15, 39 * 16 + 15};
        goodix_diff_screen(&s, &corner, &x, &y); CHECK(x == 4233 && y == 1270);   // V26 top-left tap
        goodix_diff_screen(&s, &far, &x, &y); CHECK(x == 30479 && y == 20319);   // clamped
    }

    {                                                                      // V30: hold after a swipe
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
        unsigned before = s.presses;
        for (unsigned c = 30; c >= 10; --c) { fill(10640); blob(9, c, -1300); goodix_diff_frame(&s, grid); }
        for (unsigned f = 0; f < 600; ++f) goodix_diff_frame(&s, grid);   // held 600 frames at the new spot
        CHECK(s.pressed && s.presses == before + 1 && s.live.index == 9 * C + 10);
        fill(10640); blob(9, 10, -250); goodix_diff_frame(&s, grid);       // one weak frame mid-press:
        CHECK(s.pressed && s.live.index == 9 * C + 10);                    // still down, position kept
        blob(9, 10, -1300); goodix_diff_frame(&s, grid); CHECK(s.pressed && s.presses == before + 1);
        fill(10640); for (unsigned f = 0; f < 2; ++f) goodix_diff_frame(&s, grid);
        CHECK(s.pressed);                                                  // 2 weak frames: not yet up
        goodix_diff_frame(&s, grid); CHECK(!s.pressed);                    // the third releases
        for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
    }

    {                                                                      // V56: a lifting finger never jumps
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
        unsigned before = s.presses;
        for (unsigned c = 38; c >= 28; --c) { fill(10640); blob(9, c, -1200); goodix_diff_frame(&s, grid); }
        CHECK(s.pressed && s.presses == before + 1 && s.live.index == 9 * C + 28);
        for (unsigned f = 0; f < GoodixDiffReleaseFrames; ++f) {           // the finger fades while a weak dip
            fill(10640); blob(9, 28, -250); blob(12, 33, -450); blob(4, 36, -480);   // sits near and far
            goodix_diff_frame(&s, grid);
            CHECK(s.live.index == 9 * C + 28);                             // never followed
        }
        CHECK(!s.pressed && s.presses == before + 1);                      // released instead
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
    }

    {                                                                      // V58: a tap stays put, a swipe follows
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
        unsigned before = s.presses;
        fill(10640); blob(9, 20, -1200);
        for (unsigned f = 0; f < GoodixDiffHold; ++f) goodix_diff_frame(&s, grid);
        CHECK(s.pressed && s.presses == before + 1 && !s.moving);
        const struct GoodixDiffPress down = s.report;
        fill(10640); blob(9, 20, -1200); blob(10, 20, -700); goodix_diff_frame(&s, grid);   // the contact widens a little
        CHECK(s.live.row16 != down.row16 && s.report.row16 == down.row16 && s.report.col16 == down.col16 && !s.moving);
        fill(10640); blob(10, 21, -450); goodix_diff_frame(&s, grid);       // rolling off: shallow, a node away
        CHECK(s.report.row16 == down.row16 && s.report.col16 == down.col16 && !s.moving);
        fill(10640); lift(); CHECK(!s.pressed);
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
        blob(9, 30, -1200); for (unsigned f = 0; f < GoodixDiffHold; ++f) goodix_diff_frame(&s, grid);
        for (unsigned c = 29; c >= 20; --c) { fill(10640); blob(9, c, -1200); goodix_diff_frame(&s, grid); }
        CHECK(s.pressed && s.moving && s.report.col16 == s.live.col16 && s.report.col16 < 21 * 16);   // follows exactly
        fill(10640); lift(); CHECK(!s.pressed);
        fill(10640); for (unsigned f = 0; f < 5; ++f) goodix_diff_frame(&s, grid);
    }

    goodix_diff_reset(&s, 40, 40);                                         // too many nodes
    CHECK(s.nodes == 0 && goodix_diff_frame(&s, grid) == 0);
    if (failures) return 1;
    puts("PASS: Goodix finger blob press (3 frames, centroid), spikes and lone stuck nodes ignored, hysteresis, drift, stuck-press recalibration, slow drift tracked, line noise removed, live position and screen mapping, debounced release, hold after swipe, no jump on lift, tap slop");
    return 0;
}
