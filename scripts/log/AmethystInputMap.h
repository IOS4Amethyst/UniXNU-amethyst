// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>

// Native Consumer-page meanings; no power/volume-to-Home substitution.
enum { AmethystConsumerPage=0x0c, AmethystPowerUsage=0x30,
       AmethystVolumeUpUsage=0xe9, AmethystVolumeDownUsage=0xea };
static inline unsigned amethyst_consumer_usage(unsigned linuxCode) {
    switch(linuxCode) {
        case 116:return AmethystPowerUsage;
        case 115:return AmethystVolumeUpUsage;
        case 114:return AmethystVolumeDownUsage;
        default:return 0;
    }
}
// Normal Goodix coordinates are panel pixels; normalize to the existing
// TouchState extent without applying Android's separate reporting factor100.
static inline int amethyst_touch_coordinate(unsigned pixel,unsigned extent,
    unsigned logicalMaximum,uint32_t *out) {
    if(!out || extent<2 || pixel>=extent || !logicalMaximum)return 0;
    *out=(uint32_t)(((uint64_t)pixel*logicalMaximum)/(extent-1));
    return 1;
}
