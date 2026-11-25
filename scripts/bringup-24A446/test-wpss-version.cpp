// SPDX-License-Identifier: BSD-2-Clause
#include "../../package/touch/WpssGlinkVersion.h"
#include <cassert>

int main() {
    uint32_t d[]={8,24,24,16},tx[8]={},rx[8]={};
    WpssVersion v{};v.d=d;v.tx=tx;v.rx=rx;v.tn=v.rn=32;
    int kicks=0;auto kick=[&]{++kicks;};
    rx[6]=1u<<16;rx[7]=1;rx[0]=0x10001;rx[2]=2; // OPEN after ACK stays queued
    assert(v.step(kick)==0 && !kicks && d[2]==24); // insufficient TX space
    d[0]=24;
    assert(v.step(kick)==0 && kicks==2 && tx[6]==0x10000 && tx[0]==0x10001 && !tx[1]);
    assert(v.step(kick)==1 && d[2]==8 && v.agreed && v.replied);
    d[3]=33;assert(v.step(kick)==-1 && d[2]==8 && kicks==2);
}
