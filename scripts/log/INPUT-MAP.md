# Amethyst native input mapping (not enabled on hardware)

| Input | Source hardware | Intended HID meaning |
| --- | --- | --- |
| Touch | Goodix BerlinD, QUP0 SE0 SPI CS0, IRQ70/reset87 | Direct multitouch, retain contact IDs0..9 |
| Volume up | volcano-pmic-overlay.dtsi: pmxr2230 GPIO6, active-low,15ms debounce | Consumer page0x0c usage0xe9 |
| Volume down | pmk8550.dtsi: PON HLOS0x1300, resin interrupt bit6 | Consumer page0x0c usage0xea |
| Power | pmk8550.dtsi: PON HLOS0x1300, power interrupt bit7 | Consumer page0x0c usage0x30 |

Volume-up is a PMIC GPIO, not a TLMM pin. All side-button reads require SPMI
transport/PMIC identification; never use pin numbers from Asteroids. Bark
inputs are reset/safety events, not extra physical buttons: do not forward them
as duplicate power presses or alter PMIC reset policy. No Home substitution.

AmethystInputMap.h implements only key-code/coordinate conversion with host
tests. It does not access registers, create HID services or generate events.
Power usage interpretation by the selected iPadOS HID stack still needs testing.
TouchState coordinates use the existing30480x20320 extent; panel normal-mode
pixels map monotonically to those endpoints with no Android x100 multiplier.
Orientation is native portrait unless hardware evidence requires correction.

check-input-map.c passes with ASan+UBSan. Physical input remains unimplemented:
pending QUP clock-stage hardware result, then rail/reset/mux/DMA qualification
and live packets; separate SPMI read-only side-key state discovery.

## Button transport survey candidate (2026-11-03)

Parsed local Amethyst ADT `.scratch/ufs-audit/new.dts` (raw ADT despite suffix):
core0xb400000/0x3000, cnfg0xb42d000/0x4000, observer0xb440000/0x80000,
channels0xb500000/0x400000, interrupt0xb4c0000/0x10000.
These ADT ranges are BUS-RELATIVE, not physical. arm-io adds0x1000000:
physical core0xc400000, matching volcano.dtsi lines2542-2559. The first
survey incorrectly treated0xb400000 as physical and panicked. The copied
UEFI helper's core address agrees, but its SID1 GPIO assumption is unverified.

`--buttons-survey` runs independently of touch flags, reads core version,
features and bounded v7 APID map, retains matches for PID01/13/8d across all
SIDs. No observer commands, PMIC writes or HID events. Unsupported geometry
is logged and refused. Next hardware result must qualify layout and candidates
before PMIC identity/status reads. No button press required for this survey.
ADT bounds self-check and six ARM64 compiles pass; link/prelink/pack pass.
Image `staging/amethyst-buttons-survey/Mu-amethyst-buttons-survey.img`, SHA256
`91fbdc1ce747d5e5337bc5910a362e45a5986076c50a85e0d40ddde14e2a60fa`,
kernel UUID `FFBCD78D-D398-3226-BC2C-B0747125973D`. Hardware untested.

### Survey run1 panic and v2 address correction

Run1 panicked (user photo); recovered `result-buttons-survey-1/untagged-146467050`
contains only initial checkpoint, no button lines or durable panic. Wrong
physical core address is a verified code defect; exact FAR/PC not readable
from photo. V2 maps physical0xc400000 instead of bus-relative0xb400000.
Source evidence: local volcano.dtsi lines2542-2559 and ADT arm-io translation.
Regression and ADT bounds checks, six ARM64 compiles, link/prelink/pack pass.
Same read-only survey; no PMIC commands or HID events. Hardware untested.

Image `staging/amethyst-buttons-survey-v2/Mu-amethyst-buttons-survey-v2.img`,
SHA256 `fbdc4eb8ae82d213bb3d4d32584f9360abe439639df43c7fab2c22b00c2f05bc`,
kernel UUID `1098F618-9B7C-30B1-9DAD-A587934B85D4`.

### Survey2 passed; identity candidate

`result-buttons-survey-2/untagged-2bee87ec`: uptime222s,108 rounds,errors0,
GPT1/1. Version70010000 features80060380 count896; PON013 APID3,
GPIO18d APID31; duplicate78d entries prohibit first-match assumptions.
Survey repeated because its guard preceded the touch once-guard; fixed with
independent atomic once guard. New --buttons-identity reads PID01 offsets0..5
on each uniquely mapped SID, using EE0 observer at physical0xc440000.
v7 channel stride0x20, EXT_READL opcode1, offset<<4, one byte;1000 polls
with1us delay, stop on first error, no PMIC writes. Reference: Linux
drivers/spmi/spmi-pmic-arb.c offset_v7/fmt_cmd_v2/read_cmd/status handling.
No status polling/HID yet. Model tests cover success/error/timeout/bounds with
ASan/UBSan exit0; snapshot check and ARM64 compile/link/prelink/pack pass.
Hardware untested. Image staging/amethyst-buttons-identity/Mu-amethyst-buttons-identity.img,
SHA256 `3f8eb4418f69cffb2adf78e922284fe000f56393fab80f41d2e632f9562dec79`,
UUID `2B0CC2AE-AA26-3B7C-B3EA-14421626EA25`.

### Combined button path work

AmethystButtons.h implements native consumer bits2/4/8, two-sample debounce,
press/release transitions, and release on read failure. check-buttons.c passes
with ASan/UBSan (exit0). Not wired to hardware yet. Existing reference HID
builder's IOHIDFamily hash matches7e4c5104…; its ABI checks reached compilation,
which failed because resolved tree lacks drivers/ShengHidInput/NativeHidInput.cpp
(exit1). Source fetched to .scratch/ShengHidInput for inspection. No new boot
requested; pending complete board transport/HID integration, not another
identity-only image. Reference ButtonProbe SID assertions still need checking
against primary Amethyst PMIC DTS before use.

HID ABI builder now passes instruction identity, native-base imports, call
boundaries and exact owned vtable on the local reference. Resolved build tree
uses .scratch/ShengHidInput via symlink; build-arm-driver restored the recorded
missing-designated-initializer waiver, HID builder uses installed llvm-objcopy
instead of Homebrew. These are local build-tree adaptations, not yet a portable
device build entry point. Compiler emitted missing sysroot 'iphoneos' warning;
qualification completed exit0.
AmethystButtons.cpp adds a polling worker and native_buttons_set_sink, mapped
observer reads, unique APID selection, two-sample debounce, error release and
stop-on-first-transport-failure. Initial ARM64 compile exit0. GPIO PPID must be
explicitly qualified; no implicit SID1 inherited from the reference board.
Not linked/published yet: HID startup adapter and primary GPIO SID evidence
remain required. No working-buttons image exists from this work.

AmethystButtonsHid.cpp now compiles against recorded C++ recipe: bounded HID
family loading/resource lookup, class-size check, lifecycle attach/start,
failure cleanup and no synthetic Home request. Requires lock-safe
OSKextRegisterBoardClasses; symbol absent from current kernel, so adapter
refuses startup rather than using the known deadlocking bare preModLoad path.
Reference patch is .scratch/asteroids-review/patches/xnu/zzyg-board-class-registration.patch.
Both board worker and startup adapter ARM64 compile exit0. They remain unlinked
pending kernel registration support and primary GPIO SID evidence. Reference
ShengHidInput source revision71a40e76daeae1bb4127546f96ffa7339c7a06ff.

Lock-order registration patch now saved as board-class-registration.patch;
reverse dry-run matches the prepared source (exit0). OSKext-buttons.o compiled
with unchanged recorded OSKext.cpo.json arguments, only output redirected
(exit0). Original OSKext object retained. Candidate not linked yet. Primary
Amethyst PMIC SID evidence remains unresolved; repository searches did not
locate the required PMIC overlay. Do not enable a guessed volume-up SID.

### User-supplied reference repositories (2026-11-04)

Inspected rootstockos/UniXNU-sm7635-common at
c9e36f6da28e0264b3148f7d4ed9de5f3d96ce42: dts/sm7635.dtsi lines59-75
confirm physical SPMI ranges and PMK8550 SID0. README identifies its primary
source as NullDebris/android_kernel_nothing_sm7635-devicetrees revision
e0eb1dc2235850f9f966bfb3294967d06c00a0f1, validated on Nothing3a, not Amethyst.
Fetched rootstockos/UniXNU-asteroids main bbf26b0; changes since our541674c
reference affect audio/logs/record, not ButtonProbe.cpp. Its board.toml selects
ShengHidInput and sheng-buttons=1; documented T36a iOS functionality is
Asteroids evidence only. rootstockos/UniXNU asteroids-dev pinned at
eb5bf598791aad81c4e302325bc5b95ccef80473; HID builder retains the same exact
207-slot/208-byte ABI qualification. These supply reusable integration, not
primary evidence for Amethyst volume-up SID or Goodix wiring.

### Combined native button candidate

Hardware file source located: lunik1980-code/xiaomi_amethyst_sm7635-devicetrees
commit c7bccfee7911adef12b9fb7cb697eb82929d9cff. qcom/pmxr2230.dtsi
lines10-12 SID1, lines23-25 GPIO base8800; volcano-pmic-overlay.dtsi
lines39/56 GPIO6 active-low. This qualifies PPID18d alongside surveyed APID31;
it is repository source evidence, not a live identity read.
Linked board worker, HID startup, qualified original HID bindings and patched
OSKext-buttons.o via separate filelist. Original inputs retained. Native HID
requests never generate synthetic Home. Polling10ms, two-sample debounce,
release and stop on read failure; retained transition diagnostics bounded by
snapshot capacity. No PMIC interrupt/reset-policy writes.
ARM64 compilation, HID ABI checks, link/prelink/pack pass; hardware untested.
Image staging/amethyst-buttons-native/Mu-amethyst-buttons-native.img,
SHA256 `31ae9a12051d12a21fe015af66dc4a9bd285e88a190634db0fba14bdce6b1512`,
UUID `F5C35C28-5038-3673-9E49-6871A3FFB4EA`.

### Native buttons run1 and opt-in upgrade-screen Home event

Run result-buttons-native-1/untagged-2e4c8726:249s/121 rounds/errors0/GPT1/1.
User confirms power works; volume unconfirmed. HID families/classes/thread rc0,
PON3/GPIO31, event3 value1 means event driver attached, not an error. Repeated
event3 logging filled48 rows; now only attachment state changes are retained.
--home-once requires --buttons and requests one native Home press/release via
the existing HID timer after30 attached ticks. Side-key mapping unchanged.
Not a setup bypass; display performance remains unmeasured. Host callback
check passes disabled/delayed/single/no-repeat cases; ARM64/link/prelink/pack
pass. Hardware untested. Image staging/amethyst-buttons-home-once/
Mu-amethyst-buttons-home-once.img SHA256
`d90687a43092a2b745fecbf8b671c2f6668de58df34fe6a6a5d31accf1f3bda2`,
kernel UUID `83CB2EC3-1029-34B7-8453-C235E8F76E5D`.

Quiet candidate uses the same prelinked kernel/UUID and Data seed; only removes
explicit -enable_kprintf_spam via --quiet-kprintf. Retains verbose console and
durable log for comparison. Packaging verification passed; no measured boot-time
improvement claimed. Image staging/amethyst-buttons-home-quiet/
Mu-amethyst-buttons-home-quiet.img SHA256
`9e466e33ec2e9ea2f03a224e3fa65ea4631ff430ea9f70d7c63171c7bd2965d2`.
Prior recovered msgbuf shows ongoing userland service failures/container scans,
but late circular logs cannot attribute total early boot time to them.
