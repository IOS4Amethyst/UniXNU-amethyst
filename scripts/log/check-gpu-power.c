// SPDX-License-Identifier: BSD-2-Clause-Patent
// Build: gcc -I. -I<NativeAdreno> check-gpu-power.c (GpuPowerBoard.h includes NativeAdrenoA810HwInit.h since V42).
// Host check of GpuPowerBoard.h modes 1-3 (the only modes amethyst enables) against a simulated
// register file: mode 1 never writes; mode 2 writes only gcc_gpu_cfg_ahb_clk's enable; mode 3 powers
// CX only from a collapsed GDSC, refuses one under HW control, and reads the SMMU only once CX and its
// vote clock are on; a missing window or a read error stops the later stages.
#include <stdio.h>
#include <string.h>
#include "GpuPowerBoard.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); ++failures; } } while (0)

struct sim {
    uint32_t gcc[0x400], vote[0x400], cc[0x9800 / 4], smmu[0x400], gpu[0x400];
    unsigned writes, smmu_reads, fail_window;
    int cx_powers, vote_clock_turns_on;
};
static uint32_t *reg(struct sim *s, unsigned w, uint32_t off) {
    switch (w) {
    case GPB_WIN_GCC_GPU: return &s->gcc[off / 4];
    case GPB_WIN_GCC_VOTE: return &s->vote[off / 4];
    case GPB_WIN_GPUCC: return &s->cc[off / 4];
    case GPB_WIN_SMMU: return &s->smmu[off / 4];
    case GPB_WIN_GPU: return &s->gpu[off / 4];
    }
    return 0;
}
static int rd(void *c, unsigned w, uint32_t off, uint32_t *out) {
    struct sim *s = c;
    if (w == s->fail_window) return -1;
    if (w == GPB_WIN_SMMU) ++s->smmu_reads;
    *out = *reg(s, w, off);
    return 0;
}
static int wr(void *c, unsigned w, uint32_t off, uint32_t v) {
    struct sim *s = c;
    ++s->writes;
    *reg(s, w, off) = v;
    // The hardware's answers: enabling the GDSC raises PWR_ON in the hw-ctrl status; enabling a branch clears CLK_OFF.
    if (w == GPB_WIN_GPUCC && off == GPB_CC_CX_GDSCR && !(v & GPB_GDSC_SW_COLLAPSE) && s->cx_powers)
        s->cc[GPB_CC_CX_GDS_HW_CTRL / 4] |= GPB_GDSC_PWR_ON;
    if (w == GPB_WIN_GPUCC && off == GPB_CC_SMMU_VOTE_CLK && (v & GPB_CBCR_ENABLE) && s->vote_clock_turns_on)
        s->cc[off / 4] &= ~GPB_CBCR_CLK_OFF;
    return 0;
}
static void reset(struct sim *s) {
    memset(s, 0, sizeof *s);
    s->fail_window = 99;
    s->cx_powers = s->vote_clock_turns_on = 1;
    s->gcc[GPB_GCC_GPU_CFG_AHB / 4] = GPB_CBCR_CLK_OFF;                  // AHB branch disabled after boot
    s->cc[GPB_CC_CX_GDSCR / 4] = GPB_GDSC_SW_COLLAPSE;                     // CX collapsed
    s->cc[GPB_CC_SMMU_VOTE_CLK / 4] = GPB_CBCR_CLK_OFF;
    s->cc[GPB_CC_AHB_CLK / 4] = GPB_CBCR_CLK_OFF;
    s->smmu[GPB_SMMU_ID0 / 4] = 0x12345678;
}

int main(void) {
    static struct sim s;
    struct gpb_io io = {&s, rd, wr, 0, 0};
    struct gpb_result r;
    const unsigned all = (1u << GPB_WINDOWS) - 1;

    reset(&s); gpb_run(&io, 1, all, &r);                                   // mode 1: reads only
    CHECK(s.writes == 0 && r.writes == 0 && r.gcc == GPB_DONE && r.cc == GPB_SKIPPED_PREREQ && s.smmu_reads == 0);

    reset(&s); gpb_run(&io, 2, all, &r);                                   // mode 2: one enable write
    CHECK(s.writes == 1 && r.enabled_ahb && r.cc == GPB_DONE && (s.gcc[GPB_GCC_GPU_CFG_AHB / 4] & GPB_CBCR_ENABLE));
    CHECK(!r.powered_cx && s.smmu_reads == 0 && r.smmu == GPB_SKIPPED_PREREQ);

    reset(&s); gpb_run(&io, 3, all, &r);                                   // mode 3: CX on, clocks, then the SMMU
    CHECK(r.powered_cx && r.powered_clocks && !r.cx_refused && !r.io_error);
    CHECK(r.smmu == GPB_DONE && r.id0 == 0x12345678 && s.smmu_reads > 0);
    CHECK(!(s.cc[GPB_CC_CX_GDSCR / 4] & GPB_GDSC_SW_COLLAPSE));

    reset(&s); s.cc[GPB_CC_CX_GDSCR / 4] |= GPB_GDSC_HW_CONTROL;            // under HW control: refused, no CX write
    gpb_run(&io, 3, all, &r);
    CHECK(r.cx_refused && !r.powered_cx && s.smmu_reads == 0 && s.cc[GPB_CC_CX_GDSCR / 4] & GPB_GDSC_SW_COLLAPSE);

    reset(&s); s.cx_powers = 0; gpb_run(&io, 3, all, &r);                  // GDSC never reports on: no clocks, no SMMU
    CHECK(!r.powered_cx && !r.powered_clocks && s.smmu_reads == 0);

    reset(&s); s.vote_clock_turns_on = 0; gpb_run(&io, 3, all, &r);        // vote clock stays off: SMMU not read
    CHECK(r.powered_cx && !r.powered_clocks && s.smmu_reads == 0 && r.smmu == GPB_SKIPPED_PREREQ);

    reset(&s); gpb_run(&io, 3, all & ~(1u << GPB_WIN_GPUCC), &r);          // GPUCC not mapped
    CHECK(r.cc == GPB_NOMAP && s.writes == 1 && !r.powered_cx);

    reset(&s); s.fail_window = GPB_WIN_GCC_GPU; gpb_run(&io, 3, all, &r);  // first read fails: nothing else runs
    CHECK(r.io_error && s.writes == 0 && r.cc == GPB_SKIPPED_PREREQ);

    reset(&s); gpb_run(&io, 0, all, &r);
    CHECK(s.writes == 0 && r.gcc == GPB_SKIPPED_MODE);
    {                                                                      // mode 4: pool round trip
        static uint64_t pool[1 << 14];
        struct gpb_pool_result p;
        gpb_pool_test(pool, sizeof pool, &p);
        CHECK(p.words == (1 << 14) && !p.pattern_bad && !p.zero_bad && p.first_bad == ~0ULL && !pool[7]);
    }
    if (failures) return 1;
    puts("PASS: GPU power modes 1-3 and the mode 4 pool round trip: read-only, single AHB write, gated CX power-up, SMMU only after CX and vote clock");
    return 0;
}
