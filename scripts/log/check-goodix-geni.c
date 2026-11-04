// SPDX-License-Identifier: BSD-2-Clause-Patent
// Link with GoodixBerlinGeni.c and include QcomGeniSpi; no MMIO is accessed.
#include "GeniSpi.h"
#include <assert.h>
#include <string.h>
int amethyst_goodix_read(struct geni_spi *,uint32_t,uint8_t *,size_t,uint8_t *,uint8_t *,size_t);
static unsigned transfers,active;
static uint32_t rd(void *p,uint32_t reg){(void)p;assert(reg==SE_GENI_STATUS);return active;}
static void wr(void *p,uint32_t r,uint32_t v){(void)p;(void)r;(void)v;assert(0);}
static void delay(void *p,unsigned us){(void)p;(void)us;}
static void cache(void *p,void *v,size_t n){(void)p;(void)v;(void)n;}
int geni_spi_transfer(struct geni_spi *s,const uint8_t *tx,uint8_t *rx,size_t n,int keep){
    (void)s;assert(n==10 && !keep && tx[0]==0xf1);++transfers;
    memset(rx,0,n);rx[8]=0x12;rx[9]=0x34;return 0;
}
int main(void){
    struct geni_spi s={0};uint8_t tx[40],rx[40],out[2]={0},dmaTx[40],dmaRx[40];
    assert(amethyst_goodix_read(&s,0x10014,out,2,tx,rx,40)==GENI_ERR_PARAM);
    s.se.rd=rd;s.se.wr=wr;s.se.delay_us=delay;s.info.protocol=GENI_PROTO_SPI;
    s.poll_limit=100;s.cur_speed_hz=960000;s.info.tx_fifo_depth=16;s.info.fifo_width_bits=32;
    assert(!amethyst_goodix_read(&s,0x10014,out,2,tx,rx,40));
    assert(out[0]==0x12 && out[1]==0x34 && transfers==1);
    active=GENI_STATUS_M_ACTIVE;
    assert(amethyst_goodix_read(&s,0,out,2,tx,rx,40)==GENI_ERR_IO);active=0;
    s.info.fifo_disabled=1;
    assert(amethyst_goodix_read(&s,0,out,2,tx,rx,40)==GENI_ERR_NOTSUP);
    s.use_dma=1;
    assert(amethyst_goodix_read(&s,0,out,2,tx,rx,40)==GENI_ERR_PARAM);
    s.dma.clean=cache;s.dma.clean_inval=cache;s.dma.inval=cache;s.dma.size=40;
    s.dma.tx_virt=dmaTx;s.dma.rx_virt=dmaRx;s.dma.tx_phys=0x10000;s.dma.rx_phys=0x20000;
    assert(!amethyst_goodix_read(&s,0,out,2,tx,rx,40));
    s.dma.rx_phys=s.dma.tx_phys+1;
    assert(amethyst_goodix_read(&s,0,out,2,tx,rx,40)==GENI_ERR_PARAM);
    s.dma.rx_phys=UINT64_MAX-2;
    assert(amethyst_goodix_read(&s,0,out,2,tx,rx,40)==GENI_ERR_PARAM);
    assert(transfers==2);
    return 0;
}
