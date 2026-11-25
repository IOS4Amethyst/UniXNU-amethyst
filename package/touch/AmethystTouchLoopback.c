// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "AmethystTouchLoopback.h"
#include <string.h>

static uint32_t read_register(struct geni_spi *spi,uint32_t offset) {
    return spi->se.rd(spi->se.ctx,offset);
}
static void write_register(struct geni_spi *spi,uint32_t offset,uint32_t value) {
    spi->se.wr(spi->se.ctx,offset,value);
}
// A cancel or abort request does not prove the engine stopped. Only a cleared
// master-active bit counts; an uncertain engine keeps the DMA buffers quarantined.
static int wait_idle(struct geni_spi *spi) {
    for(unsigned attempt=0;attempt<2000;++attempt) {
        if(!(read_register(spi,SE_GENI_STATUS)&GENI_STATUS_M_ACTIVE))return 0;
        spi->se.delay_us(spi->se.ctx,10);
    }
    return GENI_ERR_IO;
}
static int quiesce(struct geni_spi *spi) {
    write_register(spi,SE_GENI_M_CMD_CTRL_REG,GENI_M_CMD_CANCEL);
    if(!wait_idle(spi))return 0;
    write_register(spi,SE_GENI_M_CMD_CTRL_REG,GENI_M_CMD_ABORT);
    return wait_idle(spi);
}

// Board must already own idle SE0, its pinned mappings/buffers and a verified
// route. External pins MUST remain in GPIO mode. Never call concurrently or
// retry after a failure; memory and mappings must remain retained until reboot.
// A successful call leaves the engine in normal mode with the loopback bit off.
int amethyst_touch_loopback(struct geni_spi *spi,struct AmethystLoopbackResult *out) {
    if(out)memset(out,0,sizeof *out);
    if(!spi || !out || !spi->se.rd || !spi->se.wr || !spi->se.delay_us ||
        !spi->use_dma || !spi->poll_limit || spi->poll_limit>400000 ||
        !spi->cur_speed_hz || spi->cur_speed_hz>960000 ||
        !spi->dma.tx_virt || !spi->dma.rx_virt || spi->dma.size<16 ||
        !spi->dma.tx_phys || !spi->dma.rx_phys ||
        !spi->dma.clean || !spi->dma.clean_inval || !spi->dma.inval)
        return GENI_ERR_PARAM;
    if(read_register(spi,SE_GENI_STATUS)&GENI_STATUS_M_ACTIVE)return GENI_ERR_IO;
    // The initialized DMA-only engine must be idle and outside loopback mode.
    if(read_register(spi,SE_SPI_LOOPBACK)!=0)return GENI_ERR_IO;
    if(!(read_register(spi,SE_GENI_DMA_MODE_EN)&GENI_DMA_MODE_EN))return GENI_ERR_IO;
    if(spi->info.protocol!=GENI_PROTO_SPI || spi->info.fifo_width_bits!=32 ||
        (spi->info.if_disable&GENI_FIFO_IF_DISABLE)==0)return GENI_ERR_NODEV;
    // Program the loopback bit from a known idle state, without touching CS mux.
    int status=geni_spi_setup(spi,GENI_SPI_LOOP,spi->last_cs);
    if(status)return status;
    static const uint8_t pattern[16]={0x11,0x22,0x33,0x44,0x55,0x66,0x77,0x88,
        0x99,0xaa,0xbb,0xcc,0xdd,0xee,0xff,0};
    uint8_t received[16]={0};
    // Every attempt starts from cleared completion snapshots so a failed call
    // cannot report a previous attempt's evidence.
    spi->last_tx_in=0;spi->last_rx_in=0;spi->snap_valid=0;
    out->transferStatus=geni_spi_transfer(spi,pattern,received,sizeof pattern,0);
    out->status=read_register(spi,SE_GENI_STATUS);
    out->idle=!(out->status&GENI_STATUS_M_ACTIVE);
    out->txBytes=spi->last_tx_in;out->rxBytes=spi->last_rx_in;
    memcpy(out->received,received,sizeof received);
    out->matched=!memcmp(pattern,received,sizeof pattern);
    // Do not reconfigure an engine that may still be executing or whose stop is
    // not proven; the caller must quarantine the controller and buffers instead.
    if(!out->idle && quiesce(spi))return GENI_ERR_TIMEOUT;
    out->status=read_register(spi,SE_GENI_STATUS);
    out->idle=!(out->status&GENI_STATUS_M_ACTIVE);
    if(!out->idle)return GENI_ERR_TIMEOUT;
    status=geni_spi_setup(spi,0,spi->last_cs);
    if(out->transferStatus==GENI_ERR_TIMEOUT)return GENI_ERR_TIMEOUT;
    if(status)return status;
    if(out->transferStatus)return out->transferStatus;
    return out->matched && out->txBytes==16 && out->rxBytes==16?0:GENI_ERR_IO;
}
