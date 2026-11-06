// SPDX-License-Identifier: BSD-2-Clause-Patent
// cc -std=c11 -Wall -Wextra -Werror check-goodix-packet.c -o CHECK && ./CHECK
#include "GoodixBerlinPacket.h"
#include <assert.h>
#include <string.h>

static void checksum(uint8_t *p,size_t n) {
    unsigned sum=0;
    for(size_t i=0;i<n-2;++i)sum+=p[i];
    p[n-2]=(uint8_t)sum;p[n-1]=(uint8_t)(sum>>8);
}
int main(void) {
    uint8_t p[90]={0};struct GoodixBerlinFrame frame={0},before;
    p[0]=0x80;p[2]=1;checksum(p,8);
    p[8]=0x90;p[10]=0xc3;p[11]=4; // slot9, x1219
    p[12]=0x97;p[13]=0xa;p[14]=42; // y2711, width42
    checksum(p+8,10);
    assert(goodix_berlin_decode(p,18,&frame));
    assert(frame.count==1 && frame.points[0].id==9 && frame.points[0].x==1219 &&
           frame.points[0].y==2711 && frame.points[0].width==42);
    before=frame;
    for(size_t n=0;n<18;++n)assert(!goodix_berlin_decode(p,n,&frame));
    p[17]^=1;assert(!goodix_berlin_decode(p,18,&frame));p[17]^=1;
    p[8]=0x91;checksum(p+8,10);assert(!goodix_berlin_decode(p,18,&frame));
    p[8]=0xa0;checksum(p+8,10);assert(!goodix_berlin_decode(p,18,&frame));
    p[8]=0x90;p[10]=0xc4;checksum(p+8,10);assert(!goodix_berlin_decode(p,18,&frame));
    assert(!memcmp(&before,&frame,sizeof frame));
    p[10]=0xc3;p[2]=2;memcpy(p+16,p+8,8);checksum(p,8);checksum(p+8,18);
    assert(!goodix_berlin_decode(p,26,&frame)); // duplicate ID
    p[16]=0x80;checksum(p+8,18);assert(goodix_berlin_decode(p,26,&frame));
    p[2]=11;checksum(p,8);assert(!goodix_berlin_decode(p,sizeof p,&frame));
    p[2]=0;checksum(p,8);assert(goodix_berlin_decode(p,8,&frame) && !frame.count);
    p[0]=0;checksum(p,8);assert(!goodix_berlin_decode(p,8,&frame));
    assert(!goodix_berlin_decode(0,8,&frame));
    return 0;
}
