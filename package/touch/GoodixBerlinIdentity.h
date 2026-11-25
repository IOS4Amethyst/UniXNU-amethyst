// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include "GoodixBerlinPacket.h"
#include "GoodixBerlinSpi.h"

enum { GoodixBerlinVersionAddress=0x10014, GoodixBerlinVersionBytes=28 };
struct GoodixBerlinIdentity {
    char rom[7], patch[9];
    uint8_t romVersion[3], patchVersion[4], sensor;
};
static inline int goodix_berlin_identity(const uint8_t *data,size_t bytes,
    struct GoodixBerlinIdentity *out) {
    if (!data || !out || bytes!=GoodixBerlinVersionBytes ||
        !goodix_berlin_checksum(data,bytes) || memcmp(data,"BERLIN",6)) return 0;
    struct GoodixBerlinIdentity identity;memset(&identity,0,sizeof identity);
    memcpy(identity.rom,data,6);memcpy(identity.romVersion,data+6,3);
    memcpy(identity.patch,data+10,8);memcpy(identity.patchVersion,data+18,4);
    identity.sensor=data[23];
    *out=identity;
    return 1;
}
// One bounded read; firmware/config upload and controller resets are excluded.
static inline int goodix_berlin_read_identity(GoodixBerlinTransfer transfer,
    void *context,struct GoodixBerlinIdentity *out,uint8_t *tx,uint8_t *rx,size_t capacity) {
    if (!out) return -22;
    uint8_t data[GoodixBerlinVersionBytes];
    int status=goodix_berlin_read(transfer,context,GoodixBerlinVersionAddress,
        data,sizeof data,tx,rx,capacity);
    if (status) return status;
    return goodix_berlin_identity(data,sizeof data,out)?0:-71;
}
