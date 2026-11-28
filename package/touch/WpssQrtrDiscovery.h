// SPDX-License-Identifier: BSD-2-Clause
#pragma once
#include <stdint.h>

// QRTR v1 discovery control packets, following Sheng's QcomQrtr.c.
// ponytail: no v2, DATA, or service table until the discovery transport runs.
struct WpssQrtrDiscovery {
    uint32_t type=0, source=0, destination=0;
    uint32_t service=0, instance=0, node=0, port=0;

    bool parse(const uint8_t *p,uint32_t bytes) {
        *this={};
        constexpr uint32_t ctrl=0xfffffffeu,broadcast=0xffffffffu;
        if(!p || bytes!=52 || word(p)!=1 || word(p+20)!=20)return false;
        const uint32_t command=word(p+4),src=word(p+8),dst=word(p+24);
        if((command!=2 && command!=4) || word(p+32)!=command || src==broadcast ||
           word(p+12)!=ctrl || word(p+28)!=ctrl || word(p+16)>1)return false;
        type=command;source=src;destination=dst;
        if(command==4) {
            service=word(p+36);instance=word(p+40);node=word(p+44);port=word(p+48);
        }
        return true;
    }

private:
    static uint32_t word(const uint8_t *p) {
        return uint32_t(p[0]) | uint32_t(p[1])<<8 | uint32_t(p[2])<<16 | uint32_t(p[3])<<24;
    }
};
