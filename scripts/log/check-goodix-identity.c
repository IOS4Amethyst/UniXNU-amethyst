// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "GoodixBerlinIdentity.h"
#include <assert.h>
static uint8_t packet[28];
static int status;
static void checksum(void) {
    unsigned sum=0;for(unsigned i=0;i<26;++i)sum+=packet[i];
    packet[26]=(uint8_t)sum;packet[27]=(uint8_t)(sum>>8);
}
static int transfer(void *context,const uint8_t *tx,uint8_t *rx,size_t bytes) {
    (void)context;
    assert(bytes==36 && tx[0]==0xf1 && tx[1]==0 && tx[2]==1 && tx[3]==0 && tx[4]==0x14);
    memcpy(rx+8,packet,sizeof packet);return status;
}
int main(void) {
    memcpy(packet,"BERLIN",6);packet[6]=2;packet[8]=4;
    memcpy(packet+10,"GEST",4);packet[20]=1;packet[21]=4;packet[23]=255;checksum();
    uint8_t tx[36],rx[36];struct GoodixBerlinIdentity identity={0},before;
    assert(!goodix_berlin_read_identity(transfer,0,&identity,tx,rx,sizeof tx));
    assert(!strcmp(identity.rom,"BERLIN") && !strcmp(identity.patch,"GEST"));
    assert(identity.sensor==255 && identity.romVersion[0]==2 && identity.patchVersion[3]==4);
    before=identity;
    status=-110;assert(goodix_berlin_read_identity(transfer,0,&identity,tx,rx,sizeof tx)==-110);
    status=0;packet[27]^=1;assert(!goodix_berlin_identity(packet,28,&identity));
    assert(goodix_berlin_read_identity(transfer,0,&identity,tx,rx,sizeof tx)==-71);
    checksum();packet[0]='X';checksum();assert(!goodix_berlin_identity(packet,28,&identity));
    assert(!goodix_berlin_identity(packet,27,&identity));
    assert(!memcmp(&identity,&before,sizeof identity));
    return 0;
}
