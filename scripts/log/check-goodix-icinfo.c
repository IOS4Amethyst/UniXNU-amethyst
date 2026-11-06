// SPDX-License-Identifier: BSD-2-Clause-Patent
// Host check of GoodixBerlinIcInfo.h against a block built with Linux's layout.
#include <stdio.h>
#include "GoodixBerlinIcInfo.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

static size_t build(uint8_t *b, const unsigned lists[5], uint32_t touch) {
    size_t at = 2;
    for (unsigned i = 0; i < 16; ++i) b[at++] = (uint8_t)(0x10 + i);  // version
    for (unsigned i = 0; i < 10; ++i) b[at++] = (uint8_t)(0x20 + i);  // feature
    b[at++] = 36; b[at++] = 16; b[at++] = 0; b[at++] = 0;              // drv sen button force
    for (unsigned l = 0; l < 5; ++l) {
        b[at++] = (uint8_t)lists[l];
        for (unsigned i = 0; i < 2 * lists[l]; ++i) b[at++] = (uint8_t)(0x40 + i);
    }
    size_t misc = at;
    for (unsigned i = 0; i < 120; ++i) b[at++] = 0x55;                 // the rest of misc
    b[misc] = 0x00; b[misc + 1] = 0x01; b[misc + 2] = 0x01; b[misc + 3] = 0;  // cmd 0x10100
    b[misc + 24] = 0x00; b[misc + 25] = 0x20; b[misc + 26] = 0x01; b[misc + 27] = 0;   // frame 0x12000
    b[misc + 28] = 16; b[misc + 29] = 0; b[misc + 30] = 0x20; b[misc + 31] = 0; b[misc + 32] = 4; b[misc + 33] = 1;
    b[misc + 44] = (uint8_t)touch; b[misc + 45] = (uint8_t)(touch >> 8);
    b[misc + 46] = (uint8_t)(touch >> 16); b[misc + 47] = (uint8_t)(touch >> 24);
    size_t length = at + 2;
    b[0] = (uint8_t)length; b[1] = (uint8_t)(length >> 8);
    uint32_t sum = 0;
    for (size_t i = 0; i < at; ++i) sum += b[i];
    b[at] = (uint8_t)sum; b[at + 1] = (uint8_t)(sum >> 8);
    return length;
}

int main(void) {
    uint8_t b[GoodixBerlinIcInfoMax];
    struct GoodixBerlinIcInfo info;
    const unsigned lists[5] = {3, 5, 2, 2, 1};
    size_t n = build(b, lists, 0x10308);
    CHECK(goodix_berlin_ic_info_parse(b, n, &info) == 0);
    CHECK(info.touchDataAddress == 0x10308 && info.cmdAddress == 0x10100);
    CHECK(info.frameDataAddress == 0x12000 && info.frameHeadLen == 16 && info.fwAttrLen == 0x20 && info.fwLogLen == 0x104);
    CHECK(info.drvNum == 36 && info.senNum == 16);
    CHECK(info.miscOffset == 2 + 16 + 10 + 4 + 5 + 2 * (3 + 5 + 2 + 2 + 1));
    b[40] ^= 1;
    CHECK(goodix_berlin_ic_info_parse(b, n, &info) == -3);
    n = build(b, lists, 0);
    CHECK(goodix_berlin_ic_info_parse(b, n, &info) == -5);
    CHECK(goodix_berlin_ic_info_parse(b, n - 1, &info) == -1);     // length field disagrees
    memset(b, 0xff, sizeof b); b[0] = 100; b[1] = 0;
    CHECK(goodix_berlin_ic_info_parse(b, 100, &info) == -2 || goodix_berlin_ic_info_parse(b, 100, &info) == -3);
    memset(b, 0, sizeof b); b[0] = 0; b[1] = 4;                     // 1024: too long
    CHECK(goodix_berlin_ic_info_parse(b, 1024, &info) == -1);
    const unsigned huge[5] = {200, 200, 0, 0, 0};                    // lists run past the end
    n = build(b, huge, 0x10308);
    b[0] = 60; b[1] = 0;
    CHECK(goodix_berlin_ic_info_parse(b, 60, &info) != 0);
    if (failures) return 1;
    puts("PASS: Goodix IC_INFO length/dummy/checksum, variable lists, frame/touch addresses and frame layout");
    return 0;
}
