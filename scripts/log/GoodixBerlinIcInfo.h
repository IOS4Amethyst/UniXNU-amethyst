// SPDX-License-Identifier: BSD-2-Clause-Patent
// Goodix Berlin IC_INFO block (BerlinD/GT9916 at 0x10070) and device confirm,
// following Linux drivers/input/touchscreen/goodix_berlin_core.c:
//  - length: LE16 at the start, < 1024; the whole block ends in a LE16 sum
//    of every byte before it; all-0x00/0xff data is a missing device.
//  - layout: length(2), version(16), feature(10), drv/sen/button/force(4), five
//    variable LE16 lists (count byte + 2*count), then misc: cmd_addr at +0 and
//    touch_data_addr at +44 (the event block address).
//  - dev confirm: write 8 x 0xAA at the boot option address 0x10000, read back.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum {
    GoodixBerlinIcInfoAddress = 0x10070,
    GoodixBerlinIcInfoMax = 1024,
    GoodixBerlinBootOptionAddress = 0x10000,
    GoodixBerlinConfirmValue = 0xaa,
    GoodixBerlinConfirmBytes = 8,
};

struct GoodixBerlinIcInfo {
    uint32_t cmdAddress, touchDataAddress, frameDataAddress;
    unsigned length, miscOffset, drvNum, senNum, frameHeadLen, fwAttrLen, fwLogLen;
};
static inline uint32_t goodix_le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static inline int goodix_berlin_block_sum_ok(const uint8_t *data, size_t size) {
    if (size < 2) return 0;
    uint32_t sum = 0;
    for (size_t i = 0; i < size - 2; ++i) sum += data[i];
    return (uint16_t)sum == (uint16_t)(data[size - 2] | data[size - 1] << 8);
}
static inline int goodix_berlin_is_dummy(const uint8_t *data, size_t size) {
    for (size_t i = 0; i < size; ++i)
        if (data[i] > 0 && data[i] < 0xff) return 0;
    return 1;
}
static inline unsigned goodix_berlin_ic_info_length(const uint8_t two[2]) {
    return (unsigned)(two[0] | two[1] << 8);
}
// 0 on success; negative reasons: -1 length, -2 dummy, -3 checksum, -4 layout, -5 null address.
static inline int goodix_berlin_ic_info_parse(const uint8_t *data, size_t length,
    struct GoodixBerlinIcInfo *out) {
    if (!data || !out || length < 2 || length >= GoodixBerlinIcInfoMax) return -1;
    memset(out, 0, sizeof *out);
    out->length = (unsigned)length;
    if (goodix_berlin_ic_info_length(data) != length) return -1;
    if (goodix_berlin_is_dummy(data, length)) return -2;
    if (!goodix_berlin_block_sum_ok(data, length)) return -3;
    size_t offset = 2 + 16 + 10 + 4;
    if (offset >= length) return -4;
    out->drvNum = data[28];
    out->senNum = data[29];
    for (unsigned list = 0; list < 5; ++list) {
        unsigned count = data[offset++];
        offset += 2u * count;
        if (offset >= length) return -4;
    }
    if (offset + 48 > length - 2) return -4;
    const uint8_t *misc = data + offset;
    out->miscOffset = (unsigned)offset;
    // misc: cmd_addr +0, frame_data_addr +24, frame_data_head_len +28, fw_attr_len +30,
    // fw_log_len +32, touch_data_addr +44 (vendor offsets 0xa7/0xbf/0xc3/0xc5/0xc7/0xd3).
    out->cmdAddress = goodix_le32(misc);
    out->frameDataAddress = goodix_le32(misc + 24);
    out->frameHeadLen = (unsigned)(misc[28] | misc[29] << 8);
    out->fwAttrLen = (unsigned)(misc[30] | misc[31] << 8);
    out->fwLogLen = (unsigned)(misc[32] | misc[33] << 8);
    out->touchDataAddress = goodix_le32(misc + 44);
    return out->touchDataAddress ? 0 : -5;
}
