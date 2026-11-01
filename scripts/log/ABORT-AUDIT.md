# Post-round-1 data abort: investigation in progress

## Image identity

Parsed `LC_UUID` values (not assumed fixed file offsets):

| Kernel | UUID |
| --- | --- |
| hook-fix linked and prelinked kernel | `39F68633-231F-3DED-94DD-13A58CBEB7B0` |
| guard linked kernel | `6E6D7962-09C2-3CB2-85C1-84729E166D4A` |

UUID identifies kernel content/build association, not necessarily the entire
boot image: repacking one kernel with different arguments can retain its UUID.
Keep the image SHA256 too. `kernel-uuid.py --check` tests extraction and malformed
inputs. Its hook-fix result agrees between linked and prelinked files.

Candidate used for host symbolication: `staging/amethyst-hookfix/Mu-amethyst-hookfix.img`,
SHA256 `ad32f65735c2a7bac2fed6e5f7b2be5492bc35f3d8cbc84738315342b71560eb`.
Linked kernel: `.scratch/ufs-audit/logkit/kernel-hookfix.macho` in the workspace.
The second capture has not yet been independently tied to this image's hash.
Do not treat these resolutions as exact-image evidence until confirmed.

## Symbolication against that candidate

`llvm-objdump -d` resolves:

| Address | Resolution | Status |
| --- | --- | --- |
| `0xfffffff00bffd468` | `panic_with_thread_kernel_state + 0x19c`, `clrex`, immediately following `bl panic` | High-confidence supplied caller |
| `0xfffffff00bfc126c` | `hostWrite4k + 0x110`, `mov w8, #0x2a`, immediately following `bl memmove` | Low-confidence supplied pc/LR candidate only |

The latter could be a return address from the copy, not the faulting PC.
Neither supplied FAR has been reliably associated with a register label.
Do not assign either FAR to a function; a data address need not be in text.

## Verified source defect and targeted change

`amethyst_boot_log_flush` passed `(const uint8_t *)&h`, a small stack header,
to `native_ufs_log_write(0, 1, ...)`. That API copies one **4096-byte block**
through `hostWrite4k` and `memmove`, reading beyond the header's C object and
potentially beyond a mapped stack page. This is a defect regardless of whether
it caused the photographed panic.

The flush now zeros the first block of its existing allocated scratch buffer,
copies the header into it, and submits that full block after the message write.
No change to `pe_init.o`, device tree, disk layout, root policy or boot arguments.

Host checks: `check-flush-block.py` PASS; `build-log.py` compiled both objects
successfully (245 imports); `check-display-flush.py` PASS. No new image packed
and no device test of this change yet.

## Corrections to earlier claims

- Removing panic-time UFS calls prevents those particular lock attempts. It does
  not prove every observed nested panic came from them without a backtrace.
- A normal-thread flush can still block on the UFS lock after its export try-lock.
- RAM message buffers and the new RAM-only panic snapshot are not proven to
  survive reset. A writer cannot be assumed to run after panic stops CPUs.
- The prior on-disk rounds remain useful, but the panic tail is not durable.
- The control boot shows the old image boots; it does not alone prove which
  individual change caused the other image to freeze.

## Remaining work (not validated)

Single-sink/one-register-per-line console output, a loader-owned panic allocation
reserved from XNU's memory allocator, panic-ring capture, display reference
ownership/counter, and full DMA lifetime audit remain outstanding. A Linux DT
`reserved-memory` node alone is not evidence that the Apple boot allocator or
XNU excludes the range. Warm-reset retention and recovery extraction must be
demonstrated before claiming durable RAM panic capture.

Acceptance still requires an identified boot image, reliable PC/LR/FAR,
readable durable panic capture, and three hardware runs past round 1.

## Follow-up call-site review

All seven production `native_ufs_log_write` calls in `BootLog.c` now use
`boot_log_buffer` or `boot_log_panic_buffer`. Each allocation is
`LogMsgBlocks * LogBlock` (32 * 4096 = 131072 bytes); the largest request is
32 blocks, slot requests are 16 blocks, and header requests are one block.
The logging code contains no corresponding buffer free. This bounds check
does not establish race freedom or validate the host DMA mapping lifetime.

`hostWrite4k` copies `blocks * 4096U` synchronously into `gHost.virt +
kDataOffset` under `gHost.lock` before issuing the command. Other production
callers are the store/export wrappers; those advance a caller-provided source
by whole blocks. Their upstream source lifetimes remain to be audited.

The stack-header defect is in the explicit checkpoint flush, not the periodic
writer's header path: the latter already uses its allocated buffer. Therefore
`round=1 write/sync=1` does not establish that the defective checkpoint executed
successfully in round 1. Establish the invoking thread before comparing FP/FAR
or reporting the writer's stack bounds as the faulting stack.

Do not silently zero-pad the existing raw block API: it cannot determine the
source object's length. A future length-aware helper would need an explicit
byte count, bounds checks, and defined partial-block semantics. Keep that ABI
change out of the buffer-fix control experiment.

## Prediction recorded before testing

If the checkpoint's stack over-read caused this abort, replacing its short
source with a full allocated block should eliminate that particular read across
the stack boundary. Three or more runs past round 1 support the repair but do
not prove causation. Recurrence at the same copy requires checking destination
mapping, other callers, and the instruction's actual source/destination registers;
ESR plus LR alone cannot distinguish them or prove a fix was absent.

The flush now logs its current thread, actual kernel stack interval, `&h`,
`sizeof h`, and the replacement source buffer from inside the checkpoint.
It uses `thread_get_kernel_stack` and `kernel_stack_size`; stack.c allocates
guard pages on both sides of that interval. This diagnostic changes stack
placement and is not itself a reproduction of the old over-read.

Host checks after the diagnostic: UUID self-check PASS, flush-block check PASS,
both logging objects compile (247 imports). No new linked/packed image or boot.
Single-sink panic output and register formatting are still pending: printf.c
calls console output, debug output and sometimes PE_kputc; this alone does not
prove which two routes reach the same screen. Inspect their implementations
before suppressing a sink. RAM breadcrumbs and released-guard counter remain
pending and are not claimed to be present in any test image.

## Packed buffer-fix control (hardware untested)

| Field | Value |
| --- | --- |
| Image | `staging/amethyst-flush-block-fix/Mu-amethyst-flush-block-fix.img` |
| Image bytes | 80310272 |
| Image SHA256 | `89794193450d1b7a7c5ead21a78b66d046b5dcf3c95dca2f8492a348d474d2b8` |
| Packed kernel UUID | `EE1EC88A-731D-3885-A035-81979019DDF7` |
| Prelinked kernel SHA256 | `5a295e34c1116b76eae442afd4a4bee88d6d9a9b615719f25c64887f5d8710da` |
| Original pe_init.o SHA256, unchanged | `79f82aa98c6e7ad2f2e31982475831fc6c4d5189c7cca8cf2c3fa4b92299e11c` |

Compile succeeded; isolated link exit 0, no undefined symbols; mandatory prelink
and packing passed. Extracted packed kernel equals the prelinked output exactly;
the complete packed TOML equals hook-fix's configuration. UUID extraction,
flush-block, panic-hook-lock, and display-flush checks pass.

Changes relative to hook-fix: full-block checkpoint header source and the
checkpoint-local thread/stack diagnostic. No sink tags, register-format patch,
breadcrumb ring, guard counter or new RAM reservation. UUID is recorded beside
the image in KERNEL-UUID.txt; automatic boot-time UUID printing is not added.

Do three separately recorded RAM-only boots. Pull to a distinct result directory
after each attempt before another boot overwrites the log window. Record whether
the checkpoint stack line appears, whether rounds progress, and whether a panic
recurs. Retain any photo beside this image. No hardware success claimed yet.

## Capture 3: conditional symbolication and lifetime evidence

User reports launchd, MSUEarlyBoot and FIPS user tests through rsa_sig, then a
single data abort. Image UUID has not been read from the capture; all resolutions
below are against the buffer-fix kernel UUID listed above, not proven identity.

| Supplied address | Resolution in buffer-fix kernel |
| --- | --- |
| PC `0xfffffff00bfeee54` | `native_legacy_state_words + 0x5c`: `ldr w8, [x23, x24]` |
| LR `0xfffffff00bfeee38` | `native_legacy_state_words + 0x40`: return site after `snprintf` |
| Caller `0xfffffff00bffd4bc` | `panic_with_thread_kernel_state + 0x19c`, after `bl panic` |

At entry, x23 is loaded from `native_legacy_object` at `0xfffffff00c10a4c0`.
x24 starts at 5808 (`0x16b0`) and advances by 4 through 5868 (`0x16ec`).
The source loops over raw display-object state words at those offsets. Thus
the faulting effective address is x23+x24, not a stack-copy address. If the
medium-confidence FAR `0xfffffff818d4b6b0` is correct and this is the first
iteration, the implied object base is `0xfffffff818d4a000`. This subtraction
is a hypothesis, not an observed x23 value or proof of allocation ownership.

`upstream-LegacyDisplayProbe.cpp` publishes the allocated display through
`native_legacy_object` without a separate reference, then releases the local
reference on both failure/early exit paths (lines 482 and 587 at audit time).
`native_legacy_state_words` checks only non-NULL and dereferences the published
address. The earlier released flag protects `native_legacy_properties_now`,
not this reader. Other raw readers include the client wrapper, and the provider
nub address is likewise published before a possible release. This is concrete
unsafe ownership evidence, but the particular allocation's mapped/free state at
panic cannot be established from the supplied registers alone.

Full pull attempted with serial 42fe0af2 to `.scratch/ufs-audit/result-capture3`:
exit 1, `adb: device '42fe0af2' not found`. No new Windows result directory was
found. Round count, checkpoint line, x23/x24 and captured UUID remain unknown.
No further image built; original pe_init.o remains in the buffer-fix image.

## Capture 3 pulled log and Asteroids comparison

Read `/mnt/c/Users/Bluekyeet/result-capture3/untagged-1518cbd25` after the
Windows pull succeeded: 18 rounds, 18 slots, uptime 34 s, errors 0, GPT 1/1.
Four checkpoint stack lines and successful flushes appear in msgbuf.txt:
gate-wait-done, before-graphics-load, after-graphics-load, after-late-probe.
They report stack `[0xfffffff3a8370000,0xfffffff3a8374000)`, header
`0xfffffff3a8373ce0`, header bytes 152, buffer `0xfffffff4490a7000`.
The former 4096-byte read from that header would extend 0xce0 beyond stack top;
the corrected flushes returned ok=1. This is evidence for the buffer fix, not
proof of the earlier photographed fault's cause.

The decisive display messages (msgbuf lines 268–274) are:
`provider was not AppleARMIODevice`, `start_hardware failed`, experiment
returned, then `flush=after-late-probe ok=1`. Latest slot 17 still reads
`d+5808:` followed by zeros. Thus diagnostics continue reading the published
display address after the display-start failure path. No panic.txt was produced;
this is pre-panic evidence, not a recovered full panic/UUID/register record.

Reviewed rootstockos/UniXNU-asteroids at
`541674cc36bca3d8caa91f616cbccebba9f3aba7`, and its pinned GopFramebuffer
`046b7c35c6b30da995b6a5692782236bbaab7ac1`, in `.scratch/asteroids-review`.

- README reports desktop/GPU/touch on Nothing Phone (3a), SM7635, iPadOS
  17.7.11. This is upstream's hardware report, not validation on Amethyst.
- LegacyDisplayProbe.cpp:483–489 has the same unchecked raw state-word reader.
  Lines 714/725/835 publish the pointer and release on failure without clearing
  it. Success at lines 799–823 keeps the initial display/provider references and
  returns before release. Upstream is not a ready-made failure-path UAF fix.
- GopFramebuffer/NativeLegacyDisplay.cpp:815 overrides startHardware for GOP,
  requires offline-legacy-gop=1, maps actual provider memory, validates its range,
  and transfers the map reference into bootMapping. board.toml enables GOP,
  PM, validation, transfer, CPU presentation and phase 4. This is materially
  different from calling Apple's original AppleARMIODevice-dependent backend.
  Determine Amethyst's actual vtable/override binding before copying flags.
- Asteroids BootLog.c uses periodic full allocated buffers and reads the same
  unsafe state reader. Its inspected implementation has no added SHLOGPN1 UFS
  panic hook or Amethyst checkpoint stack-header write.
- scripts/xnu-panic-screen.c directly formats onto the framebuffer, copies text
  to PANIC_TEXT when supplied, and parks. The patcher resolves candidate symbols.
  This is an alternate panic path, not proof of reserved RAM or durable disk
  panic recovery. Asteroids' userdata storage policy must not be imported here.

## Ownership repair in progress (not packed)

The actual provider generator input in `.scratch/ufs-audit/upstream-LegacyDisplayProbe.cpp`
now retains one diagnostic display reference before release/acquire publication,
and one provider-nub reference before publishing its address. These references
last until reboot: this probe runs once, and no teardown protocol yet exists.
The two original release paths still drop their local ownership only. This
deliberate bounded retention is documented at the publication site.

All three cached display readers acquire-load the published address. The state
reader checks output capacity and object extent before raw offset reads; inactive
reads increment an atomic counter and emit `display inactive; skipped reads=N`
through the existing log composition. The property reader increments the same
counter when skipped. The client-wrapper raw reads check extent and inactivity.
Reference ownership, not the inactive flag, prevents reclamation during reads.

Provider compile exit 0. `check-display-ownership.py` PASS; compiled object
exports `native_legacy_released_reads`. These are host checks only. The old
`check-dangling-display.py` pins the superseded one-line flag-guard syntax and
must be updated before claiming the full suite passes. No new image packed.

The existing ABI audit ends with `method_signatures_qualified=False` and
`provider_publication_qualified=False`. GOP vtable dispatch remains unresolved;
no claim that retaining this object repairs display startup or internal fields
freed by stop(). Full runtime ABI, panic output, durable capture and DMA audit
remain outstanding.

## Owned-display candidate (hardware untested)

Packed `staging/amethyst-display-owned/Mu-amethyst-display-owned.img`, 80310272
bytes, SHA256 `d1d7286629bb95b2914d86531270db1de8470a7757f5eb2fb945a05d97c64ad6`.
Kernel UUID `6B991CF1-83ED-3ED5-B6CF-C39FF1E1561B`; prelinked kernel SHA256
`274ca3554eb2aacae980367b097cd4402d3c83cb5d34c64ff64e95cb2977bc52`.
Provider compile, ownership check, link (exit 0, no undefined symbols), prelink
and pack checks pass. Original pe_init.o and boot argument selections unchanged.

Static follow-up: slot 288 of the buffer-fix kernel's NativeLegacyDisplay vtable
points to its owned startHardware override. Original call bytes at
0xfffffff005787ef8 are `080040f9088144f900013fd6e0000037`, confirming dispatch
through slot 288 and boolean-result testing. Do not "repair" this slot without
additional evidence; the provider-rejection path still needs base-body tracing.

Prediction: with the same display-start failure, diagnostic reads must report
`display inactive; skipped reads=N` rather than dereference its state words.
That proves the inactive branch ran; boot-long ownership separately prevents
reclamation. This does not predict successful display startup or a desktop.
The old guard-only check now delegates to the ownership check.

## User transcript after ownership candidate

Transcript reports watchdogd USER_BRK (ESR 0xf2000001), signal 5 exits with
namespace 2/code 5; repeated SpringBoard USER_BRK (ESR 0xf2000000), signal 5;
then a single panic stating watchdogd[112] exited, namespace 2/subcode 5.
No kernel data abort appears in this supplied excerpt. No pulled log yet.

Decoded caller candidate `0xfffffff00bd0fce4` in kernel-display-owned.macho is
the return address of `bl panic_with_options` at `0xfffffff00bd0fce0`, at the
end of proc_prepareexit (the next symbol is proc_exit). This agrees with XNU's
critical-process-exit panic path, not a faulting memory access. The userspace
PCs require their exact executable/shared-cache slide; do not resolve them
against kernel symbols or infer a SpringBoard assertion from the address alone.

This is progress, not three-run validation of the lifetime repair. Need the
pulled round count and `display inactive; skipped reads=N`, and actual service
failure diagnostics. Do not suppress watchdogd's critical-exit policy to call
this fixed. `Invalid denylist` is adjacent output, not established causality.

### Owned-display run 1: pulled evidence

Inspected Windows `result-display-owned-1/untagged-2476d6ec`: header boot ID
611768044, 44 rounds/slots, uptime 86 s, write_errors=0, GPT=1/1, state=2.
Header msg_bytes=68269 (the exported region occupies 131072 bytes).
43 slots contain inactive-read counters, monotonically 1,3,...,85. Latest slot
reports `display inactive; skipped reads=85`: the guarded failure path actually
ran repeatedly, rather than being avoided by chance.

All four checkpoint flushes report ok=1. Display still reports
`provider was not AppleARMIODevice` and `start_hardware failed` before the
after-late-probe checkpoint. watchdogd and SpringBoard repeatedly exit on
signal 5. Last persisted message is `Invalid denylist`; no final panic record
or captured UUID is present. Kernel panic reason comes from the user's separate
transcript, not this on-disk tail. One observed run supports the lifetime repair;
two more runs and the remaining display/service fixes are still required.

## GOP startup gate: missing boot argument fixed

Disassembled the actual prelinked base start body at 0xfffffff0057872e8.
The provider cast failure prints the warning at 0xfffffff005787858, then
continues at 0xfffffff005787860; it does NOT return failure there. Earlier
claims identifying that warning as the fatal rejection were incorrect.
The later virtual hardware startup calls slot 288, verified to point at the
owned GOP override. That override parses offline-legacy-gop, default 0, and
returns false unless it is 1. pack-log.py omitted this flag entirely.

The display-probe packing option now adds exactly one offline-legacy-gop=1,
with duplicate detection. Candidate `staging/amethyst-gop-enabled/Mu-amethyst-gop-enabled.img`
SHA256 `08274b05d8a53fbe212fb83d9dedc7699a95dec1059cf139d182a6f0f316bc52`,
80310272 bytes, reuses the identical prelinked owned-display kernel and UUID
`6B991CF1-83ED-3ED5-B6CF-C39FF1E1561B`. No kernel rebuild or layout change.
Pack checks PASS. Independently extracted both images: kernel byte-identical,
TOML differs only by ` offline-legacy-gop=1`, exactly once.

Prediction: hardware startup can now execute GOP mapping validation instead of
returning at its disabled gate. Successful display publication, SpringBoard and
watchdogd recovery are NOT established and require the next hardware log. The
AppleARMIODevice warning may remain even if startup succeeds.

### GOP-enabled run 1

Read `result-gop-enabled-1/untagged-26cbe445`: 43 rounds/slots, uptime 84 s,
errors 0, GPT 1/1. No `start_hardware failed` in msgbuf. Latest display state
starts `4c4 a98` (1220 by 2712), and mfb clients appear, including nine calls
from pid 57. msgbuf records IOMobileFramebufferUserClient display-type queries.
No SpringBoard SERVICE_EXIT or USER_BRK appears in this saved run. This supports
successful hardware-start progression, not verified desktop presentation.

watchdogd still traps at per-process addresses ending in b0c, then exits on
signal 5. The user transcript supplies the final critical-exit panic; the
persisted tail contains failed watchdogd coredump creation with ENOENT.
Creating /private/var/cores would not fix the daemon's original trap.

Investigated the available extracted watchdogd (UUID
04A420F0-C003-380E-8BD0-D9B24150A0E7). It contains `IOWatchdog`, failure-to-find
the watchdog KEXT, IOServiceOpen failures and VM-specific handling strings.
This suggests a service/backend investigation but is not a resolved call site:
without the process Mach-O load address, ASLR slide is unknown. In particular
naively interpreting b0c as unslid 0x10000bb0c lands inside an import stub, not
BRK. Do not fabricate the slide or claim a specific assertion from these PCs.

### Watchdog trap narrowed by page-offset search

The user's proposed low-12-bit search finds one BRK at file offset 0xab0c,
inside __text, encoding 0xd4200020 (BRK #1), matching ESR 0xf2000001 and all
captured PCs' low bits. Unslid VA is 0x10000ab0c. The preceding cold block at
0x10000aaf8 loads string VA 0x10000ce37, calls stub 0x10000b4b4 (indirect symbol
__os_crash), then traps. The string is:
`connect_to_kext() unexpectedly failed - unable to lookup watchdog KEXT service`.
Caller at 0x10000682c branches into this cold block. This is strong candidate
identification, not independently observed process slide/binary UUID at runtime.

SpringBoard definitely launched: msgbuf line 388 records SpringBoard[26], and
line 391 records its CPU-wakeup-limit warning. Absence of later exit messages
is not proof of a responsive UI.

Asteroids' BootWatchdogPolicy defaults sheng-disable-watchdogd to 1 and writes
launchd disabled.plist on Data, disabling com.apple.watchdogd/com.apple.watchdog.
This is a temporary unavailable-backend policy, not an IOWatchdog implementation.
Do not copy its Data reset, job-disable lists or storage destinations. No rootfs,
Data or launchd-policy modification made in this investigation.

## Temporary watchdog-off RAM seed experiment

macOS Actions run 41241364966 succeeded using the exact original seed artifact
from run 41169591242. The script verified original raw and packed hashes, edited
a copy and retained APFS volume UUID A173DCC8-1F31-46ED-9F67-DB2A68FC25DE.
Packed replacement SHA256:
`4c98dd2aa2e6ed3bc9d1a3df0b305404939e271c1668edf72254df283e888494`.
Only com.apple.watchdogd and com.apple.watchdog are added/changed in launchd's
disabled.plist dictionary. Existing overrides are preserved. No physical device
or on-device root image is modified; this replaces the volatile Data seed.

Image: `staging/amethyst-watchdog-off/Mu-amethyst-watchdog-off.img`, 80318464 bytes.
SHA256 `08528210e6633fd5856b2bc4d22ef69a333fa21eeb46f987a42a0b6d54e44b0d`.
Kernel UUID remains `6B991CF1-83ED-3ED5-B6CF-C39FF1E1561B`.
Independent payload extraction confirms kernel, complete config, ADT and
trustcache byte-identical to GOP-enabled; only scratch-data.img differs and
equals the verified Actions artifact. Packer round-trip checks and override
self-check PASS. Hardware untested.

Prediction: watchdogd should not launch and cannot trigger this particular
critical-exit panic. Missing watchdog supervision is a deliberate temporary
limitation, not a fix or hardware watchdog implementation. Revert by booting
amethyst-gop-enabled. Retire the override when a real backend is implemented;
keep it out of any production/safety validation.

### First watchdog-off experiment failed

User transcript still shows watchdogd spawning/restarting and critical-exit
panic. Therefore the first seed override did not disable the job; packing
verification was not proof launchd accepted the policy.

The transformer omitted disabled.migrated. Asteroids BootWatchdogPolicy.c
creates this marker after writing the root-owned override. UniXNU's exact
launchd emulation check (scripts/check-launchd-watchdog-policy.py) explicitly
models the marker in the override loader. This is a concrete omitted input,
not yet hardware-confirmed causality. Transformer now creates the marker and
sets both files to root ownership/mode 0644, refusing symlinks. Original seed
is reused; no physical-device writes. Replacement Actions run 41260781275.

### Corrected override candidate v2

Run 41260781275 failed its root-ownership assertion. Added hdiutil `-owners on`
rather than weakening that check. Actions run 41260884740 then passed the
policy/marker ownership, plist read-back and unchanged volume UUID checks.
Downloaded seed SHA256
`40d76e6f2a856f92ca999f07a38e7c0a89097e04223dde3ae74e0bb1c047545e`.

New image `staging/amethyst-watchdog-off-v2/Mu-amethyst-watchdog-off-v2.img`,
80318464 bytes, SHA256
`d8ebde57e7f9d769970f3f9ae31026f0afb84fc2648710c398eb9aaac1983f00`.
UUID remains `6B991CF1-83ED-3ED5-B6CF-C39FF1E1561B`. Independent extraction:
kernel, config, ADT, trustcache identical to GOP control; only RAM seed differs
and equals the corrected artifact. Packing checks PASS. Hardware untested.
Old images retained. Prediction remains no watchdogd launch; a recurrence
requires inspecting the mounted override and migration path, not suppressing
the kernel panic or declaring the backend implemented.

### Watchdog-off-v2 run 1: seven-minute hardware observation

Read `result-watchdog-off-v2-1/untagged-281d62aa`: 226 rounds/slots, uptime
451 s, write_errors=0, GPT 1/1, state=2. msg_bytes=131072: the message ring is
full and early boot messages have wrapped out. No watchdogd or kernel data-abort
messages in retained tail. Five explicitly labelled SpringBoard signal-5 exits;
repeated SpringBoard PC 0x1b481c744. Other processes also trap, so not every
occurrence of that PC can be assigned to SpringBoard without PID correlation.
Bluetoothd exits signal 0, not SIGTRAP.

Latest state: dimensions 1220x2712, scanout prepare calls=0, notification regs=0,
client pid56 calls=15/fails=12. Selector6 returns success once; selectors
12/22/27/29/33/50/57/74/79/84 report 0xe00002c7, selector15 0xe00002bc.
Display properties report controller/panel power false. These require exact
selector ABI/return-code analysis; do not label all such results fatal.

## Logical display PM gate experiment

NativeLegacyDisplay::startDisplay returns before PMinit/registerPowerDriver
unless offline-legacy-pm=1. logicalPM stays false; kickOnce also requires it.
Current arguments omit this flag. Expose opt-in pack-log.py --display-pm,
requiring --display-probe, adding only offline-legacy-pm=1.

Candidate `staging/amethyst-display-pm/Mu-amethyst-display-pm.img`, SHA256
`0247c31855a15c093b273f0ffe9618db1eecfb6823200434e87f622a78c25e97`,
80318464 bytes, UUID 6B991CF1-83ED-3ED5-B6CF-C39FF1E1561B. Packed and
independently extracted: only TOML changes, exactly the single PM argument;
kernel, ADT, trustcache and RAM Data seed identical to watchdog-off-v2.
Hardware untested. This is logical IOKit PM for scanout scheduling, not panel
power hardware control. Transfer/validation remain disabled, so no full UI
prediction. Check whether display startup still succeeds and client failures
change before combining further gates.

### PM run 1: registration succeeds, client failures unchanged

Read result-display-pm-1/untagged-2834ba32: 169 rounds, uptime 336 s,
errors 0, GPT 1/1. Latest properties now contain IOPowerManagement with
DevicePowerState=1, CurrentPowerState=1, MaxPowerState=1, CapabilityFlags=0x8000.
State word at offset 5836 changed to 1. Thus the PM experiment succeeded in
registering logical power, but not in resolving UI startup. Controller/panel
properties remain false; do not equate those with hardware panel state.

Client pid56 remains calls15/fails12/regs0; prepare calls=0. Same failing
selectors as watchdog-off-v2. SpringBoard repeatedly traps at 0x1bf0c4744
(ESR 0xf2000000), low page bits 0x744 matching the previous run but different
shared-cache slide. No direct runtime slide recorded.

Located `failed to find the main CADisplay` string in exact extracted cache
subcache .09 at file offset 0x6323ecc, preferred VA 0x19f453ecc. This is NOT
yet a resolved reference from the observed trap: nearby literal/indirect
references and runtime mapping still require analysis. Do not identify this
as the current assertion merely because the string exists. No next image built.

### SpringBoard mode-configuration assertion identified (host evidence)

Intersected exact encodings at page-offset-matching candidate addresses for
three PM-run traps: 0x1bf0c4744 BRK#0, 0x1e15ea4f8 BRK#1, and 0x1a0fd52f0
BRK#1. Candidate counts respectively 2/6323/114 within nonnegative slides below
0x40000000; exactly one common slide 0x1fcf4000. Runnable
`.scratch/ufs-audit/check-springboard-slide.py` PASS. This is cross-instruction
inference, not a runtime VM-map observation.

SpringBoard preferred PC is 0x19f3d0744. The exact cache's local symbols resolve
it to `-[FBSDisplayMode _initWithWidth:height:preferredScale:scaleOverride:refreshRate:gamut:hdr:rotation:virtual:validityCheck:]`
+0x244 (method starts 0x19f3d0500). BRK#0 follows the validity failure path.
CFString at 0x1e7a9b008 resolves to string 0x19f4522d4:
`FBSDisplayMode cannot be configured as requested : %@`.
Thus the current failure is NOT established as missing main CADisplay.

Disassembly validates width/height against 0/-1, preferred scale and scale
override, refresh-rate/scale relationships, and gamut/HDR/rotation ranges.
The capture has no method-entry arguments, so which condition fails remains
unknown. Never bypass this assertion or invent acceptable mode values. Next
trace caller mode construction and actual framebuffer/MobileGestalt values.

### Mode-value evidence candidate

Added normal userspace-BRK evidence in sleh-trap.c using the recorded sleh.o.json
compile recipe: BRK immediate zero copies 80 bytes from user x19 with copyin,
reports status and raw words at offsets 8..72, plus x19..x28 one per line.
No direct EL0 dereference, assertions bypassed, exception-delivery changes or
userspace executable modifications. The failing FBSDisplayMode initializer
stores width/height at +8/+16, refresh at +24, doubles at +32/+40 and enum
values at +48/+56/+64. These words expose which input fails without choosing a
default value or changing the presentation gates. Other BRK0 contexts may have
a different x19, so retain the raw labels/copy status.

Candidate staging/amethyst-mode-evidence/Mu-amethyst-mode-evidence.img,
SHA256 68c275e9869274fc732e97aaefb1ab9591685983546455abd8fbe435098da06f,
UUID 573D4197-098D-369B-B6FC-CA5B03AD0204, 80318464 bytes. Compilation,
link exit 0/no undefined symbols, prelink and packing PASS. Uses same PM/GOP
arguments and corrected watchdog-off seed. No pe_init.o rebuild. Hardware
untested. Pull soon after the first SpringBoard exit to avoid ring wrap.

### Mode evidence run and explicit scale-policy test

Read result-mode-evidence-1/untagged-2536b425: 85 rounds, uptime 168 s,
errors 0, GPT 1/1. SpringBoard pid27 and subsequent launches report copyin=0
with width=1220, height=2712, integer preferredScale at +24=0, double
scaleOverride at +32=0.0, double refreshRate at +40=60.0, gamut=1, HDR=0,
rotation=0, virtual=0. Captured x24=0, x25=1220, x26=2712, x28=1 corroborate
the object values and strict validity-failure path. Photos _X8A0120.JPG and
_X8A0121.JPG remain beside the mode-evidence image; inspected latter shows
text console only.

Zero preferredScale is the next hypothesis, NOT yet proof of invalidity:
the validator has a separate zero-preferredScale branch and the scale helper's
return semantics/class comparison still need qualification. Do not claim that
all zero defaults are forbidden solely from their values.

Existing native_legacy_gop_mapping_observed can publish/match actual UI scale
in /chosen, but defaults offline-legacy-unit-scale to zero (disabled). Test the
explicit native-pixel policy 1 through its existing checks, not an assertion
bypass. Candidate staging/amethyst-display-scale1/Mu-amethyst-display-scale1.img,
SHA256 d078bd614571338c811a7143e1f31f30aec864f95a75f25f0c789316260417d0,
UUID 573D4197-098D-369B-B6FC-CA5B03AD0204. Independent extraction PASS:
only config difference from mode-evidence is offline-legacy-unit-scale=1;
all four payload files identical. Hardware untested. Prediction: preferred
scale becomes 1 if the platform query consumes /chosen. If unchanged, trace
MobileGestalt/cache consumption rather than trying random scales. Transfer
and validation flags remain unchanged.

### Scale1 run: swap submission reached; validator gate next

Read result-display-scale1-1/untagged-2257d941: 150 rounds, uptime 299 s,
errors0/GPT1/1. Retained tail contains repeated `verify_swap failed`, no
SpringBoard USER_BRK or exit lines. Early messages wrapped out, so absence
does not prove no earlier crash. Latest pid56 framebuffer client: 3317 calls,
1663 failures, selector4 1649 successes, selector5 1649 failures 0xe00002c2.
No scanout preparations. This is new swap activity after scale publication.

validateSwap defaults disabled and only accepts bounded supported requests
with offline-legacy-validate=1 and logicalPM/primary/mapping present. Enable
that existing validator alone; do not force acceptance or fabricate a surface.
Candidate staging/amethyst-display-validate/Mu-amethyst-display-validate.img,
SHA256 7a2641433ba13c6ead3466df8e0659da9c45c8246c06ce13848cfe367d6c6529,
UUID 573D4197-098D-369B-B6FC-CA5B03AD0204. Pack/extraction PASS: identical
four payloads, config differs only offline-legacy-validate=1. Hardware untested.
Transfer/completion remain unchanged. Prediction: supported requests can now
pass validation; unsupported formats/layers/geometry must still fail. Not a
prediction of presented frames. Pull early to retain useful transition evidence.

## First visible Apple UI: CPU presentation run 1

User reports a white UI with small `press home to upgrade` text, slow arrival,
and no touch response. This is visible Apple UI, not a desktop/unlock/input claim.
Image amethyst-cpu-present SHA256
af4d99934bb18ff22de2a1ea1153f3f986f9bba9d125d8dd1da515abe96c5e05,
UUID E37ABB7B-1B56-31D9-9351-90EDCD61DEDB. Preserve as first UI-working baseline.

Read result-cpu-present-1/untagged-31032ee1: 149 rounds, uptime299s, errors0,
GPT1/1. Latest state generation/completion counters=6/6/6. Six successful
selector4/5 submissions and swap timeline records completed at 2000,2338,1752,
1880,99,108 ms after each submission. Prepare calls10/failures0. These are
end-to-end observed submission-to-transfer/completion delays, not isolated
memcpy timings or total boot duration. First-frame scans (scanBudget32), CPU
copy cost and scheduling are hypotheses pending separate measurements.

Scale1 is explicit native pixels per point and explains small UI; scale2 is a
separate UI-policy test, not hardware pixel-density fabrication. Touch/HID calls
were deliberately stripped by compile-display-provider.py and latest log has
touch edge installed0/reports0. Do not enable Asteroids touch constants on
Amethyst without controller/transport/GPIO evidence. White upgrade prompt does
not establish touch or Home-button support. No new image yet.

### Validator run 1: guarded abstract slot 345 reached

Read result-display-validate-1/untagged-3309a8f6: 98 rounds, uptime194s,
errors0/GPT1/1. Selector4 and selector5 each succeeded once. Swap timeline W1
has neither offer nor completion; prepare calls remains zero. Photo
_X8A0122.JPG shows NativeLegacyDisplay's deliberate leaf=345 backend guard,
not a kernel data abort. Preserved photo; enlarged grayscale crop in
.scratch/ufs-audit/validate-panic-crop.png. Exact caller digits remain subject
to doubled-console transcription; do not assign an address from the photo alone.

Scanned actual __PLK_TEXT_EXEC and disassembled slot345 dispatches within the
original family: 0xfffffff00578b640 and 0xfffffff00578b77c are no-extra-argument
boolean predicates (ldr [vptr,#0xac8], blr, tbz w0,#0). Another at
0xfffffff00578c1c4 calls it during a diagnostic/report path. This establishes a
boolean use at two sites, NOT the semantic meaning or a valid return value for
the GOP backend. Slot345 is pure virtual in the original class and guarded in
both our source and inspected Asteroids source. Replacing it with constant true
or false without tracing its downstream actions would fabricate a capability.

The validate-only experiment exposed an incomplete backend contract. Transfer
flags remain off; do not ship a guard bypass or claim validation alone suffices
for presentation. Next inspect full callers and real concrete implementations
to qualify slot345 semantics and expected state. No replacement image built.

### Slot345: last-client cleanup, not a direct validation callback

Decoded the surrounding original string references: the two predicate calls
at 0xfffffff00578b644 and 0xfffffff00578b780 are inside
`void IOMobileFramebufferLegacy::disp_has_no_clients_gated()` (string at
0xfffffff0045fc6e1). A true result gates additional virtual slot339 work
followed by other operations; false skips that branch but continues cleanup.
Neither proves that a constant false implementation is correct for every
slot345 caller, including the diagnostic call at 0xfffffff00578c1c8.

The enlarged screenshot also shows SpringBoard pid27 exiting signal9/code9
before the guard panic; its termination cause is not established. Consequently
the guard can be a secondary cleanup failure after the sole accepted swap
never reaches presentation/completion. Validation success does not establish
a functioning presenter. Transfer remains disabled, and calling the complete
pipeline hardware-qualified would be false.

Checked rootstockos/GopFramebuffer asteroids 046b7c35 and newer main
33e941a878dcf01b98dbd9160d798bd56e7551ea: both retain LEAF(345), with no
ready-made implementation. No guard bypass or new boot image produced.
Further work must qualify last-client cleanup and the actual supported
presentation/completion path before another validation-enabled device test.

### Fail-closed pipeline acceptance repaired (host only)

NativeLegacyDisplay::validateSwap now requires validation=1, transfer=1 and
either cpu-present=1 or retire=1 before it can accept any request. Previously
validation=1 alone accepted a request even though kickOnce immediately returned
because transfer was disabled. That is an internally inconsistent configuration,
and prevents this backend from making progress on its accepted request.

Factored only the four-flag predicate into NativeLegacyRequest.h and exhaustively
tested all values 0..2 in the existing request-state.c test (81 combinations).
`cc -std=c11 -Wall -Wextra -Werror .../Test/request-state.c` and executable exit0.
Recorded IOPlatformExpert.cpo.json recipe compiles NativeLegacyDisplay.cpp exit0
to display-recovered/NativeLegacyDisplay.pipeline.o; old late-linked object
preserved. This object is not linked/packed/shipped yet.

This prevents accepting work with the pipeline explicitly disabled. It does not
implement slot345, prove the runtime queue ABI, guarantee successful copies,
or qualify producer synchronization. The transfer path maps a source read-only,
checks bounds/format and uses store barriers before completion; no evidence yet
that every system producer satisfies its completed-CPU-surface assumption.
BootScanoutOwnership.c's handoff only tracks its own writer flag, not the native
console writer: exclusive framebuffer ownership still needs qualification before
calling that path safe. No additional hardware test requested at this stage.

### Native video-console release added (host only)

BootScanoutOwnership.c now calls initialize_screen(NULL,kPEReleaseScreen)
in its late prepare hook, only in normal thread context with interrupts enabled.
XNU video_console.c handles release by gc_enable(FALSE): clears gc_enabled,
disables backend drawing and takes/releases VCPUTC_LOCK. vcputc_options checks
gc_enabled again under that lock, so the release drains an in-flight ordinary
video-console character before ownership is published. Ownership publication
precedes unlocking the hook's own writer flag; subsequent preparation returns
without repeating release. Early boot_progress_stage remains parameter-only.

Compile-ownership and check-scanout-release.py PASS; request-state C test exit0.
This qualifies the normal video-console route only, not independent firmware
debug/framebuffer sinks, panic drawing, later console reacquisition or GPU
producers. No presentation/cleanup implementation or slot345 bypass added.
The compiled ownership object changed locally; recorded old images remain
unchanged. Do not relink the shared filelist and call it a one-variable image
without recording both pending source changes (pipeline predicate and handoff).

### Slot345 ABI narrowed to timing-enable query

Further original-code tracing connects slot345's return bit directly to the
`TimingEnable = %d` argument in swap_wait_gated's diagnostic at
0xfffffff0045fc80c. The dispatch at 0xfffffff00578c1c8 receives only self;
its x0 result is masked to one bit and printed alongside HPD, DPLink and
power states. The two last-client cleanup callers gate slot339 transitions
with parameters 7/0 or 6/0 on that same return bit. Runnable
check-slot345-contract.py verifies exact call/test and diagnostic bytes: PASS.

This establishes the call ABI and timing-enable meaning but not how GOP's
firmware-owned scanout should implement it. The software vsync timer is not
hardware timing enable; likewise a retained framebuffer mapping is not proof
that display timing is active. Returning either constant to bypass the guard
would remain an unsupported behavioral choice. Slot339's hardware transition
contract must also be qualified before selecting a stateful timing reply.
Original pe_init.o and existing packed images remain unchanged.

### Slot339 qualified as dummy-swap reprogramming

Slot339 target 0xfffffff00579e150 forwards (self,uint32 intent,swap pointer)
to 0xfffffff00579de90 with fourth argument zero. The helper names itself via
string 0xfffffff0045fe014:
`IOReturn IOMobileFramebufferLegacy::reprogramDisplayWithDummySwapAPT_gated(IOMFBSwapRequestIntent, IOMFBSwap *, uint64_t)`.
It rejects intents >=25 and unsupported state, selects separate buffers for
intent6/7, stores the intent at request+0x17c and dispatches via slot178.
It is not a hardware power setter. check-slot345-contract.py now verifies the
forwarding, request-intent store, dispatch and diagnostic name bytes: PASS.

Implication: returning true from TimingEnable during cleanup can inject dummy
swaps; returning false suppresses those only, not every other timeout/cleanup
operation. Neither is yet qualified against a concrete firmware-GOP lifecycle.
Ownership and scanout-release checks also PASS. No new image, no semantic
slot345 implementation, no pe_init.o rebuild. Next implement an explicit
supported dummy-request policy alongside actual transfer/completion rather
than accepting generic timing work or bypassing the guard.

## CPU presentation candidate: complete gated pipeline, hardware untested

User requested continuation through producing an image. Built
`staging/amethyst-cpu-present/Mu-amethyst-cpu-present.img`, 80318464 bytes,
SHA256 af4d99934bb18ff22de2a1ea1153f3f986f9bba9d125d8dd1da515abe96c5e05.
UUID E37ABB7B-1B56-31D9-9351-90EDCD61DEDB. Prelinked kernel SHA256
ffd9eb12cc7c07de5a1ca53980685a543944ed1d53d1d5631977a664e65f48df.

Includes pending pipeline-acceptance predicate, no-eviction validated tracking,
and native console-release handoff. Uses separate NativeLegacyDisplay.pipeline.late.o,
preserving old late object. Enables transfer=1 and cpu-present=1 together on top
of scale1/PM/GOP/validation and corrected watchdog-disabled RAM seed. Slot345
remains guarded: no fabricated timing state or cleanup bypass. No GPU flag,
software-vsync rate, root policy, partition or pe_init.o change.

Host checks: request-state test exit0; ownership, scanout-release and slot345
contract checks PASS; recorded display recipe compile exit0; link exit0/zero
undefined symbols; prelink/pack checks PASS. Independent extraction confirms
only kernel and TOML differ from display-validate, with precisely the transfer
and cpu-present arguments added; ADT/trustcache/seed identical. Original
pe_init.o SHA256 still 79f82aa98c6e7ad2f2e31982475831fc6c4d5189c7cca8cf2c3fa4b92299e11c.

Prediction: supported validated requests can reach real GOP stores and retirement,
recording prepare/transfer/completion. This is not proof of working system
producer synchronization or independent debug-sink exclusion. Screen may become
black after native console release; only pulled diagnostics distinguish a blank
swap from a stalled copy. Slot345 may still panic if last-client cleanup runs.
Do not interpret a black screen or absence of text as successful UI. Pull early
after a visible change or failure; preserve photo beside the candidate.

### Accepted-request tracking overflow fixed (host only)

The eight-entry validated array silently replaced entry0 on overflow, forgetting
an accepted in-flight request and making isValidated reject it when its turn
arrived. It now refuses the ninth distinct request (reason6), preserves all
existing entries, accepts duplicate registration, and reuses only empty entries.
This is not claimed as the cause of the one-frame validation-only failure.
The helper stores borrowed identity pointers, not new ownership: original-family
queue ownership and removal/cancellation paths still need qualification.

Added null/full/duplicate/no-eviction/reuse tests alongside the 81 pipeline
configuration cases. Existing test compiled and ran exit0 both normally and
with AddressSanitizer+UndefinedBehaviorSanitizer. Display source compiled from
recorded recipe exit0. git diff --check clean. Pending compiled object remains
separate from old late-linked object; no image packed and no slot345 bypass.

Photo `_X8A0119.JPG` retained beside watchdog-off-v2 image shows the lit text
console, no visible graphical UI. Source image is unchanged SHA256
d8ebde57e7f9d769970f3f9ae31026f0afb84fc2648710c398eb9aaac1983f00.
Long kernel/logging survival is established for one run; UI operation is not.
Next: identify the shared-cache image/slide for SpringBoard's trap and correlate
the failure with framebuffer contract/power/notification state. No new candidate.
