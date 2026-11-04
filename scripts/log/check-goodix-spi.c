// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "GoodixBerlinSpi.h"
#include <assert.h>
static int fake_transfer(void *context,const uint8_t *tx,uint8_t *rx,size_t bytes) {
    int status=*(int *)context;
    assert(bytes==10 && tx[0]==0xf1 && tx[4]==0x70);
    memset(rx,0xa5,bytes);rx[8]=0x12;rx[9]=0x34;
    return status;
}
int main(void) {
    uint8_t tx[GoodixBerlinReadPrefix+GoodixBerlinReadMax+1];
    memset(tx,0xa5,sizeof tx);
    assert(goodix_berlin_read_frame(0x10070,2,tx,sizeof tx)==10);
    const uint8_t expected[]={0xf1,0,1,0,0x70,0xff,0xff,0xff,0,0};
    assert(!memcmp(tx,expected,sizeof expected) && tx[10]==0xa5);
    assert(!goodix_berlin_read_frame(0,0,tx,sizeof tx));
    assert(!goodix_berlin_read_frame(0,GoodixBerlinReadMax+1,tx,sizeof tx));
    assert(!goodix_berlin_read_frame(0,2,tx,9));
    assert(!goodix_berlin_read_frame(UINT32_MAX,2,tx,sizeof tx));
    assert(!goodix_berlin_read_frame(0,1,0,sizeof tx));
    assert(goodix_berlin_read_frame(UINT32_MAX,1,tx,sizeof tx)==9);
    assert(goodix_berlin_read_frame(0,GoodixBerlinReadMax,tx,sizeof tx)==GoodixBerlinReadPrefix+GoodixBerlinReadMax);
    memset(tx,0xa5,sizeof tx);
    assert(goodix_berlin_read_frame(0,1024,tx,sizeof tx)==1032);
    assert(tx[1032]==0xa5);
    uint8_t rx[1032]={0},out[2]={0xcc,0xdd};int status=-110;
    assert(goodix_berlin_read(fake_transfer,&status,0x10070,out,2,tx,rx,sizeof rx)==-110);
    assert(out[0]==0xcc && out[1]==0xdd);
    status=0;
    assert(!goodix_berlin_read(fake_transfer,&status,0x10070,out,2,tx,rx,sizeof rx));
    assert(out[0]==0x12 && out[1]==0x34);
    assert(goodix_berlin_read(0,&status,0,out,2,tx,rx,sizeof rx)==-22);
    return 0;
}
