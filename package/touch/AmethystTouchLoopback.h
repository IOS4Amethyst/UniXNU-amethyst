// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
#include "GeniSpi.h"
struct AmethystLoopbackResult {
    int transferStatus, matched, idle;
    uint32_t status, txBytes, rxBytes;
    uint8_t received[16];
};
int amethyst_touch_loopback(struct geni_spi *,struct AmethystLoopbackResult *);
