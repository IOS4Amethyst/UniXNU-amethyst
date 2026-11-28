// SPDX-License-Identifier: BSD-2-Clause
#include "../../package/touch/WpssQrtrDiscovery.h"
#include <cassert>

int main() {
    uint8_t packet[53]={};
    auto put=[&](unsigned offset,uint32_t value) {
        for(unsigned i=0;i<4;++i)packet[1+offset+i]=uint8_t(value>>(8*i));
    };
    const auto *p=packet+1; // Exercise unaligned decoding.
    put(0,1);put(4,2);put(8,13);put(12,0xfffffffe);put(20,20);
    put(24,0xffffffff);put(28,0xfffffffe);put(32,2);
    WpssQrtrDiscovery q;
    assert(q.parse(p,52) && q.type==2 && q.source==13 && !q.service);
    for(unsigned n=0;n<52;++n)assert(!q.parse(p,n) && !q.type);
    put(32,4);assert(!q.parse(p,52)); // Envelope/payload command mismatch.
    put(4,4);put(24,1);put(36,69);put(40,0x301);put(44,13);put(48,0x1234);
    assert(q.parse(p,52) && q.type==4 && q.service==69 && q.instance==0x301 && q.node==13 && q.port==0x1234);
    put(20,0xffffffff);assert(!q.parse(p,52) && !q.service);
    put(20,20);put(28,7);assert(!q.parse(p,52));
}
