// SPDX-License-Identifier: BSD-2-Clause-Patent
// Goodix BerlinD touch event block, as the vendor berlin driver (goodix_brl_hw.c)
// and Linux goodix_berlin_core.c read it: an 8-byte head (status, -, request,
// -, -, -, u16 LE sum of bytes 0-5), then 8 bytes per contact (id in bits 7:4
// of byte 0, x/y/w as LE16 at +2/+4/+6) and a u16 LE sum over the contacts.
// Status bit 7 = touch event, bit 6 = request event; contact count = byte 2 & 15.
// The event is acknowledged by writing one zero byte at the event address.
// GT9916 places the block at 0x10308; the head checksum is checked before the
// poller writes anything, so a wrong address is never written to.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum {
    GoodixBerlinEventAddress = 0x10308,
    GoodixBerlinEventHead = 8,
    GoodixBerlinCoordBytes = 8,
    GoodixBerlinMaxTouch = 10,
    GoodixBerlinEventBytes = GoodixBerlinEventHead + GoodixBerlinCoordBytes * GoodixBerlinMaxTouch + 2,
    GoodixBerlinWriteFlag = 0xf0,
    GoodixBerlinWritePrefix = 5,
};

struct GoodixBerlinContact { uint8_t id, type; uint16_t x, y, w; };
struct GoodixBerlinEvent {
    uint8_t status, request;
    unsigned count;          // contacts reported (0 = all lifted)
    int headOk, pointsOk;    // checksums
    struct GoodixBerlinContact points[GoodixBerlinMaxTouch];
};

// Sum of n bytes compared with the LE16 that follows them.
static inline int goodix_berlin_sum_ok(const uint8_t *data, size_t n) {
    uint16_t sum = 0;
    for (size_t i = 0; i < n; ++i) sum = (uint16_t)(sum + data[i]);
    return sum == (uint16_t)(data[n] | data[n + 1] << 8);
}

// Returns 1 when the head is valid. Points are filled only for a touch event
// whose contact checksum is valid.
static inline int goodix_berlin_event_parse(const uint8_t *data, size_t bytes,
    struct GoodixBerlinEvent *out) {
    if (!data || !out || bytes < GoodixBerlinEventHead) return 0;
    memset(out, 0, sizeof *out);
    out->status = data[0];
    out->request = data[2];
    out->headOk = goodix_berlin_sum_ok(data, 6);
    if (!out->headOk) return 0;
    if (!(out->status & 0x80)) return 1;
    out->count = data[2] & 0x0f;
    if (out->count > GoodixBerlinMaxTouch) { out->count = 0; return 1; }
    size_t need = GoodixBerlinEventHead + out->count * GoodixBerlinCoordBytes + 2;
    if (need > bytes) return 1;
    out->pointsOk = goodix_berlin_sum_ok(data + GoodixBerlinEventHead,
        out->count * GoodixBerlinCoordBytes);
    if (!out->pointsOk) return 1;
    for (unsigned i = 0; i < out->count; ++i) {
        const uint8_t *c = data + GoodixBerlinEventHead + i * GoodixBerlinCoordBytes;
        out->points[i].id = c[0] >> 4;
        out->points[i].type = c[0] & 0x0f;
        out->points[i].x = (uint16_t)(c[2] | c[3] << 8);
        out->points[i].y = (uint16_t)(c[4] | c[5] << 8);
        out->points[i].w = (uint16_t)(c[6] | c[7] << 8);
    }
    return 1;
}

// F0 + 32-bit big-endian address + payload, no dummy bytes (BerlinD write).
static inline size_t goodix_berlin_write_frame(uint32_t address, const uint8_t *data,
    size_t bytes, uint8_t *tx, size_t capacity) {
    if (!tx || !data || !bytes || capacity < GoodixBerlinWritePrefix ||
        bytes > capacity - GoodixBerlinWritePrefix) return 0;
    tx[0] = GoodixBerlinWriteFlag;
    for (unsigned i = 0; i < 4; ++i) tx[1 + i] = (uint8_t)(address >> (24 - i * 8));
    memcpy(tx + GoodixBerlinWritePrefix, data, bytes);
    return GoodixBerlinWritePrefix + bytes;
}
