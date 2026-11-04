// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "AmethystTouchLoopback.h"
#include <assert.h>
#include <string.h>
static unsigned calls,cancel,abortCalls,loopback,dmaMode;
// mode0: the timed-out command keeps running; it stops only after the cancel.
// mode1: the timed-out command never stops, even after cancel and abort.
static int activeCalls,corrupt,error,stuck;
static uint32_t read_reg(void *p,uint32_t reg){
    (void)p;
    switch(reg){
    case SE_GENI_STATUS:return activeCalls?GENI_STATUS_M_ACTIVE:0;
    case SE_SPI_LOOPBACK:return loopback;
    case SE_GENI_DMA_MODE_EN:return dmaMode;
    default:assert(0);return 0;
    }
}
static void write_reg(void *p,uint32_t reg,uint32_t value){
    (void)p;
    if(reg==SE_SPI_LOOPBACK){assert(!activeCalls || !value);loopback=value;return;}
    if(reg==SE_GENI_M_CMD_CTRL_REG){
        if(value==GENI_M_CMD_CANCEL){++cancel;if(!stuck)activeCalls=0;}
        else if(value==GENI_M_CMD_ABORT)++abortCalls;
        else assert(0);
        return;
    }
    assert(0);
}
static void delay(void *p,unsigned us){(void)p;(void)us;}
static void cache(void *p,void *v,size_t n){(void)p;(void)v;(void)n;}
int geni_spi_setup(struct geni_spi *s,unsigned mode,unsigned chip_select){
    assert((mode==0 || mode==GENI_SPI_LOOP) && !chip_select && !activeCalls);
    ++calls;s->last_mode=mode;s->last_cs=chip_select;
    loopback=(mode&GENI_SPI_LOOP)?1:0;
    return 0;
}
int geni_spi_transfer(struct geni_spi *s,const uint8_t *tx,uint8_t *rx,size_t n,int keep){
    assert(s->last_mode==GENI_SPI_LOOP && n==16 && !keep && dmaMode==GENI_DMA_MODE_EN);
    memcpy(rx,tx,n);if(corrupt)rx[0]^=1;
    s->last_tx_in=s->last_rx_in=16;
    if(error==GENI_ERR_TIMEOUT)activeCalls=1;   // a timed-out command may still be running
    return error;
}
int main(void){
    struct geni_spi spi={0};struct AmethystLoopbackResult out;uint8_t tx[16],rx[16];
    spi.se.rd=read_reg;spi.se.wr=write_reg;spi.se.delay_us=delay;
    spi.use_dma=1;spi.poll_limit=100;spi.cur_speed_hz=960000;
    spi.info.protocol=GENI_PROTO_SPI;spi.info.fifo_width_bits=32;spi.info.if_disable=GENI_FIFO_IF_DISABLE;
    spi.dma.tx_virt=tx;spi.dma.rx_virt=rx;spi.dma.size=16;
    spi.dma.tx_phys=0x1000;spi.dma.rx_phys=0x2000;
    spi.dma.clean=cache;spi.dma.clean_inval=cache;spi.dma.inval=cache;
    dmaMode=GENI_DMA_MODE_EN;
    // Success restores normal mode and reports the echoed pattern.
    assert(!amethyst_touch_loopback(&spi,&out) && out.idle && out.matched && calls==2 && !loopback);
    // Stale snapshots from an earlier attempt are cleared before the attempt.
    spi.last_tx_in=99;corrupt=1;
    assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_IO && !out.matched && out.txBytes==16);corrupt=0;
    // A timeout whose command keeps running is cancelled and proven stopped.
    error=GENI_ERR_TIMEOUT;
    assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_TIMEOUT);
    assert(cancel==1 && abortCalls==0 && !loopback && !activeCalls);
    assert(out.idle && !(out.status&GENI_STATUS_M_ACTIVE));
    // An engine that never stops is not reconfigured and reports uncertainty.
    error=GENI_ERR_TIMEOUT;stuck=1;
    assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_TIMEOUT);
    assert(abortCalls==1 && loopback && out.idle==0);   // setup(0) never ran
    error=0;stuck=0;activeCalls=0;loopback=0;
    // Refused calls clear the result so no stale evidence reaches the log.
    memset(&out,0xff,sizeof out);
    loopback=1;assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_IO);loopback=0;
    dmaMode=0;assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_IO);dmaMode=GENI_DMA_MODE_EN;
    assert(!out.matched && !out.txBytes && !out.rxBytes && !out.transferStatus);
    // FIFO reachable means this is not a FIFO-disabled DMA-only engine.
    spi.info.if_disable=0;assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_NODEV);spi.info.if_disable=GENI_FIFO_IF_DISABLE;
    // Wrong protocol is refused before any register write.
    spi.info.protocol=GENI_PROTO_NONE;assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_NODEV);spi.info.protocol=GENI_PROTO_SPI;
    // Transfers longer than the retained buffer are refused by the caller's size.
    memset(&out,0xff,sizeof out);
    spi.dma.size=8;assert(amethyst_touch_loopback(&spi,&out)==GENI_ERR_PARAM);spi.dma.size=16;
    assert(!out.idle && !out.matched && !out.txBytes && !out.rxBytes);
    return 0;
}
