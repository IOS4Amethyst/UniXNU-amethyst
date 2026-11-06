// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stdint.h>
// v7 observer, EE0, one EXT_READL byte. Caller serializes the channel and
// supplies mapped MMIO callbacks; no PMIC write command is exposed.
static inline int amethyst_spmi_read8(void *ctx,
    uint32_t (*read)(void *,unsigned),void (*write)(void *,unsigned,uint32_t),
    void (*delay)(void *),unsigned apid,unsigned reg,uint8_t *value,uint32_t *status) {
    if(!read || !write || !delay || !value || !status || apid>=1024 || reg>255)return -22;
    unsigned channel=apid*0x20;
    write(ctx,channel,(1u<<27)|(reg<<4));
    for(unsigned i=0;i<1000;++i) {
        *status=read(ctx,channel+8);
        if(*status&1) {
            if(*status&14)return -5;
            *value=(uint8_t)read(ctx,channel+0x18);
            return 0;
        }
        delay(ctx);
    }
    return -110;
}
