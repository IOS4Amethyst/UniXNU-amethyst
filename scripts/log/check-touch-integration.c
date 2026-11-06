// SPDX-License-Identifier: BSD-2-Clause-Patent
// Link with the real GeniSpi.c and AmethystTouchLoopback.c, not driver stubs.
#include "AmethystTouchLoopback.h"
#include <assert.h>
#include <string.h>
static uint32_t regs[0x4000/4];
static unsigned commands;
static struct geni_spi spi;
static uint32_t read_reg(void *p,uint32_t offset) {
    (void)p;assert(offset<sizeof regs && !(offset&3));return regs[offset/4];
}
static uint32_t read_wrapper(void *p,uint32_t offset) {
    (void)p;assert(offset==QUPV3_HW_VER_REG);return 0x300d0000;
}
static void write_reg(void *p,uint32_t offset,uint32_t value) {
    (void)p;assert(offset<sizeof regs && !(offset&3));regs[offset/4]=value;
    if(offset==SE_GENI_M_CMD0) {
        assert(regs[SE_SPI_LOOPBACK/4]&GENI_LOOPBACK_ENABLE);
        assert(regs[SE_GENI_DMA_MODE_EN/4]&GENI_DMA_MODE_EN);
        ++commands;
    }
    if(offset==SE_DMA_TX_LEN) {
        assert(value==16 && regs[SE_DMA_RX_LEN/4]==16);
        memcpy(spi.dma.rx_virt,spi.dma.tx_virt,value);
        regs[SE_DMA_TX_IRQ_STAT/4]=regs[SE_DMA_RX_IRQ_STAT/4]=GENI_DMA_DONE;
        regs[SE_DMA_TX_LEN_IN/4]=regs[SE_DMA_RX_LEN_IN/4]=value;
    }
}
static void delay(void *p,unsigned us){(void)p;(void)us;}
static void cache(void *p,void *v,size_t n){(void)p;(void)v;assert(n==16);}
int main(void) {
    uint8_t tx[4096],rx[4096];struct AmethystLoopbackResult result;
    spi.se.rd=read_reg;spi.se.wr=write_reg;spi.se.delay_us=delay;
    spi.wrapper.rd=read_wrapper;spi.use_dma=1;spi.poll_only=1;spi.poll_limit=100;
    spi.dma.tx_virt=tx;spi.dma.rx_virt=rx;spi.dma.tx_phys=0x1000;
    spi.dma.rx_phys=0x2000;spi.dma.size=sizeof tx;
    spi.dma.clean=spi.dma.clean_inval=spi.dma.inval=cache;
    regs[GENI_FW_REVISION_RO/4]=0x102;regs[GENI_IF_DISABLE_RO/4]=1;
    regs[SE_HW_PARAM_0/4]=0x202029e4;
    assert(geni_spi_set_clock(&spi,960000,19200000,0)==GENI_ERR_PARAM);
    assert(!geni_spi_init(&spi) && spi.oversampling==1 && !spi.cur_speed_hz);
    assert(!commands && regs[SE_IRQ_EN/4]==0);
    assert(!geni_spi_set_clock(&spi,960000,19200000,0));
    assert(!amethyst_touch_loopback(&spi,&result));
    assert(commands==1 && result.idle && result.matched);
    assert(result.txBytes==16 && result.rxBytes==16 && !regs[SE_SPI_LOOPBACK/4]);
    return 0;
}
