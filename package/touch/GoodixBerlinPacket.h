// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// Normal coordinate mode only, not Xiaomi THP/raw frames or stylus packets.
// Source evidence: goodix_brl_hw.c goodix_touch_handler/goodix_parse_finger,
// goodix_ts_utils.c CHECKSUM_MODE_U8_LE. Coordinates stay in panel pixels;
// Android's reporting layer applies its separate super-resolution factor100.
struct GoodixBerlinPoint { uint8_t id; uint16_t x, y, width; };
struct GoodixBerlinFrame { unsigned count; struct GoodixBerlinPoint points[10]; };

static inline uint16_t goodix_berlin_le16(const uint8_t *p) {
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}
static inline int goodix_berlin_checksum(const uint8_t *p, size_t bytes) {
    if (bytes < 2) return 0;
    uint32_t sum=0;
    for (size_t i=0;i<bytes-2;++i) sum+=p[i];
    return (uint16_t)sum == goodix_berlin_le16(p+bytes-2);
}
// Invalid input leaves the caller's last valid frame untouched. A valid empty
// frame has count0 and means release all contacts, not a failed transaction.
static inline int goodix_berlin_decode(const uint8_t *packet, size_t bytes,
    struct GoodixBerlinFrame *out) {
    if (!packet || !out || bytes<8 || !goodix_berlin_checksum(packet,8) ||
        !(packet[0]&0x80) || (packet[0]&0x60)) return 0;
    unsigned count=packet[2]&15;
    if (count>10 || bytes<8+count*8+(count?2:0)) return 0;
    if (count && !goodix_berlin_checksum(packet+8,count*8+2)) return 0;
    struct GoodixBerlinFrame frame;memset(&frame,0,sizeof frame);
    unsigned seen=0;
    for (unsigned i=0;i<count;++i) {
        const uint8_t *p=packet+8+i*8;
        unsigned id=p[0]>>4, type=p[0]&15;
        // Stylus types1/3 have a different layout; unknown types fail closed.
        if (type!=0 || id>=10 || (seen&(1u<<id))) return 0;
        seen|=1u<<id;
        struct GoodixBerlinPoint point={(uint8_t)id,goodix_berlin_le16(p+2),
            goodix_berlin_le16(p+4),goodix_berlin_le16(p+6)};
        if (point.x>=1220 || point.y>=2712) return 0;
        frame.points[i]=point;
    }
    frame.count=count;
    *out=frame;
    return 1;
}
