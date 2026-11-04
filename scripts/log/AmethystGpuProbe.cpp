// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opt-in (amethyst-gpu-power=1..3) Adreno 810 power observation, the first step of GPU acceleration.
// Runs rootstockos/UniXNU-asteroids' staged sequence (GpuPowerBoard.h gpb_run, host-tested by
// check-gpu-power.c) on its own thread 10 s after the boot log's first durable round, as asteroids'
// GpuPowerProbe.cpp does: mode 1 reads GCC/GPUCC/SMMU/RBBM state only, mode 2 adds the one write Linux's
// GCC probe makes (gcc_gpu_cfg_ahb_clk enable), mode 3 powers the CX GDSC as the downstream gdsc driver
// does and turns on the GPU SMMU's two GPU-CC clocks. Each block is read only once its prerequisite is
// on: an unclocked read can hang the bus. native_gpu_power_stage names the block being touched, so a hang
// shows where it stopped; the results fill BootLog's existing [gpu uc] rows (native_gpu_uc_text).
// Mode 4 (V32) adds asteroids' GMU pool round trip (gpb_pool_test) over GMU_Pool 0xE6440000 + 32 MiB, which
// only the patched firmware reserves (.scratch/uefi-gmu/patch-gmu-pool.py; pack-log refuses mode 4 without it),
// mapped write-combined as asteroids does (uke's DC ZVA alignment abort on a cached map). Modes 5+ (GMU SMMU
// context, GMU firmware, GX, CP) need Sm7635Platform and NativeAdreno: not in this build.
// Mode 5 (V38) is asteroids' GpuPowerProbe.cpp gmuContext(): once gpb_gmu_context_gate passes (CX on, SMMU
// read, pool clean), the whole kgsl-smmu window is mapped uncached and NativeAdrenoGmuContextBringup builds
// the GMU's SMMU context bank (bank 1, ASID 1, SMR slot 2, stream 5) with its page tables and the five GMU
// payloads in the pool (NativeAdreno 5e2b23e, ArmSmmu4K 83969cd; compile-gpu.py). The GMU stays halted.
// V39: the GMU bank's FSR (write 1 to clear) is cleared before the bring-up; V38 read 0x400 after it.
// Modes 9-11 (V41): asteroids' gmuColdStart and gpuGx. 9 loads the GMU firmware to HFI ready
// (ShengGmuLoadAndStartHfi), 10 is the full cold start with the HFI startup messages (ShengGmuColdStart), 11 votes
// a GPU level over HFI and asks the GMU for GX with OOB GPU_SET (gpb_gx_on). Each runs only when the one before
// succeeded. GMU window, RSCC (mode 8's maps), the GPU block and CX_MISC match the live DT.
// Modes 7-8 (V40): asteroids' gmuClocksOn (the GMU clocks on, GMU in reset) and gmuState (read the GMU/RSCC state).
// Writes are now limited to asteroids' allow-list per mode (gpb_mode7_writable from mode 7).
// Mode 6 (V39, read only) is asteroids' gmuPrerequisites(): the GMU clock tree (gpb_read_gmu_clocks), then the
// AOP command DB (aop_cmd_db_region@81c60000, 128 KiB, uncached read-only map, aligned 32-bit reads) parsed by
// Sm7635Platform's ShengGmuCommandDbInputs into the HFI startup table (ShengGmuBuildStartup). No GMU register.
#include <IOKit/IODeviceMemory.h>
#include <IOKit/IOLib.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <string.h>
#include "NativeAdrenoGmuContext.h"
#include "NativeAdrenoSha256.h"
extern "C" {
#include "ShengGmu.h"
#include "ShengGmuSource.h"     // generated: GMU windows and registers (prepare-asteroids-gmu.py)
#include "ShengGmuFirmware.h"   // generated: amethyst's gen80300_gmu.bin as an array (equal to asteroids' pin)
#include "ShengGmuCommandDb.h"
#include "ShengGmuCommandDbSource.h"
#include "ShengGmuVotes.h"
}

extern "C" {
#include "GpuPowerBoard.h"
volatile uint32_t native_gpu_power_stage;      // BootLog [gpu uc] stage (weak stand-in replaced)
uint32_t native_gpu_uc[16];
char native_gpu_uc_text[4][120];
void amethyst_gpu_probe_start(void);
// NativeAdrenoMemory::release asks a kernel patch amethyst's kernel does not carry which task maps a page;
// asteroids' GpuPowerProbe.cpp gives the same weak stand-in (no user BOs exist before mode 17).
__attribute__((weak)) unsigned int native_pmap_user_mappings(ppnum_t, void **, uint64_t *, char *) {return 0;}
}

namespace {
enum { kMaxMode = 11 };
// The first GPB_WINDOWS are mapped at start; the GMU and RSCC windows (live DT: gmu@3d37000 + 0x68000, the kgsl
// node's rscc 0x3d50000 + 0x10000) only by mode 8.
const uint64_t kPhys[GPB_ALL_WINDOWS] = {GPB_GCC_GPU_PAGE, GPB_GCC_VOTE_PAGE, GPB_GPUCC_BASE, GPB_SMMU_BASE, GPB_GPU_BASE,
                                         GPB_GMU_BASE, GPB_RSCC_BASE};
const uint64_t kLen[GPB_ALL_WINDOWS] = {0x1000, 0x1000, GPB_GPUCC_SIZE, GPB_SMMU_GR0_SIZE, GPB_GPU_WINDOW, GPB_GMU_SIZE,
                                        GPB_RSCC_SIZE};
IOMemoryMap *gMaps[GPB_ALL_WINDOWS];
uintptr_t gVa[GPB_ALL_WINDOWS];
bool gClocksOn;   // mode 7's writes are allowed only while gpb_gmu_clocks_on runs
unsigned gMode;
gpb_result gResult;
gpb_pool_result gPool;
int rd(void *,unsigned w,uint32_t off,uint32_t *out);
int wr(void *,unsigned w,uint32_t off,uint32_t v);
void stage(void *,unsigned w);
void delayUs(void *,unsigned us);
int gPoolState=-1;   // -1 not run, 0 done, 1 map failed
NativeAdrenoGmuContextReport gGmu;
unsigned gGmuGate=99;   // 99 not run
IOReturn gGmuStatus;
uint32_t gGmuGfsr,gGmuFsr,gGmuFsrBefore;
NativeAdrenoGmuPayload gPayloads[5];
IOMemoryMap *gSmmuMap;  // the context bank points at tables in the pool for the rest of the boot
void gmuProgress(uint32_t step) {native_gpu_power_stage = 0x50 + step;}
void gmuContext() {
    gGmuGate = gpb_gmu_context_gate(&gResult, &gPool);
    if (gGmuGate) return;
    native_gpu_power_stage = 0x50;
    IODeviceMemory *memory = IODeviceMemory::withRange(GPB_SMMU_BASE, GPB_SMMU_WINDOW);
    gSmmuMap = memory ? memory->map(kIOMapAnywhere | kIOMapInhibitCache) : nullptr;
    if (memory) memory->release();
    if (!gSmmuMap) {gGmuGate = 98; return;}
    volatile uint8_t *va = reinterpret_cast<volatile uint8_t *>(gSmmuMap->getVirtualAddress());
    volatile uint32_t *fsr = reinterpret_cast<volatile uint32_t *>(va + gpb_smmu_cb_offset(gResult.id1, GPB_GMU_BANK) + GPB_SMMU_CB_FSR);
    gGmuFsrBefore = *fsr;
    if (gGmuFsrBefore) {*fsr = gGmuFsrBefore; __asm__ volatile("dsb sy" ::: "memory");}   // write 1 to clear
    gGmuStatus = NativeAdrenoGmuContextBringup(va, GPB_SMMU_WINDOW, GPB_GMU_BANK, GPB_GMU_ASID, GPB_GMU_SLOT,
                                               GPB_GMU_SID, gmuProgress, &gGmu);
    gGmuGfsr = *reinterpret_cast<volatile uint32_t *>(va + GPB_SMMU_GFSR);
    gGmuFsr = *reinterpret_cast<volatile uint32_t *>(va + gpb_smmu_cb_offset(gResult.id1, GPB_GMU_BANK) + GPB_SMMU_CB_FSR);
    if (!gGmuStatus) NativeAdrenoGmuContextPayloads(gPayloads);
    native_gpu_power_stage = gGmuStatus ? 0x5f : 0x57;
}
// Mode 6
alignas(8) uint8_t gCmdDb[SHENG_GMU_COMMAND_DB_BYTES];
ShengGmuVoteInputs gVoteInputs;
AdrenoHfiStartupConfig gStartup;
gpb_gmu_clocks gClk;
int gClkIo;
uint32_t gDb[4] = {0, 0, 99, 99};   // version, magic, parse, build
void gmuPrerequisites() {
    gpb_io io{nullptr, rd, wr, stage, delayUs};
    gpb_result scratch = gResult;
    native_gpu_power_stage = 0x60;
    gpb_read_gmu_clocks(&io, &scratch, &gClk);
    gClkIo = scratch.io_error;
    native_gpu_power_stage = 0x61;
    IODeviceMemory *memory = IODeviceMemory::withRange(SHENG_GMU_COMMAND_DB_BASE, SHENG_GMU_COMMAND_DB_BYTES);
    IOMemoryMap *map = memory ? memory->map(kIOMapAnywhere | kIOMapInhibitCache | kIOMapReadOnly) : nullptr;
    if (memory) memory->release();
    if (!map) {gDb[2] = 98; return;}
    const volatile uint32_t *db = reinterpret_cast<const volatile uint32_t *>(map->getVirtualAddress());
    for (uint32_t i = 0; i < SHENG_GMU_COMMAND_DB_BYTES / 4; ++i) {const uint32_t w = db[i]; memcpy(gCmdDb + 4 * i, &w, 4);}
    map->release();
    memcpy(&gDb[0], gCmdDb, 4); memcpy(&gDb[1], gCmdDb + 4, 4);
    gVoteInputs = {};
    gVoteInputs.chip_id = 0x44010000u;   // A810; sku_present 0: the bin listing 0 ([2, 0], also amethyst's AB = 2)
    gDb[2] = ShengGmuCommandDbInputs(gCmdDb, SHENG_GMU_COMMAND_DB_BYTES, gDb[0], &gVoteInputs);
    if (gDb[2] == ShengGmuDbSuccess) gDb[3] = ShengGmuBuildStartup(&gVoteInputs, &gStartup);
    const AdrenoHfiStartupConfig &c = gStartup;
    IOLog("AmethystGpu: gmuclk pll0 mode=%08x op=%x st=%08x l=%08x a=%08x user=%08x io=%d\n", gClk.pll0_mode,
          gClk.pll0_opmode, gClk.pll0_status, gClk.pll0_l, gClk.pll0_alpha, gClk.pll0_user, gClkIo);
    IOLog("AmethystGpu: gmuclk rcg gmu=%08x/%08x hub=%08x/%08x div=%08x ff=%08x/%08x\n", gClk.gmu_cmd, gClk.gmu_cfg,
          gClk.hub_cmd, gClk.hub_cfg, gClk.hub_div, gClk.ff_cmd, gClk.ff_cfg);
    IOLog("AmethystGpu: gmuclk br gmu=%08x cxo=%08x ahb=%08x hubcx=%08x hubaon=%08x memnoc=%08x\n", gClk.branch[0],
          gClk.branch[1], gClk.branch[2], gClk.branch[3], gClk.branch[4], gClk.branch[5]);
    IOLog("AmethystGpu: cmddb v=%x magic=%08x parse=%u build=%d gmxc=%u aux gfx=%u cx=%u mx=%u sh0=%u mc0=%u acv=%u cn0=%x\n",
          gDb[0], gDb[1], gDb[2], (int)gDb[3], gVoteInputs.gmxc_present, unsigned(gVoteInputs.gfx.bytes),
          unsigned(gVoteInputs.cx.bytes), unsigned(gVoteInputs.mx.bytes), unsigned(gVoteInputs.sh0.aux.bytes),
          unsigned(gVoteInputs.mc0.aux.bytes), unsigned(gVoteInputs.acv.aux.bytes), gVoteInputs.cn0_address);
    if (!gDb[3]) {
        for (unsigned i = 0; i < c.gpu_count && i < 16; ++i)
            IOLog("AmethystGpu: hfi gpu%u %lluk vote=%08x dep=%08x\n", i, (unsigned long long)(c.gpu[i].hz / 1000),
                  c.gpu[i].vote, c.gpu_dependency[i]);
        for (unsigned i = 0; i < c.gmu_count && i < 4; ++i)
            IOLog("AmethystGpu: hfi gmu%u %lluk vote=%08x\n", i, (unsigned long long)(c.gmu[i].hz / 1000), c.gmu[i].vote);
    }
    native_gpu_power_stage = 0x6f;
}
// Mode 7 (V40): asteroids' gmuClocksOn: gpb_gmu_clocks_on from mode 6's snapshot (GPLL0 vote, PLL0 700 MHz, hub
// 200 MHz, GMU 350 MHz, gpu_cc_ahb), then the snapshot again. The GMU stays in reset.
gpb_gmu_clock_on gOn;
gpb_gmu_clocks gClkAfter;
void gmuClocksOn() {
    gpb_io io{nullptr, rd, wr, stage, delayUs};
    gpb_result scratch = gResult;
    native_gpu_power_stage = 0x70;
    gClocksOn = true;
    gpb_gmu_clocks_on(&io, &scratch, &gClk, &gOn);
    gClocksOn = false;
    native_gpu_power_stage = 0x71;
    gpb_read_gmu_clocks(&io, &scratch, &gClkAfter);
    static const char *const kFail[] = {"ok", "refused", "timeout", "io"};
    IOLog("AmethystGpu: gmuclk on step=%u %s writes=%u running=%u lock=%u hub=%u/%u gmu=%u/%u\n", gOn.step,
          gOn.failed < 4 ? kFail[gOn.failed] : "?", gOn.writes, gOn.pll_was_running, gOn.lock_polls, gOn.hub_root_polls,
          gOn.hub_polls, gOn.gmu_root_polls, gOn.gmu_polls);
    IOLog("AmethystGpu: gmuclk on vote=%08x pll0=%08x/%x/%08x hub=%08x/%08x gmu=%08x/%08x ahb=%08x\n", gOn.vote,
          gOn.pll0_mode, gOn.pll0_opmode, gOn.pll0_user, gOn.hub_cmd, gOn.hub_cfg, gOn.gmu_cmd, gOn.gmu_cfg, gOn.ahb);
    IOLog("AmethystGpu: gmuclk after pll0 mode=%08x st=%08x l=%08x rcg gmu=%08x/%08x hub=%08x/%08x br %08x %08x %08x %08x %08x %08x\n",
          gClkAfter.pll0_mode, gClkAfter.pll0_status, gClkAfter.pll0_l, gClkAfter.gmu_cmd, gClkAfter.gmu_cfg,
          gClkAfter.hub_cmd, gClkAfter.hub_cfg, gClkAfter.branch[0], gClkAfter.branch[1], gClkAfter.branch[2],
          gClkAfter.branch[3], gClkAfter.branch[4], gClkAfter.branch[5]);
    native_gpu_power_stage = 0x72;
}
// Mode 8 (V40): asteroids' gmuState: map the GMU and RSCC windows uncached and read the cold start's
// preconditions (gpb_read_gmu_state). Nothing is written.
gpb_gmu_state gState;
void gmuState() {
    const int clocks_ok = gOn.step == 8 && !gOn.failed;
    native_gpu_power_stage = 0x80;
    for (unsigned w = GPB_WINDOWS; w < GPB_ALL_WINDOWS && clocks_ok; ++w) {
        IODeviceMemory *memory = IODeviceMemory::withRange(kPhys[w], kLen[w]);
        if (!memory) continue;
        gMaps[w] = memory->map(kIOMapAnywhere | kIOMapInhibitCache);
        memory->release();
        if (gMaps[w]) gVa[w] = gMaps[w]->getVirtualAddress();
    }
    if (clocks_ok && (!gVa[GPB_WIN_GMU] || !gVa[GPB_WIN_RSCC])) {IOLog("AmethystGpu: gmu map failed\n"); return;}
    gpb_io io{nullptr, rd, wr, stage, delayUs};
    gpb_result scratch = gResult;
    gpb_read_gmu_state(&io, &scratch, clocks_ok, &gState);
    native_gpu_power_stage = 0x81;
    const uint32_t *v = gState.v;
    IOLog("AmethystGpu: gmu gate=%s reset=%08x hfi=%08x ao=%08x cm3=%08x rscc=%08x io=%d\n", gpb_gmu_state_text(gState.gate),
          v[0], v[1], v[2], v[3], gState.rscc_busy, gState.io_error);
    if (gState.done && !gState.io_error) {
        IOLog("AmethystGpu: gmu init=%08x boot=%08x cfg=%08x gen8=%08x gen9=%08x gen10=%08x fence=%08x\n", v[4], v[5], v[6],
              v[7], v[8], v[9], v[10]);
        IOLog("AmethystGpu: gmu itcm0=%08x dtcm_ver=%08x ao=%08x:%08x then %08x (+%u)\n", v[11], v[12], v[14], v[13], v[15],
              v[15] - v[13]);
    }
    native_gpu_power_stage = 0x82;
}
// Modes 9-10
int gmuSha256(void *, const void *bytes, size_t length, uint8_t digest[32]) {
    NativeAdrenoSha256::hash(static_cast<const uint8_t *>(bytes), length, digest);
    return 0;
}
int gmuNowUs(void *, uint64_t *out) {
    uint64_t ns = 0;
    absolutetime_to_nanoseconds(mach_absolute_time(), &ns);
    *out = ns / 1000;
    return 0;
}
int gmuWaitUs(void *, unsigned us) {IODelay(us); return 0;}
const char *const kShengStatus[] = {"ok", "argument", "firmware", "busy", "io", "state-mismatch", "timeout", "clock",
                                    "poisoned", "fault", "protocol"};
ShengGmuMmio gColdMmio;       // the cold start's windows and device; mode 11 keeps using them
ShengGmuDevice gColdDevice;
ShengGmuStartupResult gStart;
uint32_t gStartStatus = 0xffffffffu;
uint32_t gAfter[4];
IOMemoryMap *gExtraMap[2], *gPayloadMap[5];   // kept: the GMU uses them after the cold start
void gmuColdStart() {
    native_gpu_power_stage = 0x90;
    NativeAdrenoGmuPayload payloads[5];
    const bool ready = gState.done && !gState.gate && NativeAdrenoGmuContextPayloads(payloads) == kIOReturnSuccess;
    const bool table = gDb[1] == 0x0c0330dbu && gDb[2] == 0 && gDb[3] == 0;
    if (!ready || (gMode >= 10 && !table)) {
        IOLog("AmethystGpu: gmuboot skipped: gate=%s context=%d table=%d\n", gpb_gmu_state_text(gState.gate), ready, table);
        return;
    }
    ShengGmuMmio &mmio = gColdMmio;
    ShengGmuDevice &device = gColdDevice;
    const unsigned extra[2] = {ShengGmuGpu, ShengGmuCxMisc};
    for (unsigned i = 0; i < 2; ++i) {
        const unsigned w = extra[i];
        IODeviceMemory *memory = IODeviceMemory::withRange(sheng_gmu_window_physical[w], sheng_gmu_window_bytes[w]);
        if (memory) {gExtraMap[i] = memory->map(kIOMapAnywhere | kIOMapInhibitCache); memory->release();}
        if (!gExtraMap[i]) {IOLog("AmethystGpu: gmuboot map of window %u failed\n", w); return;}
        mmio.windows[w] = {reinterpret_cast<volatile unsigned char *>(gExtraMap[i]->getVirtualAddress()),
                           sheng_gmu_window_physical[w], static_cast<size_t>(sheng_gmu_window_bytes[w])};
    }
    mmio.windows[ShengGmuCore] = {reinterpret_cast<volatile unsigned char *>(gVa[GPB_WIN_GMU]), GPB_GMU_BASE,
                                  static_cast<size_t>(GPB_GMU_SIZE)};
    mmio.windows[ShengGmuRscc] = {reinterpret_cast<volatile unsigned char *>(gVa[GPB_WIN_RSCC]), GPB_RSCC_BASE,
                                  static_cast<size_t>(GPB_RSCC_SIZE)};
    const ShengGmuStatus bind = ShengGmuBindMmio(&mmio, &device.io);
    if (bind != ShengGmuSuccess) {IOLog("AmethystGpu: gmuboot bind refused (%u)\n", bind); gStartStatus = 100 + bind; return;}
    ShengGmuMemory *memory[5];
    ShengGmuConfig config{};
    memory[0] = &config.icache; memory[1] = &config.dummy; memory[2] = &config.debug; memory[3] = &config.log;
    memory[4] = &config.hfi;
    for (unsigned i = 0; i < 5; ++i) {
        IODeviceMemory *range = IODeviceMemory::withRange(payloads[i].physical, payloads[i].bytes);
        if (range) {gPayloadMap[i] = range->map(kIOMapAnywhere | kIOMapWriteCombineCache); range->release();}
        if (!gPayloadMap[i]) {IOLog("AmethystGpu: gmuboot payload %u map failed\n", i); return;}
        *memory[i] = {reinterpret_cast<volatile uint32_t *>(gPayloadMap[i]->getVirtualAddress()),
                      static_cast<size_t>(payloads[i].bytes), payloads[i].iova};
    }
    config.chip_id = SHENG_GMU_CHIP_ID;
    config.firmware = sheng_gmu_firmware;
    config.firmware_bytes = sizeof sheng_gmu_firmware;
    config.poll_budget = 100;
    device.services = {nullptr, gmuSha256, gmuNowUs, gmuWaitUs};
    ShengGmuStatus status;
    IOLog("AmethystGpu: gmuboot %s: firmware %u bytes, hfi@%llx log@%llx\n",
          gMode >= 10 ? "cold start with HFI startup" : "load to HFI ready", static_cast<unsigned>(sizeof sheng_gmu_firmware),
          static_cast<unsigned long long>(config.hfi.iova), static_cast<unsigned long long>(config.log.iova));
    native_gpu_power_stage = 0x91;
    if (gMode >= 10) {
        const ShengGmuStartupLimits limits = {1000, 100, 100000, 1000000};   // 100 ms per command, 1 s in all
        status = ShengGmuColdStart(&device, &config, &gStartup, &limits, &gStart);
    } else {
        gStart = ShengGmuStartupResult{};
        status = ShengGmuLoadAndStartHfi(&device, &config, &gStart.load);
    }
    gStartStatus = status;
    native_gpu_power_stage = 0x92;
    const ShengGmuResult &l = gStart.load;
    IOLog("AmethystGpu: gmuboot %s phase=%u hash=%u reset_ok=%u fw_ready=%u hfi_ready=%u init=%08x hfi=%08x\n",
          status < 11 ? kShengStatus[status] : "?", l.phase, l.complete_hash_verified, l.reset_verified,
          l.firmware_ready_observed, l.hfi_ready_observed, l.firmware_init_result, l.hfi_status);
    IOLog("AmethystGpu: gmuboot writes=%u reads=%u polls=%u ao=%08x cm3=%08x\n", l.write_attempts, l.read_attempts,
          l.poll_reads, l.ao_fault_status, l.cm3_fault_status);
    if (gMode >= 10) {
        const AdrenoHfiStartupResult &p = gStart.protocol;
        IOLog("AmethystGpu: gmuboot hfi startup=%u stage=%u acked=%08x sent=%u polls=%u fw_err=%08x hdr=%08x t=%lluus health=%u\n",
              gStart.protocol_status, p.stage, p.acknowledged_stages, p.published_commands, p.poll_reads, p.firmware_error,
              p.wire_header, static_cast<unsigned long long>(p.elapsed_us), gStart.health_status);
    }
    const uint32_t regs[4] = {GPB_GMU_CM3_SYSRESET, GPB_GMU_CM3_FW_INIT_RESULT, GPB_GMU_HFI_CTRL_STATUS, GPB_GMU_AO_COUNTER_L};
    for (unsigned i = 0; i < 4; ++i) rd(nullptr, GPB_WIN_GMU, regs[i], &gAfter[i]);
    IOLog("AmethystGpu: gmuboot after reset=%08x init=%08x hfi=%08x ao=%08x log %08x %08x %08x %08x\n", gAfter[0],
          gAfter[1], gAfter[2], gAfter[3], config.log.cpu[0], config.log.cpu[1], config.log.cpu[2], config.log.cpu[3]);
    native_gpu_power_stage = 0x93;
}
// Mode 11: GX through the cold start's MMIO adapter; GPU-block writes are not allowed in this mode.
unsigned gxWindow(unsigned w) {return w == GPB_WIN_GMU ? ShengGmuCore : ShengGmuGpu;}
int gxRead(void *, unsigned w, uint32_t off, uint32_t *out) {
    ShengGmuIo &io = gColdDevice.io;
    return io.read32(io.cookie, gxWindow(w), off, out);
}
int gxWrite(void *, unsigned w, uint32_t off, uint32_t value) {
    ShengGmuIo &io = gColdDevice.io;
    if (w != GPB_WIN_GMU) return 1;
    return io.write32(io.cookie, gxWindow(w), off, value) || io.barrier(io.cookie);
}
int hfiWaitOnce(void *) {IODelay(100); return 0;}
int gxVote(void *, uint32_t perf, uint32_t bw, uint32_t *firmware_error) {
    const uint32_t vote[3] = {1, perf, bw};   // a6xx_hfi_gx_bw_perf_vote_cmd: ack_type 1 (blocking), freq, bw
    uint32_t out[16];
    size_t count = 0;
    return AdrenoHfiExchange(&gColdDevice.hfi, 30, vote, 3, 1000, hfiWaitOnce, out, 16, &count, firmware_error);
}
void gxDelay(void *, unsigned us) {IODelay(us);}
gpb_gx gGx;
unsigned gGxPerf, gGxBw;
uint32_t gGxGdscr;
void gpuGx() {
    native_gpu_power_stage = 0xa0;
    if (gStartStatus != ShengGmuSuccess) return;
    unsigned level = 6;   // asteroids' default: 763 MHz
    PE_parse_boot_argn("amethyst-gpu-level", &level, sizeof level);
    gGxPerf = ShengGmuPerfVote(level, &gGxBw);
    native_gpu_power_stage = 0xa1;
    gpb_gx_io io{nullptr, gxRead, gxWrite, gxVote, gxDelay};
    gpb_gx_on(&io, gGxPerf, gGxBw, &gGx);
    native_gpu_power_stage = 0xa2;
    static const char *const kFail[] = {"ok", "vote", "no-ack", "io", "gx-off"};
    IOLog("AmethystGpu: gx perf %u bw %u (%lluk) %s step=%u vote=%u fw=%08x oob=%u info=%08x gx %08x>%08x later=%08x\n",
          gGxPerf, gGxBw, static_cast<unsigned long long>(gStartup.gpu[gGxPerf].hz / 1000), gGx.failed < 5 ? kFail[gGx.failed] : "?",
          gGx.step, gGx.vote_status, gGx.fw_error, gGx.oob_polls, gGx.info_after, gGx.gx_before, gGx.gx_after, gGx.gx_later);
    if (gVa[GPB_WIN_GPUCC]) gGxGdscr = *reinterpret_cast<volatile uint32_t *>(gVa[GPB_WIN_GPUCC] + GPB_CC_GX_GDSCR);
    IOLog("AmethystGpu: gx rbbm=%08x gfx=%08x br=%08x gbif_halt=%08x gx_gdscr=%08x writes=%u\n", gGx.rbbm, gGx.gfx,
          gGx.gfx_br, gGx.gbif_halt, gGxGdscr, gGx.writes);
    native_gpu_power_stage = gGx.failed ? 0xaf : 0xa7;
}

int rd(void *,unsigned w,uint32_t off,uint32_t *out) {
    if (w >= GPB_ALL_WINDOWS || !gVa[w] || off >= kLen[w] || (off & 3)) return -1;
    uint32_t v;
    __asm__ volatile("ldr %w0, [%1]" : "=r"(v) : "r"(gVa[w] + off) : "memory");
    *out = v;
    return 0;
}
int wr(void *,unsigned w,uint32_t off,uint32_t v) {
    if (w >= GPB_ALL_WINDOWS || !gVa[w] || off >= kLen[w] || (off & 3)) return -1;
    // asteroids' allow-list: each mode writes only the registers it owns.
    const bool gcc = gMode >= 2 && w == GPB_WIN_GCC_GPU && off == GPB_GCC_GPU_CFG_AHB;
    const bool cc = gMode >= 3 && w == GPB_WIN_GPUCC &&
                    (off == GPB_CC_CX_GDSCR || off == GPB_CC_AHB_CLK || off == GPB_CC_SMMU_VOTE_CLK);
    const bool clocks = gMode >= 7 && gClocksOn && gpb_mode7_writable(w, off);
    if (!(gcc || cc || clocks)) return -1;
    __asm__ volatile("str %w0, [%1]\n\tdsb sy" :: "r"(v), "r"(gVa[w] + off) : "memory");
    return 0;
}
void stage(void *,unsigned w) {native_gpu_power_stage = 0x10 + w;}   // 0x10..0x14 a window, 0x30 the CX power-up
void delayUs(void *,unsigned us) {IODelay(us);}

void report() {
    const gpb_result &r = gResult;
    if (r.mode >= 7) {
        static const char *const kFail[] = {"ok", "refused", "timeout", "io"};
        snprintf(native_gpu_uc_text[0], 120, "a810 m%u io=%d | m7 clk s%u %s w=%u lock=%u pll0=%08x gmu=%08x | m8 %s reset=%08x cm3=%08x rscc=%x",
                 r.mode, r.io_error, gOn.step, gOn.failed < 4 ? kFail[gOn.failed] : "?", gOn.writes, gOn.lock_polls,
                 gClkAfter.pll0_mode, gClkAfter.gmu_cmd, r.mode >= 8 ? gpb_gmu_state_text(gState.gate) : "-", gState.v[0],
                 gState.v[3], gState.rscc_busy);
    } else
    snprintf(native_gpu_uc_text[0], 120, "a810 m%u gcc=%s cc=%s smmu=%s gpu=%s io=%d wr=%u ahb_set=%u",
             r.mode, gpb_state_text(r.gcc), gpb_state_text(r.cc), gpb_state_text(r.smmu), gpb_state_text(r.gpu),
             r.io_error, r.writes, r.enabled_ahb);
    if (r.mode >= 6)
        snprintf(native_gpu_uc_text[1], 120, "m6 cmddb v=%x magic=%08x parse=%u build=%d gmxc=%u gfx=%u cx=%u mx=%u hfi gpu=%u gmu=%u bw=%u",
                 gDb[0], gDb[1], gDb[2], (int)gDb[3], gVoteInputs.gmxc_present, unsigned(gVoteInputs.gfx.bytes),
                 unsigned(gVoteInputs.cx.bytes), unsigned(gVoteInputs.mx.bytes), gStartup.gpu_count, gStartup.gmu_count,
                 gStartup.bandwidth_count);
    else
    snprintf(native_gpu_uc_text[1], 120, "gcc ahb=%08x>%08x mem=%08x dvm=%08x axi=%08x vote=%08x",
             r.cfg_ahb, r.cfg_ahb_after, r.memnoc, r.dvm, r.axi, r.pll_vote);
    if (r.mode >= 9) {
        static const char *const kGx[] = {"ok", "vote", "no-ack", "io", "gx-off"};
        snprintf(native_gpu_uc_text[2], 120, "m9 gmuboot %s ph%u fw%u hfi%u init=%x | hfi st%u acked=%x | m11 gx %s s%u gx=%08x gdscr=%08x",
                 gStartStatus < 11 ? kShengStatus[gStartStatus] : (gStartStatus == 0xffffffffu ? "-" : "?"), gStart.load.phase,
                 gStart.load.firmware_ready_observed, gStart.load.hfi_ready_observed, gStart.load.firmware_init_result,
                 gStart.protocol_status, gStart.protocol.acknowledged_stages,
                 r.mode >= 11 && gGx.step ? (gGx.failed < 5 ? kGx[gGx.failed] : "?") : "-", gGx.step, gGx.gx_later, gGxGdscr);
    } else if (r.mode >= 5 && gGmuGate)
        snprintf(native_gpu_uc_text[2], 120, "m5 gmuctx skipped: %s (%u) id1=%08x", gGmuGate == 98 ? "SMMU map failed" :
                 gpb_gmu_gate_text(gGmuGate), gGmuGate, r.id1);
    else if (r.mode >= 5)
        snprintf(native_gpu_uc_text[2], 120, "m5 gmuctx s%u st=%x k%u/%u spans=%u mm=%03x ttbr0=%llx sctlr=%x gfsr=%x fsr=%x hfi@%llx",
                 gGmu.step, (unsigned)gGmuStatus, gGmu.program_status, gGmu.route_status, gGmu.spans, gGmu.mismatch,
                 (unsigned long long)gGmu.ttbr0, gGmu.sctlr, gGmuGfsr, gGmuFsr, (unsigned long long)gPayloads[4].iova);
    else
    snprintf(native_gpu_uc_text[2], 120, "cc cx=%08x hw=%08x gx=%08x ahb=%08x smmu=%08x gmu=%08x",
             r.cx_gdscr, r.cx_hwctrl, r.gx_gdscr, r.cc_ahb, r.cc_smmu, r.cc_gmu);
    if (r.mode >= 6)
        snprintf(native_gpu_uc_text[3], 120, "m6 clk pll0 %08x/%08x l=%x rcg gmu=%08x hub=%08x br gmu=%u cxo=%u ahb=%u hub=%u%u io=%d fsr0=%x",
                 gClk.pll0_mode, gClk.pll0_status, gClk.pll0_l, gClk.gmu_cmd, gClk.hub_cmd, gpb_branch_digit(gClk.branch[0]),
                 gpb_branch_digit(gClk.branch[1]), gpb_branch_digit(gClk.branch[2]), gpb_branch_digit(gClk.branch[3]),
                 gpb_branch_digit(gClk.branch[4]), gClkIo, gGmuFsrBefore);
    else if (r.mode >= 4)
        snprintf(native_gpu_uc_text[3], 120, "m4 pool %s words=%llu bad=%llu zero_bad=%llu first=%llx | cx>%08x pw%u%u rbbm=%08x",
                 gPoolState==0?"ok":gPoolState==1?"MAPFAIL":"skip", gPool.words, gPool.pattern_bad, gPool.zero_bad,
                 gPool.first_bad, r.cx_after, r.powered_cx, r.powered_clocks, r.rbbm_status);
    else if (r.mode >= 3)
        snprintf(native_gpu_uc_text[3], 120, "m3 cx %08x>%08x>%08x t%u ref%u pw%u%u smmu id0=%08x id1=%08x rbbm=%08x",
                 r.cx_before, r.cx_init, r.cx_after, r.cx_tries, r.cx_refused, r.powered_cx, r.powered_clocks,
                 r.id0, r.id1, r.rbbm_status);
    else
        snprintf(native_gpu_uc_text[3], 120, "smmu %08x %08x %08x %08x gfsr=%08x rbbm=%08x",
                 r.scr0, r.id0, r.id1, r.id2, r.gfsr, r.rbbm_status);
    for (unsigned i = 0; i < 4; ++i) IOLog("AmethystGpu: %s\n", native_gpu_uc_text[i]);
}

void probeThread(void *,wait_result_t) {
    IOSleep(10000);   // after the console and the late probes, as asteroids does
    native_gpu_power_stage = 2;
    unsigned have = 0;
    for (unsigned w = 0; w < GPB_WINDOWS; ++w) {
        IODeviceMemory *memory = IODeviceMemory::withRange(kPhys[w], kLen[w]);
        if (!memory) continue;
        gMaps[w] = memory->map(kIOMapAnywhere | kIOMapInhibitCache);   // kept for the boot
        memory->release();
        if (gMaps[w]) {gVa[w] = gMaps[w]->getVirtualAddress(); have |= 1u << w;}
    }
    snprintf(native_gpu_uc_text[0], 120, "a810 m%u windows %02x: running", gMode, have);
    gpb_io io{nullptr, rd, wr, stage, delayUs};
    gpb_run(&io, gMode, have, &gResult);
    if (gMode >= 4) {
        native_gpu_power_stage = 0x40;
        IODeviceMemory *memory = IODeviceMemory::withRange(GPB_GMU_POOL_BASE, GPB_GMU_POOL_BYTES);
        IOMemoryMap *pool = memory ? memory->map(kIOMapAnywhere | kIOMapWriteCombineCache) : nullptr;
        if (memory) memory->release();
        if (pool) {
            gpb_pool_test(reinterpret_cast<volatile uint64_t *>(pool->getVirtualAddress()), GPB_GMU_POOL_BYTES, &gPool);
            gPoolState = 0;
            pool->release();
        } else gPoolState = 1;
    }
    if (gMode >= 5) gmuContext();
    if (gMode >= 6 && !gGmuGate && !gGmuStatus) gmuPrerequisites();
    if (gMode >= 7 && !gDb[2] && !gDb[3]) gmuClocksOn();
    if (gMode >= 8 && gOn.step == 8 && !gOn.failed) gmuState();
    if (gMode >= 9 && gState.done && !gState.gate) gmuColdStart();
    if (gMode >= 11) gpuGx();
    native_gpu_power_stage = 3;
    report();
    native_gpu_power_stage = 4;
}
}

extern "C" void amethyst_gpu_probe_start(void) {
    static bool started;
    unsigned mode = 0;
    if (started || !PE_parse_boot_argn("amethyst-gpu-power", &mode, sizeof mode) || !mode) return;
    started = true;
    if (mode > kMaxMode) {
        snprintf(native_gpu_uc_text[0], 120, "a810: amethyst-gpu-power=%u refused; this build has modes 1-%u", mode, kMaxMode);
        native_gpu_power_stage = 0xbad;
        return;
    }
    gMode = mode;
    native_gpu_power_stage = 1;
    thread_t thread = nullptr;
    if (kernel_thread_start(probeThread, nullptr, &thread) == KERN_SUCCESS) thread_deallocate(thread);
    else native_gpu_power_stage = 0xdead;
}
