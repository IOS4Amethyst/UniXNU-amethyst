// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

// BerlinD: F1 + 32-bit big-endian address + three FF dummy bytes.
// The full-duplex response starts at byte8. BerlinA's byte9 format is different.
enum { GoodixBerlinReadPrefix = 8, GoodixBerlinReadMax = 2560 };
typedef int (*GoodixBerlinTransfer)(void *, const uint8_t *, uint8_t *, size_t);
static inline int goodix_berlin_disjoint(const void *a,size_t na,const void *b,size_t nb) {
    uintptr_t x=(uintptr_t)a,y=(uintptr_t)b;
    return x && y && na<=UINTPTR_MAX-x && nb<=UINTPTR_MAX-y &&
        (x+na<=y || y+nb<=x);
}
static inline size_t goodix_berlin_read_frame(uint32_t address, size_t bytes,
    uint8_t *tx, size_t capacity) {
    if (!tx || !bytes || bytes>GoodixBerlinReadMax ||
        bytes>capacity || capacity-bytes<GoodixBerlinReadPrefix ||
        bytes-1>UINT32_MAX-address) return 0;
    size_t total=GoodixBerlinReadPrefix+bytes;
    memset(tx,0,total);
    tx[0]=0xf1;
    for(unsigned i=0;i<4;++i)tx[1+i]=(uint8_t)(address>>(24-i*8));
    tx[5]=tx[6]=tx[7]=0xff;
    return total;
}
// Caller supplies exclusive scratch buffers and a prepared, bounded transport.
// Output is unchanged after transport failure. No mode changes or retry writes.
static inline int goodix_berlin_read(GoodixBerlinTransfer transfer, void *context,
    uint32_t address, uint8_t *out, size_t bytes,
    uint8_t *tx, uint8_t *rx, size_t capacity) {
    if (!transfer || !goodix_berlin_disjoint(tx,capacity,rx,capacity) ||
        !goodix_berlin_disjoint(out,bytes,tx,capacity) ||
        !goodix_berlin_disjoint(out,bytes,rx,capacity)) return -22;
    size_t total=goodix_berlin_read_frame(address,bytes,tx,capacity);
    if (!total) return -22;
    int status=transfer(context,tx,rx,total);
    if (status) return status;
    memcpy(out,rx+GoodixBerlinReadPrefix,bytes);
    return 0;
}
