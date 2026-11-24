// SPDX-License-Identifier: BSD-2-Clause
#pragma once
#include <stdint.h>

// Linux qcom_glink_smem descriptor order and native LE version header, as in
// Sheng AdspProbe::startGlink. Only version frames; leave channel data queued.
struct WpssVersion {
    volatile uint32_t *d, *tx, *rx;
    uint32_t tn, rn; // FIFO sizes in bytes
    bool sent=false, replied=false, agreed=false;
    static void fence() {
#ifdef __aarch64__
        __asm__ volatile("dsb sy" ::: "memory");
#else
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
#endif
    }
    // 0 pending, 1 negotiated, -1 invalid descriptor, -2 unexpected command/version.
    template<class Kick> int step(Kick kick) {
        uint32_t tt=d[0], th=d[1], rt=d[2], rh=d[3];
        if(tn<24 || rn<24 || (tn|rn|tt|th|rt|rh)&7 || tt>=tn || th>=tn || rt>=rn || rh>=rn)return -1;
        fence();
        auto send=[&](uint32_t cmd) {
            // Leave FULL_RESERVE and BLOCKED_CMD_RESERVE (eight bytes each).
            if((tt>th?tt-th:tn-th+tt)<24)return false;
            tx[th/4]=(1u<<16)|cmd;tx[th/4+1]=0; // v1, no optional features
            fence();th=(th+8==tn?0:th+8);d[1]=th;fence();kick();return true;
        };
        if(!sent && !(sent=send(0)))return 0;
        if(rt==rh)return 0;
        uint32_t header=rx[rt/4],features=rx[rt/4+1];
        uint32_t cmd=header&0xffff,version=header>>16;
        if(!version || cmd>1)return -2;
        if(cmd==0) {if(!send(1))return 0;replied=true;}
        else if(version!=1 || features) {if(!send(0))return 0;agreed=false;}
        else agreed=true;
        fence();d[2]=(rt+8==rn?0:rt+8);fence();
        return replied && agreed;
    }
};
