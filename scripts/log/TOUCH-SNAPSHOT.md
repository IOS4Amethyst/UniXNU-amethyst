# Read-only touch-state probe

`AmethystTouchSnapshot.cpp` is an opt-in snapshot, not a touch driver. It reads
GCC wrap0 clock status/vote and TLMM GPIO0/1/2/3/70/87. If any selected clock
reports CLK_OFF, it skips SE0 entirely. Otherwise it reads only SE0 status,
FIFO-disable, firmware revision and hardware geometry. It never programs mux,
reset, clocks, rails, interrupts, SMMU or SPI commands. Read-only mapping does
not make arbitrary hardware reads intrinsically safe; source addresses and
clock predicate still need device verification.

Enable argument: `amethyst-touch-snapshot=1`, absent/off by default.
The object is linked into the touch-snapshot candidate below. BootLog's normal
writer calls it after a successful durable round and GPT consistency check;
the probe runs once per boot. No early startup constructor or panic-time call.
Existing source inspection check passes.

Host evidence: recorded IOPlatformExpert.cpo.json ARM64 compile exit0;
check-touch-snapshot.py PASS; no MMIO executed on host. Hardware untested.
Candidate: staging/amethyst-touch-snapshot/Mu-amethyst-touch-snapshot.img,
SHA256618a675559096c133247965377a3c9d27509df49848c1c99289c09be056f4b61,
UUID DF975331-5018-3E71-97A8-9063AD3E19F4. Link exit0/no undefined symbols,
mandatory prelink and pack PASS. Independent extraction: only kernel and
amethyst-touch-snapshot=1 differ from UI-working scale2; seed/ADT/trustcache
unchanged. Hardware untested; this does not implement touch input. Inspect
pulled AmethystTouch lines before selecting initialization or DMA takeover.

## First snapshot run: evidence retention failure

result-touch-snapshot-1/untagged-2d040900 completed122 rounds, uptime251s,
errors0/GPT1/1. Retained message tail starts late, with no AmethystTouch lines.
The one-time evidence may have wrapped; this does not establish probe execution
or its result. v2 retains up to24 immutable128-byte rows with release/acquire
publication and includes them at the start of every periodic slot. No MMIO
addresses, access permissions, enable argument or clock policy changed.

v2 image staging/amethyst-touch-snapshot-v2/Mu-amethyst-touch-snapshot-v2.img,
80322560 bytes, SHA256
73aa8e334524dfa4f358bba224709c1048f9220ee9fabc320945b28cfab6e084,
UUID FE105B2F-5700-3770-AA97-4E5E924EE0F5. Snapshot/log compilation,
read-only source check, link exit0/no undefined symbols, prelink and pack PASS.
Hardware untested. No touch input or button handling is implemented by this probe.

User additionally reports physical side buttons do not work in Apple UI.
Prior Android inventory identifies gpio-keys, PMIC pwrkey and PMIC resin inputs;
these need separate GPIO/SPMI transport and HID/consumer-event integration.
Do not interpret this as a Goodix protocol issue or enable another board's pins.

## v2 hardware result

Read result-touch-snapshot-v2-1/untagged-2a74ac80: 113 rounds, uptime231s,
errors0/GPT1/1. Snapshot rows recovered from latest slot112:

```
GCC+23018=00004000  GCC+23008=00004000
GCC+23000=00000000  GCC+23004=00000000
GCC+18004=80004000  GCC+52008=48000900
gpio0 control=00000203 io=00000003
gpio1 control=00000203 io=00000003
gpio2 control=00000001 io=00000000
gpio3 control=00000001 io=00000000
gpio70 control=00000000 io=00000000
gpio87 control=00000001 io=00000000
SE0 skipped: clock-off observed
```

SE0 S0 CLK_OFF bit31 is set. GPIO0..3 function bits5:2 are zero (GPIO),
not function1 QUP SPI. GPIO87 output-enable bit9 is clear: low IO alone
does NOT prove software is holding the controller in reset. Rail state and
controller power are not measured. No SE0 identity or FIFO read occurred.
This establishes that an immediate prepared-controller SPI transaction is not
possible; actual cold-start ordering remains to be implemented/verified.

gcc-volcano.c independently confirms shared vote bits18(core2x),19(core),
20(m-ahb),21(s-ahb),22(s0), branches at the observed offsets. The next
write-enabled experiment should isolate QUP clock voting and bounded status
read-back first; do not combine reset/rail votes/SMMU bypass/SPI DMA in one
unattributable boot. Preserve unrelated shared GCC votes and use Amethyst's
L13B/L23B only when the later rail stage has qualified RPMh resource ownership.
The '?' suffix in slot text is the logger replacing a stored newline with a
nonprintable placeholder, not an uncertain MMIO reading.

## Clock-only candidate (hardware untested)

Mode2 (`--touch-clock-vote` with --touch-snapshot) adds one write: OR mask
0x007c0000 into GCC application vote at0x152008, preserving other bits in the
read value. It does not clear shared votes on failure; votes remain until reset.
There is no shared GCC software lock against an unknown concurrent writer;
this remains a controlled bring-up operation, not generic clock ownership.
Polls pre-mapped branch-status pages at most2000 times with10us delay (20ms
delay budget plus MMIO overhead), then reads SE0 only if no CLK_OFF observed.
Durable rows record before/after/mask and every post-vote branch.
No root-image/partition, TLMM, reset, rails, RSC, SMMU, DMA or SPI command write.

Image staging/amethyst-touch-clock/Mu-amethyst-touch-clock.img,80322560 bytes,
SHA25691b8b45bd23e261f48a12a34ec45aec87d8d34de0bb884a398dcbcc990f71f11,
UUID706EAF99-A7E9-30F6-85AE-17E9E3975BC8. Recorded recipe compile0, link0/
no undefined symbols, mandatory prelink and pack PASS. Mask-preservation and
all-five-clock-off tests PASS under ASan/UBSan; snapshot source check PASS.
Prediction: post-vote S0 CLK_OFF clears and firmware/FIFO identity is readable;
timeout safely skips SE0. No touch events expected; GPIO remains unmuxed.

### Clock-stage hardware run 1 passed

Read Windows result-touch-clock-1/untagged-25880e20: 114 completed rounds,
uptime233s, errors0, GPT1/1. Recovered before/after application vote
48000900 -> 487c0900, mask007c0000. Clock wait attempts0. Post S0 branch
00004000 (CLK_OFF cleared), other four branch values unchanged. SE0 reads:
status00000000 (master inactive), if-disable00000001, fw00000102,
hw202029e4. GPIO values unchanged from snapshot-v2. This confirms clock
voting and safe engine identity reads for this boot, not DMA/IRQ/Goodix reads.
First Windows pull failed `error: closed`; retry succeeded. No attribution to
the experimental kernel from that transient ADB transport error.

### Read-only SMMU survey candidate

Added geometry-bounded read-only apps-SMMU mapping survey for streams563,
576 and60 (UFS). Records matching SMR/S2CR and selected-bank SCTLR/CBAR/CBA2R;
does not change mappings or allocate/submit DMA. Snapshot storage enlarged to
48 rows so typical mapping evidence survives in every periodic slot. Existing
clock-only mode2 votes remain the sole MMIO write. Multiple or wildcard matches
are reported, not treated as permission to take over a bank.

Image staging/amethyst-touch-smmu/Mu-amethyst-touch-smmu.img, SHA256
168396de6b85a6a11f1cf7da02447103d2a6cf6acdacd13a601ce8772fa40da0,
UUID9D69A02D-62B5-3D9B-821C-0D2898052F32. Probe/logger compile0, isolated
link0/no undefined symbols, prelink and pack checks PASS. Source safety check
covers read-only survey and stream/bank bounds. Hardware untested. No touch or
side-button input expected; no pin/rail/reset/SMMU configuration changes.

### SMMU survey hardware run1 and restricted planner

Read result-touch-smmu-1/untagged-56765c7a:124 rounds, uptime252s, errors0,
GPT1/1. ID0=4c017e72 ID1=60000048 ID2=5111. Streams563/576 each have
zero matches. UFS060 has exactly one match slot113, SMR80000060/S2CR47,
bank71 SCTLR0/CBAR10000/CBA2R1. Clock votes again succeeded; SE0 idle,
FIFO disabled, revision102. No touch transaction or DMA performed.

AmethystTouchRoute.h now plans only reuse of that already configured bank,
discovered through the exact UFS stream (not hardcoded bank71). It refuses
ambiguous/wildcard/conflicting streams, enabled or non-bypass bank state,
unsupported geometry, overflowing DMA extent and no free stream group.
It contains no write callback, never reinitializes the bank, and does not route
GPI stream576 when using direct SE DMA. check-touch-route.c tests observed
geometry, reuse/conflict, bank-active, overflow and exhaustion with ASan/UBSan:
exit0. This is a host plan only, not permission to alter shared context state.
Register-table stability, locking/revalidation and retained DMA lifetime still
required before hardware route commit. No new image in this step.

### Route-commit helper (host only)

Added commit callback sequence: rerun the plan with the supplied DMA extent,
refuse stale slot/bank/reuse state, write S2CR/readback first, then publish
SMR/readback, then resurvey UFS/bypass state. Reuse performs no write. Never
writes context registers or UFS slot. Failure may leave a partial stream route;
caller must keep SE idle, retain DMA memory and must not blindly undo shared
state. Callback contract requires completed MMIO barriers. Serialization with
all table writers remains an external prerequisite; revalidation alone does
not close a concurrency race and the helper is not called by any image.

Synthetic tests verify ordered writes, no-write reuse, stale slot/bank refusal,
S2CR/SMR dropped-write failures and UFS-entry preservation. ASan/UBSan exit0.
No hardware route write, no DMA allocation/submission, no new boot candidate.

### Retained DMA allocation helper (host only)

AmethystTouchDma.cpp prepares one contiguous8192-byte IOBufferMemoryDescriptor,
validates a single physical extent, page alignment and36-bit reachability, then
divides it into isolated4096-byte TX/RX halves. Successful prepared allocation
is retained until reboot. Invalid allocation is completed/released before any
hardware can access it. Caller must serialize; no register programming,
SMMU mapping, cache callbacks or DMA submission is performed by this helper.
Zeroing alone does not establish DMA cache coherency. Future timeout handling
must not free/reuse a possibly active buffer.

Extent boundary/short-segment/alignment/overflow tests pass under ASan/UBSan;
recorded IOPlatformExpert.cpo.json recipe compiles allocator exit0. Not linked,
called or device-tested. Existing UI images unchanged. Next wire cache policy
and controller serialization before a controlled internal loopback stage.

### Retained DMA cache binding (host only)

Added clean/clean-invalidate/invalidate callbacks restricted to one retained
4096-byte TX or RX half; invalid ranges fail before cache operations. Cache line
size comes from CTR_EL0, page isolation bounds rounded maintenance, dsb sy
barriers bracket operations. Binding fills GENI DMA addresses/callbacks but does
not select DMA mode or access MMIO. Caller still must ensure exclusive ownership
and idle controller; no locking/race-free global SMMU ownership claim.
Extent/range tests ASan+UBSan exit0; recorded ARM64 recipe compile0.
Hardware cache coherency untested until internal loopback; no new image.

### DMA allocation + plan hardware preflight candidate

Integrated allocator into read-only survey behind explicit
amethyst-touch-dma-preflight=1, following mode2 clock-stage success. Reports
prepared DMA allocation and plan valid/slot/bank/reuse in durable rows. No
route commit, cache execution, SE-DMA registers or transaction submission.
Retains allocation until reboot. Side buttons remain unimplemented.

Image staging/amethyst-touch-dma-preflight/Mu-amethyst-touch-dma-preflight.img,
SHA256 bcc26d38ba60a73b29f065387636e4118c4ee02b6bef8ea5d6109e7699f2eb06,
UUID0319B627-2BE1-3A90-A937-C1F5927BFE0C. Both recorded-recipe compiles0,
link0/no undefined symbols, source boundary check, prelink and pack PASS.
Hardware untested. Prediction allocation rc0 and valid plan using already
bypass-configured UFS bank; otherwise preserve failure code without mapping
writes. This is not proof of working DMA or touch input.

### DMA preflight hardware run1 passed

Read result-touch-dma-preflight-1/untagged-574ac789, latest slot184:
185 completed rounds, uptime376s, write_errors0, GPT1/1, state2.
Allocation rc0, physical base0x8cce91000, TX VA0xfffffff9b4d99000,
RX VA0xfffffff9b4d9a000, extent8192. Plan valid1, slot3, bank71,
reuse0, explicitly no commit. Physical range [0x8cce91000,0x8cce93000)
fits the observed36-bit minimum address width. This address is one boot's
allocation, never a constant for later candidates.

UFS remains slot113/stream060 -> bank71 with SCTLR0/CBAR10000/CBA2R1;
touch563/GPI576 have no existing matches. Clock vote and idle SPI identity
remain consistent with previous runs. No mapping update or DMA submission
was exercised, so neither cache coherency nor controller access to this
allocation is established.

Next controlled stage: internal SE0 loopback with retained DMA buffers and
revalidated stream563 route; keep external pins, touch reset/rails and Goodix
commands untouched. Requires exclusive-controller/table ownership and bounded
quiescence handling before submission. On uncertain DMA stop, retain buffers
and do not blindly roll back shared SMMU state. Do not hardcode slot3/bank71.
No new image built from this result alone.

### Internal-loopback transaction helper (host only)

Added AmethystTouchLoopback.c over existing QcomGeniSpi API: a16-byte
full-duplex internal-loopback pattern, bounded poll count, byte-count/data
verification and explicit post-transfer master-active sampling. Only restores
normal mode if the engine is idle. Timeout or uncertain stop must quarantine
retained buffers/mappings; no automatic retry/Goodix traffic. Caller must own
idle engine, prepared route/cache buffers, and leave external pins GPIO.

Synthetic helper tests exercise success, corrupt echo, timeout, active-engine
refusal and no-reconfigure-on-uncertain-stop: ASan/UBSan exit0. Existing GENI
register-model tests also PASS for init/clock/mode/packing/PIO/SE-DMA20 PIO
lengths. These are not hardware tests. The board runner still needs clock-source
qualification, exclusive ownership and guarded route commit; helper not linked
into an image, no device DMA command issued.

Firmware protocol field (revision >>8) equals SPI1. FIFO interface remains
disabled: use the qualified SE-DMA path rather than forcing FIFO operation.
Before any DMA submission, inspect the current SMMU mappings for streams563
and576 and prove retained DMA buffers/cache maintenance. Existing NativeUfs
can use a fallback context bank; do not assume Asteroids' last-bank route is
free or safe to change. Rail/reset/mux and side-key SPMI still outstanding.

### Loopback candidate image built (untested on hardware)

First image that can perform a DMA transfer. Built through the mandatory
pipeline: `compile-touch.py` (six objects) → `link-touch-loopback.py` →
`prelink-candidate.py` → `pack-log.py`.

Recipe selection was the whole build problem, and it was a real bug rather than
a toolchain fault. `startup.o.json` is `kern/startup.c` and passes `-xc`, so
compiling a `.cpp` file with it parses C++ as C: every `class`/`template` in the
IOKit headers becomes "unknown type name", and the earlier "typedef
redefinition" storm in `bsd/sys/types.h` is the same mistake. C++ sources must
use `iokit/DEVELOPMENT/IOPlatformExpert.cpo.json` (`-std=gnu++2b`, IOKit search
paths). The shared replayer also strips every `-W` argument, which removes
XNU's own suppressions (`-Wno-missing-designated-field-initializers`,
`-Wno-unnecessary-virtual-specifier`, `-Wno-error=c99-designator`,
`-Wno-error=reorder-init-list`) and then our `-Werror` turns XNU's exported
headers into failures. Restoring the recorded `-Wno-*` flags after `-Werror`
fixes that without relaxing our own sources.

Real source defects found once the C++ path compiled: the stage shadowed its
`int status` with an `IOMemoryMap *status[2]` array, cast a pointer with
`static_cast<uintptr_t>(...)` before dereferencing, and declared
`AmethystLoopbackResult` locally instead of including `AmethystTouchLoopback.h`.
`GeniSpi.c` is not part of the board component set, so `geni_spi_init`,
`_setup`, `_read_info`, `_set_clock` and `_transfer` were undefined at link; it
is now compiled from `drivers/QcomGeniSpi` and linked as an object.

Image: `staging/amethyst-touch-loopback/Mu-amethyst-touch-loopback.img`,
80330752 bytes, sha256
`bd59818b420cc7e920e9c9aab0967fd37035dcc200156526cdff3d87ec58841b`, kernel UUID
`141AF6F9-DB6C-329F-8ACB-8DB925FA3B8D`. Boot args add, exactly once each,
`amethyst-touch-loopback=1 amethyst-touch-dma-preflight=1
amethyst-touch-snapshot=2` on top of the working display set; data seed
`watchdog-disabled.packed` (sha256 `40d76e6f…`). Not yet booted.

### Loopback run 1 aborted; addressing correction candidate v2

Run 1 photo shows a kernel data abort immediately after DMA preflight reported
valid slot3/bank71, before the first loopback stage log. Recovery pull
`result-touch-loopback-1/untagged-ec241712/latest.txt` contains only an initial
slot (uptime0, rounds0), no touch snapshot or panic text; it does not confirm
the fault address. Disassembly confirms the stage directly loaded physical
`0x00a80040` without a virtual mapping, consistent with this early abort.
Two later defects were also found: voting mapped page0x100000 rather than
0x152000, and route planning/commit used the SE0 context, not the SMMU context.

V2 moves the idle read after clock qualification and SE0 mapping, derives the
vote page from0x152008, and uses the mapped SMMU context for plan/commit.
`check-touch-snapshot.py` guards all three regressions; route/DMA/loopback host
checks pass with ASan/UBSan (exit0), all six ARM64 objects compile, link/prelink
and pack checks pass. Static checks are not hardware validation.

Image: `staging/amethyst-touch-loopback-v2/Mu-amethyst-touch-loopback-v2.img`,
80330752 bytes, SHA256
`d0cf5827ed684d2bd35ee6eee9a8210172ca3b13e5358bb94636caa7cad9ff13`.
Kernel UUID `62609BDD-80C1-3E86-9B7A-A8852FBA4342`, prelinked SHA256
`f3e4c0a77ca76867012cbbbf2ae57b8440f667d65eca3e704f9a3ec350bb2c7e`.
Same boot arguments and Data seed as run1; failed image retained. Hardware
untested. Next: one RAM-only boot, then recovery log pull; no flash or userdata
access. The selected slot/bank are discovered, not assumed to be slot0/bank71.

### Loopback run 2: stable boot, transaction result not retained

Recovery evidence `result-touch-loopback-2/untagged-23bc3016`: uptime260s,
127 rounds, errors0, GPT1/1. Snapshot retains allocation rc0 PA0x8cce95000,
valid slot3/bank71 preflight; no loopback lines in latest/slots/msgbuf.
Stage IOLog-only messages can roll out of msgbuf; absence does not prove a
successful transfer or even which stage gate was reached.

V3 changes logging only: exports the existing snapshotLog helper and routes
all loopback stage messages through it, retaining them in the existing 48-row
periodic snapshot as well as IOLog. Normal preflight occupies28 rows and the
successful stage adds11, within capacity. No transaction, route, clock, DMA,
boot-argument or Data seed changes. Regression check requires retained result
and quarantine messages and no stage IOLog-only calls. Snapshot check and all
six ARM64 compiles pass; link/prelink/pack checks pass. Hardware untested.

Image `staging/amethyst-touch-loopback-v3/Mu-amethyst-touch-loopback-v3.img`,
SHA256 `1b251217e2e0f7f1fe678a7e58a43eb16377b11468cb08888a33c7ee86c392dc`.
Kernel UUID `9BC35D5D-AC57-3C43-8E64-3E0D0C2E0252`, prelinked SHA256
`e90abcd26ffbd496da5e63b1d474aacbf399e7322aedbdddf44a26b279cad38c`.

### Loopback run 3: clock qualification refused; v4 diagnostics

Recovery `result-touch-loopback-3/untagged-269a246a`: uptime239s, rounds118,
errors0, GPT1/1. Retained stage evidence: vote487c0900->487c0900 wait0,
identity info0/proto1/fifo1/fw00000102/hw202029e4, then "clock source
unqualified". No route commit or DMA transfer reached.

Code inspection finds set_clock refuses zero oversampling, while the stage's
zeroed struct is clock-qualified before init sets oversampling. The wrapper
0xffffffff early refusal can also precede this; neither subcase was logged.
V4 adds retained wrapper/oversampling, setup return code, and cfg/selector/
CPHA/CPOL diagnostics without changing gates, initialization or hardware writes.
Snapshot regression check, six ARM64 compiles, link/prelink/pack pass.
Hardware untested; a logging-only boot may be skipped in favor of resolving the
known initialization ordering against the driver contract before further DMA.

Image `staging/amethyst-touch-loopback-v4/Mu-amethyst-touch-loopback-v4.img`,
SHA256 `b1cf1e2089d2b624a3fa89fd19f7423c16b495e907ea192afe4f510593396a25`,
kernel UUID `B02B72DD-8138-3E94-9130-4C6D08809649`, prelinked SHA256
`a66761066f111cf36bc82d6bc2841c2fed71948ad2b3b5d468647feb9f09cc67`.

### Run4 confirms zero oversampling; v5 initialization ordering

`result-touch-loopback-4/untagged-eda95cee`: uptime315s, rounds154, errors0,
GPT1/1. Wrapper300d0000, oversampling0, clock setup rc-22; no route commit
or transfer reached. Driver init sets oversampling, resets cached speed and
enables DMA mode. V5 therefore keeps prepare/bind, claim/visibility and route
commit first, then initializes once, qualifies clock and attempts loopback.
A failed clock check after init leaves buffers quarantined and route installed;
no transfer/retry. Helper now requires enabled DMA mode, matching driver init,
rather than rejecting it. This is one initialization-contract correction, not
a clock-gate bypass. External GPIO/reset/rails and Goodix remain untouched.
Ordering regression and corrected DMA-mode host test pass (ASan/UBSan exit0),
six ARM64 compiles and link/prelink/pack pass. Hardware untested.

Image `staging/amethyst-touch-loopback-v5/Mu-amethyst-touch-loopback-v5.img`,
SHA256 `6c1db859484588be8c1d01f28b4d75ae4cb7b3bd008167c7ed5b10d33803eff9`,
UUID `9E0FCA0A-FF0A-35F3-8899-83E103E58E4C`, prelinked SHA256
`df4d9d588bbd6d24bfd8d4660cf8c15929a1f1eaa5ebc13a2963d86c4c2e2938`.

### Run5 buffer refusal; v7 software corrections

Run5 `result-touch-loopback-5/untagged-23d795a9`: uptime225s, rounds110,
errors0, GPT1/1; prepare/bind=-16 before route commit. Preflight already
prepared the retained allocation. V6 merely adds diagnostics; no V6 boot is
needed to establish this software defect. V7 allows serialized prepare reuse
only in state1, refuses binding quarantined buffers, translates boolean claim/
idle helpers to zero-success errno APIs, and changes claim to compare-exchange
so a failed claim cannot mutate free state. Loopback clears invalid-call output,
refreshes idle evidence after cancellation, and retains mappings on quarantine.
Clock rate reporting uses initialized driver oversampling rather than a constant.
Existing snapshot check, route/DMA/loopback ASan/UBSan tests and six ARM64
compiles pass (exit0); link/prelink/pack pass. No hardware PASS claimed.

V7 image SHA256 `30fa499e31d0b9c75e89745fd47c924e32dca82930851ff6a54dc80f5df3f27b`,
kernel UUID `35504A96-0CE0-3CBB-B3BE-22922D478290`, under
`staging/amethyst-touch-loopback-v7/Mu-amethyst-touch-loopback-v7.img`.
Hardware clock-source frequency and actual DMA/Goodix operation remain unproven.

### V8 continued audit and real-driver integration check

Added `check-touch-integration.c`, linked against real GeniSpi.c and loopback
helper rather than stubs. Register model exercises observed version/firmware,
init-before-clock, poll-only IRQ masking, DMA-mode contract, one16-byte echo
and cleanup. ASan/UBSan exit0. Run with:
`cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined
-I UniXNU/drivers/QcomGeniSpi UniXNU/drivers/QcomGeniSpi/GeniSpi.c
UniXNU-amethyst/scripts/log/AmethystTouchLoopback.c
UniXNU-amethyst/scripts/log/check-touch-integration.c
-o .scratch/ufs-audit/check-touch-integration` then run that executable.

Prepare clears output pointers and stale refusal reasons; allocation failures
now report reason2. Cache probe requires claimed state, uses volatile loads and
restores its original byte even after mismatch. Planner rejects wildcard touch
entries before commit (which already required an exact entry). Route sanitizer
test and snapshot check exit0; six ARM64 compiles and link/prelink/pack pass.
CPU cache round-trip is not proof of device DMA visibility or coherency.

V8 supersedes unbooted V7. Image SHA256
`788a9a0508ecb215f7e9403c884494a0c16b23d0c935857887ae0623281f307f`,
UUID `5E465A4B-2062-3966-A74C-A5AEC540EE65`, image
`staging/amethyst-touch-loopback-v8/Mu-amethyst-touch-loopback-v8.img`.
Hardware untested; neither host model nor readback proves the assumed19.2MHz
source frequency. No claim of a completed or bug-free touch stack.

### V9 source clock read from DFS level 0 (Asteroids reference)

V8 passed an assumed 19.2MHz source to `geni_spi_set_clock`. Asteroids
(rootstockos/UniXNU-asteroids bbf26b0, package/AsteroidsTouchProbe.cpp
`readClocks`/`levelHz`) reads the same SM7635 GCC SE0 RCG at 0x118010 and
derives the rate from DFS level 0. V9 ports that: `AmethystTouchDfs.h` decodes
DFSR/perf/M/N (source 0/1/6 = 19.2M/600M/300M, half divider, inverted N) and
refuses DFS-disabled, unknown-source or invalid M/N values. The stage reads
these GCC registers (no writes) before releasing the clock-status maps, logs
one retained `dfs` row, and passes the decoded rate to set_clock and the rate
report. Zero rate stops before any transfer. No route, DMA, pin, rail or
boot-argument change. `check-touch-dfs.c` (ASan/UBSan), the integration test,
`check-touch-snapshot.py` and six ARM64 compiles pass; link/prelink/pack pass.

Image `staging/amethyst-touch-dfs-v9/Mu-amethyst-touch-dfs-v9.img`, SHA256
`a9e74b4f7fb25e4f4c091e32b424a36e8a37277e7b928596f82085756db6da21`,
kernel UUID `A98C685F-5E49-38A6-8BE0-B52A773B1FAA`, prelinked SHA256
`a65ab8a7eaafd7cf200183406d7436236043757b4cc5badb353a3d3ab62b272a`.
Same pack flags and Data seed as V8; supersedes unbooted V8. Hardware untested.

### V10 Goodix power-up and identity read (opt-in, after a passing loopback)

New `amethyst-touch-goodix=1` (`pack-log.py --touch-goodix`, requires
--touch-loopback) runs only if the V9 loopback matched and released its
buffers. `AmethystTouchGoodixStage.cpp` + `AmethystTouchPower.h`:
cmd-db (0x81c60000) lookup of ldob13/ldob23; reset GPIO87 low (8mA output),
irq GPIO70 input no bias; RPMh votes on apps RSC drv-2 (0x17a20000, TCS2,
idle/empty-only, per Asteroids) iovdd 1.8V, 3ms, avdd 3.224V, 15ms; reset
high, 104ms; SPI pins GPIO0-3 to qup0_se0 (function1, 6/4/4/6mA, no bias);
500ms IRQ sampling; one BerlinD read of 0x10014 (28 bytes, up to 3 tries on
checksum/ID failure only). No writes to the controller, no firmware/config,
no mode commands. Engine still active afterwards: buffers and maps retained.
Rail/pin values: amethyst-sm7635-xiaomi-touch.dtsi, volcano-regulators.dtsi,
volcano.dtsi. `QcomRpmh.c/.h` copied unchanged from UniXNU-asteroids
(BSD-2-Clause-Patent, from sheng).

Log budget: BootLog keeps 48 rows; the success path to here uses ~43, so this
stage writes 3 rows: `goodix rsc=… vote io=…/a=…`, `goodix rst=… irqlow=`,
`goodix id OK … BERLIN …` or `goodix id FAIL rc= raw=`. Expect identity
matching Android: ROM BERLIN 020004, patch GEST 00010104, sensor 255.

Checks: check-touch-power.c, Goodix packet/identity/spi/geni tests and the
integration test (ASan/UBSan), check-touch-goodix.py, check-touch-snapshot.py,
eight ARM64 compiles; link/prelink/pack pass. Goodix headers now compile as
C++ (memset instead of `{0}`).

`~/amethyst-work` (donor BootShim.S) was deleted outside this work; pack-log
now accepts `--shim-from <earlier image>`. Repacking V9 that way gave an
identical shim and sizes; only FAT serial/timestamps differ (random per pack).

Image `staging/amethyst-touch-goodix-v10/Mu-amethyst-touch-goodix-v10.img`,
SHA256 `f820d967f26e558609077e6b8095733a8b3512812364591ca25391637cdd982c`,
kernel UUID `287560AE-398A-3050-8575-5EFF31AC8930`, prelinked SHA256
`20f7c6f2299e8794826b4921518e882f743096bd61c8c35d0263359e5b02bfa2`.
Includes V9. Hardware untested. Supersedes V9 for testing: if the loopback
fails, V10 behaves exactly like V9.

### V11 result and V12

V11 (v10 kernel + lock-screen seed + buttons), log `.scratch/log-results/result-after-v3`:
lock screen reached (137ms swaps at 845s). Loopback matched, buffers released.
`goodix rsc=00030200 avdd=41f00 iovdd=42900 vote io=0/10101 a=0/10101`: cmd-db found
both rails and both RPMh votes completed. The rst/irq and identity rows were dropped:
4 button rows + 43 loopback rows + 1 = the 48-row cap. V12 keeps rows 44-47 for
`AmethystTouch: goodix` rows only (snapshotLog). Kernel prelinked SHA256
15b750aef2187cf5b42aa4fd47e81911bbf472864903771291f03119a8201148.
Image `staging/amethyst-lockscreen-goodix-v12`. Hardware untested.

### V12 result: Goodix identity read OK (2026-11-04)

Log `.scratch/log-results/result-v12` (441s, errors0, lock screen, 136ms swaps):
`goodix rsc=00030200 ... vote io=0/10101 a=0/10101`,
`goodix rst=2c0/3 irq=0/0 spi=84/44/44/84 irqlow=487/500`,
`goodix id OK try=0 BERLIN rom=020004 patch=9916R 05800913 sensor=255`.
First try, checksum valid: ROM BERLIN 020004 and sensor 255 match Android; the patch
block reports PID 9916R, version 05800913. IRQ GPIO70 is low 97% of the 500ms window:
the controller keeps its interrupt asserted, as expected while nothing reads and
clears its event buffer. Next: read the event/coordinate block and acknowledge it.

### V13 event poller (opt-in amethyst-touch-events=1, after a valid identity)

`AmethystTouchEvents.cpp` + `GoodixBerlinEvent.h` (vendor goodix_brl_hw.c / Linux
goodix_berlin_core.c layout: 8-byte head with u16 sum, status bit7 touch, count =
byte2&15, 8-byte contacts, u16 contact sum; ack = one zero byte written at 0x10308,
F0 + BE32 address). A kernel thread samples GPIO70 every 4 ms (8 ms after 1 s idle);
on low it reads 90 bytes at 0x10308 and acks only when the head checksum is valid and
status is nonzero. Rows 46/47 are rewritten live (counters, x/y range, last event + raw
head). The loopback stage's SE/wrapper/SMMU contexts and geni_spi are now static and
its mappings retained once the poller owns the engine. No HID delivery yet.
Snapshot rows: non-Goodix cap 43, Goodix rows to 45. check-goodix-event.c (ASan/UBSan).
Kernel prelinked SHA256 e2e7b37b79376db8671a697392dcc2cf2d76d99431c852e8e66e5f9a1e3954f8.
Image `staging/amethyst-touch-events-v13`, SHA256
93afc217793a74a10de7b4a193866296c53936ab8786e1023513e49b1c4713b2. Hardware untested.

### V13 result and V14 (Linux power-on order)

V13 (`.scratch/log-results/result-v13`, 678s, lock screen): identity OK again, the poller
read 110614 event blocks at 0x10308 without an SPI error, but every block was all zero
(status 0, so no ack), and GPIO70 stayed low. Linux goodix_berlin_core.c differs in two
ways: (1) power_on writes 8 x 0xAA to the boot option 0x10000 4 ms after reset release
and reads it back (dev confirm) before the 100 ms firmware wait; V10-V13 never did; (2) the
event address is IC_INFO misc.touch_data_addr (IC_INFO at 0x10070 for BerlinD), not a
constant. V14: SPI mux and buffers before reset release, reset, 4 ms, dev confirm (3 tries,
5 ms apart), 100 ms, version, IC_INFO (length, block, sum, Linux variable-list parse;
GoodixBerlinIcInfo.h + check-goodix-icinfo.c), poller at the parsed touch_data_addr (else
0x10308). amethyst_goodix_write added (shared checks with the read). Rows:
`goodix rst=... confirm=rc/ok try= rb= irqlow=`, `goodix id OK ... info=rc/len touch= cmd=`,
`ev @addr ...`. Kernel prelinked SHA256
3bdf1d6d3a8329dd03b0c5784d7a00a977b99cf9a5fa6fe80dd7e895b8bf53bc. Image
`staging/amethyst-touch-confirm-v14`, SHA256
c80963c9897214722efac2d4f44031b6afb42b4b9bd2dcea9ca84e1218993a5c. Hardware untested.

### V14 result and V15 (host touch computing off)

V14 (`.scratch/log-results/result-v14`, user tapped the corners several times): dev confirm
OK (`confirm=0/1 try=1 rb=aaaa`), IC_INFO valid (`info=0/245 touch=10308 cmd=10174`),
yet 57570 event reads were all zero. Android ships THP files (odm/firmware
o16u_goodix_thp_config_*.ini, lib64/libtouchreport_alg_goodix.so): the firmware boots in
host touch computing mode and streams frames instead of coordinates. Vendor
goodix_core.ko (EvolutionX device tree, vendor_dlkm) goodix_htc_enable(en): cmd 0x90
"touch raw" data en?table[mode]:0, then cmd 0x91 "touch data" data en?0:(mode?0x81:1).
brl_send_cmd: 16 bytes at cmd_addr [0,0,len 5,cmd,data,LE16 byte sum of len/cmd/data];
6 sends x 6 polls 1 ms; ack byte 1: 0x80 done (+4 ms), 0/2 busy, 3 wait 10 ms resend,
else resend. goodix_append_checksum mode 0 verified by disassembly (byte sum, 2 bytes LE).
V15 sends 0x90/0 then 0x91/1 after IC_INFO when the poller is enabled; the id row adds
`raw=rc/ack crd=rc/ack`. GoodixBerlinCmd.h + check-goodix-cmd.c. Kernel prelinked SHA256
dcecbd8f26cf1b89c40fdec7a96a049ac60588ff2c9da20f2badb16c83f2dbc8. Image
`staging/amethyst-touch-coord-v15`, SHA256
105a49e116f1f1296bf2c3816a8ae6ad75fd4454b8519c3a3d280dbe6593e4f5. Hardware untested.

### V15 result and V16 (poll the event head)

V15 (`.scratch/log-results/result-v15`): both commands acknowledged (`raw=0/80 crd=0/80`).
GPIO70 changed behaviour: low 489/500 before the commands, never low afterwards (0 in all
155 slots while the user tapped), so in coordinate mode the line idles high and its pulses
are shorter than the 4 ms poll. V16 polls the 8-byte event head every 4 ms (8 ms after
10 s idle) and reads/acks the whole block only when status is nonzero; GPIO70 low samples
are still counted (`low=samples/polls`). Kernel prelinked SHA256
ec6e0f51c371682740dec23d74bc057a969eb91db0c6df1f275bbd6c5bd9a613. Image
`staging/amethyst-touch-poll-v16`, SHA256
1347e52e209e032b7b72fcfeab43cf3cc59f4830bfd98ee83b832b11b8db67fc. Hardware untested.

### V16 result and V17 (host-computing frames)

V16 (`.scratch/log-results/result-v16`): 30976 head polls at 0x10308, all status 0, GPIO70
never low: with htc_enable(0) the firmware reports nothing. In its default host touch
computing mode (V13/V14) GPIO70 was low ~97% before any command: a frame waited and was
never cleared. Vendor goodix_core.ko: brl_event_handler reads 2500 bytes at
misc.frame_data_addr (cd+0xbf) and checks byte 0 bit 7 (sync); brld_get_framedata checks
the head (frame_data_head_len, byte sum) and takes the grid at
head+fw_attr_len+fw_log_len+8 as drv*sen LE16 values; brl_after_event_handler writes one
zero byte at frame_data_addr in HTC mode (touch_data_addr otherwise).
V17 (`--touch-frames`, amethyst-touch-events=2): no commands; when GPIO70 is low it reads
the frame (only up to the grid end), requires the sync bit before writing anything, then
clears it like the vendor. Rows: `fr @addr drvxsen h/a/l n low= rd= nr= hb= clr=` and
`f sy= len= hd= max=@ min=@ abs= peak=@#frame floor=@`. GoodixBerlinFrame.h +
check-goodix-frame.c; IC_INFO parser now returns frame address/layout and drv/sen
(vendor misc offsets 0xa7/0xbf/0xc3/0xc5/0xc7/0xd3 = misc+0/24/28/30/32/44); BerlinD
read max raised to 2560. Kernel prelinked SHA256
7599fa9197f2b43ecb172fda3082b6fb86fcf9cf8e464544bd08c10d88039e9a. Image
`staging/amethyst-touch-frames-v17`, SHA256
33e57d95823367f5a5edf27a16d3899eb74118847bbffe0bdfffccd23c2ca178. Hardware untested.

### V18 result and V19 (baseline difference, 1 MiB log)

V18 (`.scratch/log-results/result-v18`, 8 cores): frames flow. IC_INFO frame block
0x10400, grid 18x40 (720 nodes), head 16, attr 30, log 132, read 1626 bytes; 9472 frames
read, 0 errors, 0 bad heads, 5297 clears; sync 0x80, len 2368. Grid values are raw counts
(max ~11025, min 0, |sum| ~7.6M), so a finger is a deviation from each node's own value.
V19: GoodixBerlinDiff.h (16-frame baseline, |diff| press >= 250 / release < 120, slow drift
on quiet nodes, ring of the last 6 presses: node index of the strongest diff and its signed
value; check-goodix-diff.c, which caught an integer-drift stall). Rows: `fr ... base= act=
pr= ext=val@node now=val@node` and `press <down> idx:val x6 raw=`.
Also BootLog's message area is 256 blocks (1 MiB, slots 495) with msgbuf=1048576
(pack-log --msgbuf), so the first ~95 s of boot are no longer overwritten; panic/flush/
record checks pass. Kernel prelinked SHA256
273c77b3f71c7879202f4cb7a3918004dbb7665252794306571db53f2bdc857f. Image
`staging/amethyst-touch-diff-v19` (with --smp --quiet-kprintf), SHA256
3197b19670d8e59996b0d8807f1b0a1708c94df9705b1720913f7e17c4d9d16a. Hardware untested.

### V19 result and V20 (frame recorder)

V19 (`.scratch/log-results/result-v19`): baseline learned, but |diff| swings 300-700 on
random nodes for the first ~87 s (boot), then ~50-100 when quiet; the user's taps gave
presses of only ~250 (115-122 s); from 126 s node 3 sits at -2200 for good. The single-
peak detector cannot separate this, so V20 records raw grids: a 2048-frame ring
(~35 s at ~58 fps, 8 + 1440 bytes each) exported every 8 s through boot_log_export_put
into BootLog's export region ("AMTFRM01" blob); pull-log.py now copies the newest export
to export.bin (read-only dd below the window); analyze-touch-frames.py (median baseline,
quiet-noise threshold, press intervals with node and both row/col readings, --dump maps;
self-tested on synthetic frames). Row 47 adds `exp=generation/status`. Kernel prelinked
SHA256 6b56d3f7db4f4b8965af795abbd932d2b466ea3b4bd488c2e71f379d4acb3c7b. Image
`staging/amethyst-touch-record-v20`, SHA256
783b89899900c4edec5f3971ab3dc6a5a40e55026d4bddb478dc879b5bb78d8c. Hardware untested.

### V20 result and V21 (measured press detector)

V20 (`.scratch/log-results/result-v20`): stuck at the Apple logo, phone warm. Swaps stop
after W2 (120.3 s) and the kernel log ends at the same moment, while the logger and the
poller kept running to 299 s (37 exports, 0 errors): userspace froze. Exporting ~3 MB every
8 s from the poller thread (boot_log_export_put -> native_ufs_export_write) while iOS read
its root is the only change from V19, so the recorder is now opt-in
(amethyst-touch-record=1, not in normal images).
The export (2048 frames, 62.8 s, 32.6 fps) shows what a finger is: a blob a few nodes wide,
-1100..-1470 from the untouched value, held for many frames; noise is single-frame +-250,
mostly on row 0; several contacts at once are common (hand on the edges).
V21 detector (GoodixBerlinDiff.h): most negative difference <= -600 for 3 frames starts a
press, > -300 ends it; deepest node and weighted centroid (1/16 node, 5x5 window) per press.
check-goodix-diff.c covers the measured shapes; replaying the 2048 recorded frames through
the same C code ignores every spike and finds 4 presses (266.75, 267.02-269.31,
276.25-277.36, 284.85-end s). Row 47: `press <down> row16,col16:depth x6`.
BootLog copy_msgbuf now keeps the newest 1 MiB when the kernel buffer holds more.
Kernel prelinked SHA256 f65a9eb4a7624ff70d5882f1d87c1c803d21bd2c38c70877105596798ea4fbde.
Image `staging/amethyst-touch-press-v21`, SHA256
53d0a95d0d4bd03d5b60cc96845484d763832f1898366f64f741f1c07005a906. Hardware untested.
