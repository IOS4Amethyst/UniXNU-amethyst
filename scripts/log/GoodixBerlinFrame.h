// SPDX-License-Identifier: BSD-2-Clause-Patent
// Goodix BerlinD host-touch-computing frame, as the amethyst vendor module reads it
// (goodix_core.ko brl_event_handler / brld_get_framedata):
//  - the block at IC_INFO misc.frame_data_addr; byte 0 bit 7 is the sync flag
//    (frame ready); the vendor reads 2500 bytes and clears the flag afterwards by
//    writing one zero byte at the same address (brl_after_event_handler).
//  - head checksum: byte sum of the first frame_head_len-2 bytes, LE16 after them.
//  - frame length: LE16 at bytes 3-4.
//  - the mutual grid starts at head_len + fw_attr_len + fw_log_len + 20, as
//    drv_num * sen_num signed LE16 values. (+8 was read from the vendor module; V20's
//    recording shows the first 6 "nodes" at +8 are constants 1474,10,90,4600,0,0, and
//    V26's five taps at TL/TR/BR/BL/centre land on (2,2) (15,2) (15,37) (2,37) (9,19)
//    of 18x40 only with the extra 12 bytes: the 18 axis is screen x, the 40 axis y.)
// The grid's sign and meaning (raw, diff) are what V17 measures on hardware.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum { GoodixBerlinFrameMax = 2500 };

struct GoodixBerlinFrameLayout { unsigned headLen, attrLen, logLen, drvNum, senNum; };
struct GoodixBerlinFrameStats {
    uint8_t sync;
    unsigned length, nodes;
    int headOk, gridOk;
    int16_t max, min;
    unsigned maxIndex, minIndex;
    uint32_t absSum;
};

static inline size_t goodix_berlin_frame_grid_offset(const struct GoodixBerlinFrameLayout *l) {
    return (size_t)l->headLen + l->attrLen + l->logLen + 20;
}
// Bytes to read for one frame: up to the end of the grid, at most the vendor's 2500.
static inline size_t goodix_berlin_frame_bytes(const struct GoodixBerlinFrameLayout *l) {
    size_t need = goodix_berlin_frame_grid_offset(l) + 2u * l->drvNum * l->senNum;
    if (!l->drvNum || !l->senNum || l->headLen < 2 || need > GoodixBerlinFrameMax) return 0;
    return need;
}
static inline void goodix_berlin_frame_analyze(const uint8_t *data, size_t bytes,
    const struct GoodixBerlinFrameLayout *l, struct GoodixBerlinFrameStats *out) {
    struct GoodixBerlinFrameStats s;
    memset(&s, 0, sizeof s);
    if (data && bytes) {
        s.sync = data[0];
        if (bytes >= 5) s.length = (unsigned)(data[3] | data[4] << 8);
        if (l->headLen >= 2 && l->headLen <= bytes) {
            uint16_t sum = 0;
            for (unsigned i = 0; i < l->headLen - 2; ++i) sum = (uint16_t)(sum + data[i]);
            s.headOk = sum == (uint16_t)(data[l->headLen - 2] | data[l->headLen - 1] << 8);
        }
        size_t need = goodix_berlin_frame_bytes(l);
        if (need && need <= bytes) {
            const uint8_t *grid = data + goodix_berlin_frame_grid_offset(l);
            s.nodes = l->drvNum * l->senNum;
            s.gridOk = 1;
            s.max = INT16_MIN; s.min = INT16_MAX;
            for (unsigned i = 0; i < s.nodes; ++i) {
                int16_t v = (int16_t)(uint16_t)(grid[2 * i] | grid[2 * i + 1] << 8);
                if (v > s.max) { s.max = v; s.maxIndex = i; }
                if (v < s.min) { s.min = v; s.minIndex = i; }
                s.absSum += (uint32_t)(v < 0 ? -(int32_t)v : v);
            }
        }
    }
    *out = s;
}
