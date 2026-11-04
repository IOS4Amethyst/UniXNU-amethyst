// SPDX-License-Identifier: BSD-2-Clause-Patent
// Host check of GoodixBerlinCmd.h against the vendor brl_send_cmd framing.
#include <stdio.h>
#include "GoodixBerlinCmd.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

int main(void) {
    uint8_t f[GoodixBerlinCmdBytes];
    CHECK(goodix_berlin_cmd_frame(GoodixBerlinCmdTouchRaw, 0, f) == 16);
    const uint8_t raw_off[16] = {0, 0, 5, 0x90, 0, 0x95, 0};
    CHECK(!memcmp(f, raw_off, 16));
    CHECK(goodix_berlin_cmd_frame(GoodixBerlinCmdTouchData, 1, f) == 16);
    const uint8_t coord_on[16] = {0, 0, 5, 0x91, 1, 0x97, 0};
    CHECK(!memcmp(f, coord_on, 16));
    CHECK(goodix_berlin_cmd_frame(0xff, 0xff, f) == 16 && f[5] == 0x03 && f[6] == 0x02);  // 5+255+255 = 0x203
    CHECK(goodix_berlin_cmd_frame(0x10, 0, f) == 0);
    CHECK(goodix_berlin_cmd_ack(0x80) == GoodixAckDone);
    CHECK(goodix_berlin_cmd_ack(0x00) == GoodixAckBusy && goodix_berlin_cmd_ack(0x02) == GoodixAckBusy);
    CHECK(goodix_berlin_cmd_ack(0x03) == GoodixAckOverflow);
    CHECK(goodix_berlin_cmd_ack(0x01) == GoodixAckResend && goodix_berlin_cmd_ack(0xff) == GoodixAckResend);
    if (failures) return 1;
    puts("PASS: Goodix command frame (len 5, LE16 byte sum), ack classes per brl_send_cmd");
    return 0;
}
