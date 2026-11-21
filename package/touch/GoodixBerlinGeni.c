// SPDX-License-Identifier: BSD-2-Clause-Patent
#include "GoodixBerlinSpi.h"
#include "GeniSpi.h"
#include "GoodixBerlinEvent.h"

static int goodix_geni_ready(struct geni_spi *spi, size_t total);

// The board owns controller initialization, mode0/CS0 setup, DMA mappings and
// scratch-buffer serialization. This adapter changes none of those resources.
static int goodix_geni_transfer(void *context, const uint8_t *tx,
    uint8_t *rx, size_t bytes) {
    return geni_spi_transfer((struct geni_spi *)context,tx,rx,bytes,0);
}

int amethyst_goodix_read(struct geni_spi *spi, uint32_t address,
    uint8_t *out, size_t bytes, uint8_t *tx, uint8_t *rx, size_t capacity);
int amethyst_goodix_read(struct geni_spi *spi, uint32_t address,
    uint8_t *out, size_t bytes, uint8_t *tx, uint8_t *rx, size_t capacity) {
    if (!bytes || bytes>GoodixBerlinReadMax) return GENI_ERR_PARAM;
    int ready=goodix_geni_ready(spi,GoodixBerlinReadPrefix+bytes);
    if (ready) return ready;
    return goodix_berlin_read(goodix_geni_transfer,spi,address,out,bytes,tx,rx,capacity);
}

// BerlinD write (F0 + BE32 address + data); same engine and buffer checks as the read.
int amethyst_goodix_write(struct geni_spi *spi, uint32_t address,
    const uint8_t *data, size_t bytes, uint8_t *tx, uint8_t *rx, size_t capacity);
int amethyst_goodix_write(struct geni_spi *spi, uint32_t address,
    const uint8_t *data, size_t bytes, uint8_t *tx, uint8_t *rx, size_t capacity) {
    if (!spi || !data || !bytes || bytes>GoodixBerlinReadMax || !tx || !rx ||
        !goodix_berlin_disjoint(tx,capacity,rx,capacity)) return GENI_ERR_PARAM;
    int ready=goodix_geni_ready(spi,GoodixBerlinWritePrefix+bytes);
    if (ready) return ready;
    size_t total=goodix_berlin_write_frame(address,data,bytes,tx,capacity);
    if (!total) return GENI_ERR_PARAM;
    return goodix_geni_transfer(spi,tx,rx,total);
}

static int goodix_geni_ready(struct geni_spi *spi, size_t total) {
    if (!spi || !spi->se.rd || !spi->se.wr || !spi->se.delay_us ||
        spi->info.protocol!=GENI_PROTO_SPI || spi->last_mode!=0 ||
        spi->last_cs!=0 || !spi->poll_limit || !spi->cur_speed_hz ||
        spi->cur_speed_hz>15000000)
        return GENI_ERR_PARAM;
    if (spi->use_dma) {
        // DMA addresses must be independently owned/translated by board setup;
        // these checks establish extent/non-aliasing, not SMMU reachability.
        if (!spi->dma.clean || !spi->dma.clean_inval || !spi->dma.inval ||
            spi->dma.size<total || !spi->dma.tx_phys || !spi->dma.rx_phys ||
            total>UINT64_MAX-spi->dma.tx_phys || total>UINT64_MAX-spi->dma.rx_phys ||
            !(spi->dma.tx_phys+total<=spi->dma.rx_phys ||
              spi->dma.rx_phys+total<=spi->dma.tx_phys) ||
            !goodix_berlin_disjoint(spi->dma.tx_virt,total,spi->dma.rx_virt,total))
            return GENI_ERR_PARAM;
    } else if (spi->info.fifo_disabled || !spi->info.tx_fifo_depth ||
               spi->info.fifo_width_bits!=32) return GENI_ERR_NOTSUP;
    // Do not submit over an outstanding command left by another owner.
    if (spi->se.rd(spi->se.ctx,SE_GENI_STATUS)&GENI_STATUS_M_ACTIVE)
        return GENI_ERR_IO;
    return 0;
}
