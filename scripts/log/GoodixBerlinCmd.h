// SPDX-License-Identifier: BSD-2-Clause-Patent
// Goodix BerlinD firmware command, from the amethyst vendor module goodix_core.ko
// (brl_send_cmd, goodix_append_checksum mode 0, goodix_htc_enable):
//  - 16 bytes written at IC_INFO misc.cmd_addr: state 0, ack 0, len, cmd, data...,
//    then the LE16 byte sum of buf[2 .. 2+len-2) (len, cmd, data) right after them.
//  - up to 6 sends; after each, up to 6 reads of the 16 bytes, 1 ms apart:
//    ack (byte 1) 0x80 done (then 4 ms), 0x00/0x02 busy (poll again),
//    0x03 wait 10 ms then resend, anything else resend.
//  - goodix_htc_enable(0) = host touch computing off: cmd 0x90 data 0 (raw frame
//    stream off), then cmd 0x91 data 1 (firmware coordinates on, mode 0).
//    The firmware boots in host computing mode, so its coordinate block stays zero.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

enum {
    GoodixBerlinCmdBytes = 16,
    GoodixBerlinCmdAckOk = 0x80,
    GoodixBerlinCmdTouchRaw = 0x90,
    GoodixBerlinCmdTouchData = 0x91,
};
enum GoodixBerlinAck { GoodixAckDone, GoodixAckBusy, GoodixAckOverflow, GoodixAckResend };

// One data byte (len 5). Returns 0 for a command the vendor never builds this way.
static inline size_t goodix_berlin_cmd_frame(uint8_t cmd, uint8_t data, uint8_t out[GoodixBerlinCmdBytes]) {
    if (!out || cmd < 0x80) return 0;
    memset(out, 0, GoodixBerlinCmdBytes);
    out[2] = 5; out[3] = cmd; out[4] = data;
    uint16_t sum = (uint16_t)(out[2] + out[3] + out[4]);
    out[5] = (uint8_t)sum; out[6] = (uint8_t)(sum >> 8);
    return GoodixBerlinCmdBytes;
}
static inline enum GoodixBerlinAck goodix_berlin_cmd_ack(uint8_t ack) {
    if (ack == GoodixBerlinCmdAckOk) return GoodixAckDone;
    if (!(ack & ~2u)) return GoodixAckBusy;
    if (ack == 3) return GoodixAckOverflow;
    return GoodixAckResend;
}
