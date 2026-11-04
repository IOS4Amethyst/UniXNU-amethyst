// SPDX-License-Identifier: BSD-2-Clause-Patent
// Baseline-difference touch detection on Goodix BerlinD host-computing frames.
// Measured on hardware (V20 recorder, .scratch/log-results/result-v20/export.bin):
// the grid is raw counts (~10600 per node, 18x40 drv-major); a finger is a blob a
// few nodes wide whose difference from the untouched value is about -1400 and lasts
// for many frames; noise is single-frame +-250 spikes, mostly on row 0.
//  - the first GoodixDiffLearnFrames frames are averaged into the baseline (the
//    screen is assumed untouched while iOS boots);
//  - a press starts when the most negative node difference stays at or below
//    -GoodixDiffPress for GoodixDiffHold consecutive frames, and ends when it rises
//    above -GoodixDiffRelease;
//  - each press records its deepest node and the weighted centroid (1/16 node) of
//    the nodes at or below -GoodixDiffRelease within 2 nodes of it;
//  - nodes far from any press drift into the baseline slowly.
// V23 on hardware: one row-0 node sat at -2200 alone for the whole boot and held a
// "press" open, so real touches never started a new one. Hence:
//  - a node counts only with a 4-neighbour at or below -GoodixDiffRelease (a finger
//    covers several nodes); an isolated dip is absorbed into the baseline quickly;
//  - V24 (nobody touching): 13 presses as raw counts in rows 0-2 drifted ~400 counts
//    per minute after power-on, past the +-GoodixDiffDrift band, so the baseline
//    never caught up. Every node away from a press now follows at d/GoodixDiffTrack
//    per frame: a drift of ~0.2 count/frame lags ~13 counts, while a finger (-600
//    within a frame or two) loses only ~5% of its depth before the 3-frame hold;
//  - a press held GoodixDiffStuckFrames frames is taken as a baseline shift: the
//    baseline is reset to the current frame and the press ends.
// V28 recorder (result-v28/export.bin, nobody touching): from 62 to 95 s (boot UI
// animating) every drive line (18 axis) jumps by up to +-500 per frame as a whole,
// tapering towards both ends of the 40 axis, sometimes changing part-way along a
// line. That was every remaining false press. Hence, per frame:
//  - each line's common shift is removed: m = median over the line of d*64/profile,
//    d -= m*profile/64 (profile: the measured taper, goodix_diff_profile40);
//  - a dip counts only with a neighbour at or below -GoodixDiffSupport in an adjacent line
//    (a finger covers 2-3 nodes both ways; a part-line jump stays in its line).
// On the V28 recording this brings the deepest candidate from -950 to -35.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum {
    GoodixDiffMaxNodes = 1024,
    GoodixDiffLearnFrames = 16,
    GoodixDiffPress = 600,
    GoodixDiffRelease = 300,
    GoodixDiffHold = 3,
    GoodixDiffDrift = 60,
    GoodixDiffRing = 6,
    GoodixDiffStuckFrames = 1500,   // ~30-45 s at the measured 32-58 fps
    GoodixDiffTrack = 64,           // baseline follows any slow change at d/64 per frame
    GoodixDiffReleaseFrames = 3,    // V30 swipes: a press ends only after 3 frames above -release
    GoodixDiffShort = 6,            // presses shorter than this many frames are counted (split swipes)
    GoodixDiffSupport = 150,        // adjacent-line neighbour depth that backs a dip (an edge
                                    // tap in V20: -1124 on the edge line, -200..-245 next to it)
};

struct GoodixDiffPress { uint16_t index; int16_t value; uint16_t row16, col16; };
struct GoodixDiffState {
    unsigned nodes, rows, cols, learned, frames, active, presses, candidate, held, stuck, isolated, weak, shorts;
    int32_t sum[GoodixDiffMaxNodes];
    int32_t base[GoodixDiffMaxNodes];
    int32_t d[GoodixDiffMaxNodes];              // this frame's difference, line shift removed
    int pressed;
    int16_t peak; uint16_t peakIndex;           // this frame's most negative difference
    int16_t extreme; uint16_t extremeIndex;     // most negative ever
    struct GoodixDiffPress current;             // the deepest point of the current press
    struct GoodixDiffPress live;                // this frame's position while pressed (follows a moving finger)
    struct GoodixDiffPress ring[GoodixDiffRing];// last presses, newest at (presses-1) % ring
};

static inline void goodix_diff_reset(struct GoodixDiffState *s, unsigned rows, unsigned cols) {
    memset(s, 0, sizeof *s);
    if (rows * cols <= GoodixDiffMaxNodes) { s->rows = rows; s->cols = cols; s->nodes = rows * cols; }
}
static inline int16_t goodix_grid_value(const uint8_t *grid, unsigned i) {
    return (int16_t)(uint16_t)(grid[2 * i] | grid[2 * i + 1] << 8);
}
static inline int32_t goodix_abs32(int32_t v) { return v < 0 ? -v : v; }

// V28's line-noise taper along the 40 axis, x64 (1.0 = 64).
static const uint8_t goodix_diff_profile40[40] = {
    50, 54, 54, 57, 58, 57, 60, 61, 62, 61, 63, 61, 64, 64, 64, 64, 62, 63, 61, 62,
    63, 63, 62, 61, 60, 59, 58, 57, 55, 53, 52, 50, 49, 46, 46, 44, 42, 41, 39, 37};
static inline int32_t goodix_diff_weight(const struct GoodixDiffState *s, unsigned c) {
    return s->cols == 40 ? goodix_diff_profile40[c] : 64;
}
// s->d = grid - base with each line's common shift removed.
static inline void goodix_diff_compute(struct GoodixDiffState *s, const uint8_t *grid) {
    int32_t q[GoodixDiffMaxNodes < 64 ? GoodixDiffMaxNodes : 64];
    for (unsigned r = 0; r < s->rows; ++r) {
        int32_t *d = s->d + r * s->cols;
        const unsigned n = s->cols < 64 ? s->cols : 64;
        for (unsigned c = 0; c < s->cols; ++c) d[c] = goodix_grid_value(grid, r * s->cols + c) - s->base[r * s->cols + c];
        for (unsigned c = 0; c < n; ++c) {               // insertion sort of d*64/w
            int32_t v = d[c] * 64 / goodix_diff_weight(s, c);
            unsigned k = c;
            while (k && q[k - 1] > v) { q[k] = q[k - 1]; --k; }
            q[k] = v;
        }
        const int32_t m = n ? (n & 1 ? q[n / 2] : (q[n / 2 - 1] + q[n / 2]) / 2) : 0;
        for (unsigned c = 0; c < s->cols; ++c) d[c] -= m * goodix_diff_weight(s, c) / 64;
    }
}

// Weighted centroid (1/16 node) of the nodes at or below -release around `at`.
static inline void goodix_diff_centroid(const struct GoodixDiffState *s, const uint8_t *grid,
    unsigned at, struct GoodixDiffPress *p) {
    (void)grid;
    const int r0 = (int)(at / s->cols), c0 = (int)(at % s->cols);
    int64_t w = 0, wr = 0, wc = 0;
    for (int r = r0 - 2; r <= r0 + 2; ++r) {
        if (r < 0 || r >= (int)s->rows) continue;
        for (int c = c0 - 2; c <= c0 + 2; ++c) {
            if (c < 0 || c >= (int)s->cols) continue;
            const unsigned i = (unsigned)r * s->cols + (unsigned)c;
            int32_t d = s->d[i];
            if (d > -GoodixDiffRelease) continue;
            w += -d; wr += (int64_t)-d * r; wc += (int64_t)-d * c;
        }
    }
    p->row16 = (uint16_t)(w ? wr * 16 / w : r0 * 16);
    p->col16 = (uint16_t)(w ? wc * 16 / w : c0 * 16);
}

static inline int32_t goodix_diff_at(const struct GoodixDiffState *s, const uint8_t *grid, unsigned i) {
    (void)grid;
    return s->d[i];
}
// A dip is backed by a finger-sized blob: a node at or below -GoodixDiffSupport in an
// adjacent line (rows r-1, r+1; columns c-1..c+1).
static inline int goodix_diff_supported(const struct GoodixDiffState *s, const uint8_t *grid, unsigned i) {
    const int r = (int)(i / s->cols), c = (int)(i % s->cols);
    for (int rr = r - 1; rr <= r + 1; rr += 2) {
        if (rr < 0 || rr >= (int)s->rows) continue;
        for (int cc = c - 1; cc <= c + 1; ++cc)
            if (cc >= 0 && cc < (int)s->cols && goodix_diff_at(s, grid, (unsigned)rr * s->cols + (unsigned)cc) <= -GoodixDiffSupport)
                return 1;
    }
    return 0;
}

// Returns 1 when this frame started a new press.
static inline int goodix_diff_frame(struct GoodixDiffState *s, const uint8_t *grid) {
    if (!s->nodes || !grid) return 0;
    ++s->frames;
    if (s->learned < GoodixDiffLearnFrames) {
        for (unsigned i = 0; i < s->nodes; ++i) s->sum[i] += goodix_grid_value(grid, i);
        if (++s->learned == GoodixDiffLearnFrames)
            for (unsigned i = 0; i < s->nodes; ++i) s->base[i] = s->sum[i] / GoodixDiffLearnFrames;
        return 0;
    }
    goodix_diff_compute(s, grid);
    int32_t low = 0; unsigned at = 0, lone = 0;
    for (unsigned i = 0; i < s->nodes; ++i) {
        int32_t d = goodix_diff_at(s, grid, i);
        if (d < low && d <= -GoodixDiffRelease && !goodix_diff_supported(s, grid, i)) {
            lone = 1;                                       // lone dip: not a finger
            continue;
        }
        if (d < low) { low = d; at = i; }
    }
    s->isolated += lone;                                    // frames that had a lone dip
    if (low < INT16_MIN) low = INT16_MIN;
    s->peak = (int16_t)low; s->peakIndex = (uint16_t)at;
    if (low < s->extreme) { s->extreme = (int16_t)low; s->extremeIndex = (uint16_t)at; }
    int started = 0;
    if (!s->pressed) {
        s->candidate = low <= -GoodixDiffPress ? s->candidate + 1 : 0;
        if (s->candidate >= GoodixDiffHold) {
            s->pressed = 1; started = 1; ++s->presses; s->candidate = 0;
            s->current.index = (uint16_t)at; s->current.value = (int16_t)low;
            goodix_diff_centroid(s, grid, at, &s->current);
        }
    } else if (low > -GoodixDiffRelease) {
        if (++s->weak >= GoodixDiffReleaseFrames) {       // debounced: a moving finger dips briefly
            if (s->held < GoodixDiffShort) ++s->shorts;
            s->pressed = 0; s->weak = 0;
        }
    } else if (s->weak = 0, low < s->current.value) {
        s->current.index = (uint16_t)at; s->current.value = (int16_t)low;
        goodix_diff_centroid(s, grid, at, &s->current);
    }
    if (s->pressed && ++s->held >= GoodixDiffStuckFrames) {
        for (unsigned i = 0; i < s->nodes; ++i) s->base[i] = goodix_grid_value(grid, i);
        s->pressed = 0; s->held = 0; ++s->stuck;
        return started;
    }
    if (!s->pressed) s->held = 0;
    if (s->pressed && !s->weak) {                         // a weak frame keeps the last position
        s->live.index = (uint16_t)at; s->live.value = (int16_t)low;
        goodix_diff_centroid(s, grid, at, &s->live);
    }
    if (s->pressed) {
        ++s->active;
        s->ring[(s->presses - 1) % GoodixDiffRing] = s->current;
    }
    // Slow drift keeps temperature/power-on settling out of the baseline; nodes under
    // the finger (within 2 of its live position, which follows a swipe) are frozen. V30
    // froze only the press's starting point, so a finger held still after moving was
    // tracked into the baseline within about a second and the touch faded out.
    const int pr = s->pressed ? (int)(s->live.index / s->cols) : -100;
    const int pc = s->pressed ? (int)(s->live.index % s->cols) : -100;
    for (unsigned i = 0; i < s->nodes; ++i) {
        int32_t d = s->d[i];
        const int r = (int)(i / s->cols), c = (int)(i % s->cols);
        if (r >= pr - 2 && r <= pr + 2 && c >= pc - 2 && c <= pc + 2) continue;
        if (goodix_abs32(d) < GoodixDiffDrift && d) {
            int32_t step = d / 32;
            s->base[i] += step ? step : (d > 0 ? 1 : -1);   // integer division alone stalls below 32
        } else if (d <= -GoodixDiffRelease && !goodix_diff_supported(s, grid, i)) {
            s->base[i] += d / 8;                            // isolated dip: absorb within ~30 frames
        } else if (d >= GoodixDiffRelease) {
            s->base[i] += d / 4;                            // a finger only lowers counts: stale baseline
        } else {
            s->base[i] += d / GoodixDiffTrack;              // slow settling (|d| >= 60, so step >= 1)
        }
    }
    return started;
}

// Screen position of a press in TouchState units (NativeHidInput: x 0..30480 across,
// y 0..20320 down, portrait). V26/V27 taps: the rows axis (18) is screen x from the
// left, the columns axis (40) screen y from the top; node n covers [n, n+1) of the axis.
enum { GoodixTouchStateW = 30480, GoodixTouchStateH = 20320 };
static inline void goodix_diff_screen(const struct GoodixDiffState *s, const struct GoodixDiffPress *p,
    int *x, int *y) {
    int32_t px = s->rows ? (int32_t)(((int64_t)p->row16 + 8) * GoodixTouchStateW / (s->rows * 16)) : 0;
    int32_t py = s->cols ? (int32_t)(((int64_t)p->col16 + 8) * GoodixTouchStateH / (s->cols * 16)) : 0;
    *x = px < 0 ? 0 : px >= GoodixTouchStateW ? GoodixTouchStateW - 1 : px;
    *y = py < 0 ? 0 : py >= GoodixTouchStateH ? GoodixTouchStateH - 1 : py;
}
