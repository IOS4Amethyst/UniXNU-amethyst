// SPDX-License-Identifier: BSD-2-Clause-Patent
// Host check of GoodixBerlinEvent.h: checksums, contact decoding, bounds, write frame.
#include <stdio.h>
#include <stdlib.h>
#include "GoodixBerlinEvent.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

static void seal(uint8_t *d, size_t n) {
    uint16_t s = 0;
    for (size_t i = 0; i < n; ++i) s = (uint16_t)(s + d[i]);
    d[n] = (uint8_t)s; d[n + 1] = (uint8_t)(s >> 8);
}

int main(void) {
    uint8_t e[GoodixBerlinEventBytes];
    struct GoodixBerlinEvent ev;
    memset(e, 0, sizeof e);
    e[0] = 0x80; e[2] = 2;
    seal(e, 6);
    uint8_t *c = e + 8;
    c[0] = 0x10; c[2] = 0x34; c[3] = 0x02; c[4] = 0x78; c[5] = 0x05; c[6] = 9;      // id1 x=0x234 y=0x578 w=9
    c[8] = 0x31; c[10] = 0xff; c[11] = 0x04; c[12] = 0x00; c[13] = 0x0a; c[14] = 3; // id3 type1
    seal(c, 16);
    CHECK(goodix_berlin_event_parse(e, sizeof e, &ev) == 1);
    CHECK(ev.headOk && ev.pointsOk && ev.count == 2);
    CHECK(ev.points[0].id == 1 && ev.points[0].x == 0x234 && ev.points[0].y == 0x578 && ev.points[0].w == 9);
    CHECK(ev.points[1].id == 3 && ev.points[1].type == 1 && ev.points[1].x == 0x4ff && ev.points[1].y == 0xa00);

    c[2] ^= 1;  // contact checksum broken: head still valid, no points
    CHECK(goodix_berlin_event_parse(e, sizeof e, &ev) == 1 && ev.headOk && !ev.pointsOk && ev.points[0].x == 0);

    e[1] = 1;   // head checksum broken: nothing trusted
    CHECK(goodix_berlin_event_parse(e, sizeof e, &ev) == 0 && !ev.headOk);

    memset(e, 0, sizeof e); e[0] = 0x80; e[2] = 0x0f; seal(e, 6);  // 15 contacts: refused
    CHECK(goodix_berlin_event_parse(e, sizeof e, &ev) == 1 && ev.count == 0 && !ev.pointsOk);

    memset(e, 0, sizeof e); e[0] = 0x80; e[2] = 0; seal(e, 6); seal(e + 8, 0);  // all lifted
    CHECK(goodix_berlin_event_parse(e, sizeof e, &ev) == 1 && ev.count == 0 && ev.pointsOk);

    memset(e, 0, sizeof e); seal(e, 6);  // all-zero block: a valid, empty head
    CHECK(goodix_berlin_event_parse(e, sizeof e, &ev) == 1 && ev.headOk && ev.count == 0);
    memset(e, 0xff, sizeof e);           // floating MISO: rejected
    CHECK(goodix_berlin_event_parse(e, sizeof e, &ev) == 0);
    CHECK(goodix_berlin_event_parse(e, 7, &ev) == 0);

    uint8_t tx[8], zero = 0;
    CHECK(goodix_berlin_write_frame(GoodixBerlinEventAddress, &zero, 1, tx, sizeof tx) == 6);
    CHECK(tx[0] == 0xf0 && tx[1] == 0 && tx[2] == 1 && tx[3] == 0x03 && tx[4] == 0x08 && tx[5] == 0);
    CHECK(goodix_berlin_write_frame(GoodixBerlinEventAddress, &zero, 4, tx, 8) == 0);
    CHECK(GoodixBerlinEventBytes == 90);

    if (failures) return 1;
    puts("PASS: Goodix event head/contact checksums, decoding, bounds and write frame");
    return 0;
}
