> **2026-11-22, D94 (branch `ios27-d94-build-audit`): iOS 27 reaches the lock screen and home screen with GPU, touch
> and smooth frames (b24).** The newest entries (b18-b25) are at the end of this file. The i-series below is the
> iPhone15,4 history.

# Handoff: iOS 27 24A446 bring-up — reaches userspace; blocker is the dyld shared cache

## Status as of i132 (booted, reaches userspace — blocker is the dyld shared cache)

> **Read this first.** i132 is built from the current map **and was booted RAM-only** on `42fe0af2`. It reaches
> userspace: `initproc` starts and then exits with `Library not loaded: /usr/lib/libSystem.B.dylib`
> (`namespace 6` / `subcode 0x1` = `DYLD_EXIT_REASON_DYLIB_MISSING`). That is past the i131 kernel wedge, so the
> execution passed the previous wedge. The cause of the missing library remains unconfirmed; see
> "i132 dyld failure: corrected analysis" below.
>
> i131 **was booted** RAM-only on `42fe0af2` but did not reach userspace: it wedged in the same UNMAPPED loop the
> i111 image hit. Images i112–i130 were not booted (device absent). The i131 analysis is retained below as the
> explanation of the previous wedge.

### The i131 wedge, fully decoded (see `/tmp/linkwork/UNMAPPED_I131.md`)

`logstub.py` turns `_unixnu_fileset_unmapped` into "log and return 0". Its format string is
`UNMAPPED +%llx x0 %llx x1 %llx f %llx %llx %llx s %llx\n`; the `+…` field is `x30` (already
`xpaci`'d) minus the image base. The three `f` words are **a frame-pointer backtrace**: the cave
walks the saved-frame chain and emits each frame's saved return address, also `xpaci`'d and
base-relative — they *are* code addresses (verified: `313a0a4`→`0xa13e0a4`, `3a1d66c`→`0xaa2166c`,
`37ca420`→`0xa7ce420` all land in `__TEXT_EXEC` and disassemble as code). `s` is `x17 - base`, which
lands in `__DATA_CONST` and is **not** an address. `x0`/`x1` are the true arguments (the stub saves
them *before* touching them).

The raw i131 console capture is no longer on disk, so the line below cannot be re-verified verbatim;
the addresses derived from it are independently verified against the shipped image.

Both logged sites resolve to the same unresolved slot target:

| caller | `bl` | stub | slot | slot target (original VA) |
|---|---|---|---|---|
| `com.apple.security.sandbox` `0xfffffff00a148a74` | `0xa148a70` | `0xa167984` | `0x81744f8` | `0xfffffff00aa9d044` |
| `com.apple.security.AppleImage4` `0xfffffff008d13274` | `0xd13270` | `0xd2d9e8` | `0x7e96ad8` | `0xfffffff00aa9d044` |

**What the two call sites are.** The Image4 site is `_IOTaskHasEntitlement(obj,
"com.apple.private.img4.ignition-blob")`, formatting `"caller not entitled to get ignition blob: %d"`
on failure. The sandbox site loads a global (`0x8174870` → `0x7ccb238`), calls `proc_ucred` on its
value and compares that against the object's cached `field_24` (`cmp x24,x0; b.eq`) — a "is this
the current process's cred" test.

**Root cause: the image was built with an outdated map.** The original kernelcache's chained-fixup
stream never contains `0xaa9d044` at either slot:

| slot | original target | original symbol |
|---|---|---|
| `0xfffffff0081744f8` (sandbox) | `0xfffffff00a8e17bc` | `_proc_ucred` |
| `0xfffffff007e96ad8` (Image4) | `0xfffffff00ab98394` | `_IOTaskHasEntitlement` |

A scan of all 532,174 original fixup sites finds **zero** targeting `0xaa9d044`; that VA is the
`_unixnu_fileset_unmapped` stub, which the emitter wrote because that build had no pairing. The
shipped i131 used `matcher_signature bf164371…`; the current map is `311070b7…` and **does** pair
both originals (`_proc_ucred` → `0x7631fec`, `_IOTaskHasEntitlement` → `0x7860f58`). Re-emitting
with the current map binds both slots to real native code (verified by disassembly:
`0x81744f8` → `pacibsp; …; mov x20,x0`; `0x7e96ad8` → `pacibsp; …; mov x19,x1`). **i131 predates the
pairing that fixes its own wedge.**

**`x0 = 0x806c` is garbage from the same missing pairing.** The emitter replaces the original
kernel `__DATA_CONST` with the native one **at the same VA and file offset**
(`0xfffffff007c18000`, fo `0xc14000`). So output VA `0x7ccb238` is native file offset `0x1c3238`,
where the native binary itself holds `0x4280000006c` verbatim. That word is **stale chain residue**:
it is not a native fixup site, it sits in the gap between real sites (`0x71c3230` → `0x71c3260`),
and all 20 copies of it in the native binary are at such gaps — so `place_kernel` copies it and
`rewrite_native_chains` correctly does not touch it. Nothing anomalous is written.

The fix is the **data pairing**, which the current map has and i131 lacked:
`0xfffffff007ccb238 -> 0xfffffff00714cc70  _kernproc`. With it the global `0x8174870` is rebound to
native `_kernproc` (`0x7c50c70`), a zero-filled BSS symbol that the kernel fills during startup:

| image | `0x8174870` word | target word |
|---|---|---|
| `kG.macho` (current) | `0x10000000c4cc70` → native `_kernproc` | `0x0` (BSS — correct) |
| shipped i131 | `0x10000000cc7238` → stale `0x7ccb238` | `0x4280000006c` (garbage) |

Its low 32 bits are `0x806c`, exactly what the log printed. So this is the *same* root cause as the
two stub slots (outdated map), not a third defect. Two earlier
conclusions in this file were built on treating it as a real small-int argument and are withdrawn:
that the callee was "not `proc_ucred` because `x0` would fault", and that the site "could never be
closed because the getter has no native counterpart". The getter *is* `proc_ucred` (zone-require
`& 0x3fff` / zone-id `== 5` / `pr_proc == p` / return `pr_ucred`); native inlines the zone check as
`bl zone_require_ro(5, 0x60, p_proc_ro)`, which is why a literal `and #0x3ff; cmp #5` scan finds 0
native hits and why that scan was not evidence of absence.

**Preventive fix landed (not the wedge cause).** `Emit.rebind()` now resolves *interior* fixup
targets via a new `InteriorBlocks` resolver instead of dropping them into `missing`: same offset in
the native twin, accepted only on verified code identity (masked words with `adrp`/`adr`/`b`/`b.cond`
as wildcards, ≥6 agreeing words, staying inside the native function). Every rejection is counted in
`statistics.interior_blocks_rebound` / `InteriorBlocks.rejected`. It fires **0** times on the current
build (`819f35a2…` unchanged, 56 refs / 447 sites) — it is a guard against a real failure class, not
a fix for i131. 5 new regression tests; suite **72 OK**.

**Trap, do not re-enter:** `/tmp/linkwork/orig_nm.txt` is **not** an original symbol table — it is
byte-identical to `llvm-nm /tmp/i131.shipped.macho`. It has been renamed
`SHIPPED_nm_MISLABELED.txt`. Any "original name" read from it is a shipped-image name. A pairing
derived from it (`0xa8e1610 = OSKext::removeDaemonExitRequests` ↔ native `0x775e750`) was tested and
**refuted**: the two divergent instructions resolve to different strings, and the original bytes at
that address are a different body entirely.

**Trap, do not re-enter:** `/tmp/linkwork/orig_nm.txt` is **not** an original symbol table — it is
byte-identical to `llvm-nm /tmp/i131.shipped.macho`. It has been renamed
`SHIPPED_nm_MISLABELED.txt`. Any "original name" read from it is a shipped-image name. A pairing
derived from it (`0xa8e1610 = OSKext::removeDaemonExitRequests` ↔ native `0x775e750`) was tested and
**refuted**: the two divergent instructions resolve to different strings, and the original bytes at
that address are a different body entirely.

### What i112–i131 changed (offline-verified)

The i111 blocker was unmatched prelink functions. Rather than patching around it, the work went
into the matcher/enricher so kext bindings resolve properly:

1. **`tools/enrich_map.py` rewritten** (`kits/XnuPrelink/`). Four real defects fixed:
   segment classification had never worked (it guessed by address distance); the segment parse
   did not skip sections; data entries were filed into `functions`; unresolvable symbols were
   silently skipped. Added `--overrides` (hand-verified identities the matcher got wrong) and
   `main(argv)`; file handles are closed; it now **fails without writing** on refusal instead of
   emitting a silently-zeroed map. Covered by `test_enrich_map.py` (5 tests; suite is 67 OK).
2. **Measured effect** of the corrected map (measured against the `kF` baseline native build, whose
   report is `/tmp/linkwork/kF.json` — *not* the shipped i131 image): unresolved closure refs
   **112 → 56**; stubbed sites **1448 → 447**; mapped entries 10085 → 10520 (+435, 2 corrected). A
   diff against the baseline emit is **1070 changed words, all inside bind slots, 0 outside**; segment
   layout unchanged. The **shipped i131 image** reports different totals (23 owners / 321 refs;
   28 owners / 1538 sites) because it was built against a different native kernel
   (`dabe46b4…` vs `a6f7f60a…`) — see `/tmp/linkwork/REPORT_MISMATCH.md`.
3. **`--overrides` corrected one wrong pairing.** Of the 4 got-override sites, `_cs_system_require_lv`
   was mis-paired to `_filt_psetevent`; verified wrong by disassembly and fixed. The other three
   (`_csblob_get_teamid`, `_IOSimpleLockFree`, `_thread_wakeup_prim`) were checked and are correct.

### Current image and how it was verified

`staging/amethyst-ios27-i131/Mu-amethyst-ios27-i131.img`
sha256 `fd36d632261faecee6b5301278b9cf90e48c73745b72b5dd50a1499cfc0338c5`

The image was verified **as shipped**, not from the build log:

- kernel extracted from the container: md5 `95d21db8aa0c0fc34e894ed93f1c84de` == build output
- trustcache `5fc382af540676b90d14308c46ab8fb7` == `/tmp/tc-24A446.trustcache`
- boot args byte-identical to i130 (still includes `sheng-no-lc=1`)
- override slots **decoded** (not name-compared): the 4 override slots point at the correct
  targets (3/1/1/4 slots each); **0** slots still point at the old wrong targets
  `0xfffffff007241288` / `0xfffffff0071f5200`
- `sha256sum -c SHA256SUMS` passes

i130 was likewise extracted and confirmed equal to its own build output
(`2de2b5de5ec8973c94531b08e604f1e0`).

Build command (2nd argument must be a **plain binding file**, not the map):
```sh
GOT_OVERRIDE=…/over-i83.json STACKSHIFT=1 PAGESHIFT=1 \
TRUST=/tmp/tc-24A446.trustcache EXTRA_ARGS="sheng-no-lc=1" \
  scripts/bringup-24A446/build5.sh
```

### i132 is BUILT, booted, and the three bindings are verified

i132 was built from the **stale prelink input** being replaced. The root cause of i131 was not the
emitter at all: `.scratch/ios27-run5/k.macho` (the file `patch-got.py` consumes) still carried the
`bf164371…` bindings. It has been regenerated from the current map:

```sh
cp .scratch/ios27-run5/k.macho .scratch/ios27-run5/k.macho.stale-bf164371   # kept for diffing
cp /tmp/linkwork/kG.macho .scratch/ios27-run5/k.macho                        # current map, 311070b7…
```

`/tmp/linkwork/mapF.json` and the build's `map-final.json` are **content-identical** (same
`matcher_signature 311070b7…`, same 9131 functions / 1389 data), and both were built against native
`a6f7f60a…` — the run7 native that is on disk. i131's recorded `native_sha256 dabe46b4…` no longer
exists anywhere, so its report can never be reproduced.

Build:

```sh
S=scripts/bringup-24A446
GOT_OVERRIDE=$S/got-override.json STACKSHIFT=1 PAGESHIFT=1 \
TRUST=/tmp/tc-24A446.trustcache EXTRA_ARGS="sheng-no-lc=1" \
  bash $S/build5.sh 132 $S/bind.json $S/data.json
# -> staging/amethyst-ios27-i132/Mu-amethyst-ios27-i132.img
#    sha256 f7defcd38589b0334945e17acf425d8894a4e4d98dafe615ec3088759c374265
```

Verified in the **packaged** `k132.macho` (extracted, not the build log):

| binding | i131 | i132 |
|---|---|---|
| sandbox slot `0x81744f8` | `0x8011000003a99044` (stub) | `0x80110000037b1fec` → `pacibsp; … mov x20,x0` |
| Image4 slot `0x7e96ad8` | `0x8011000003a99044` (stub) | `0x80110000039e0f58` → `pacibsp; … mov x19,x1` |
| `_kernproc` global `0x8174870` | `0x10000000cc7238` (stale) | `0x10000000c4cc70` → native `_kernproc` (zero-filled BSS) |

**Slots still holding the unmapped stub in i132: 0.** Both rebound targets disassemble to real
native prologues after all probe patching, so the probes did not disturb them.

### i132 dyld failure: corrected analysis

i132 hardware reported `initproc` missing `/usr/lib/libSystem.B.dylib`. The cause is **not established**.
Retraction: earlier claims that i132 omitted `MNT_UNION`, that the manifest uses the 21H461 UUID,
and that mount/configuration failure explains this error were unsupported and are withdrawn.

In the i132 kernel used for inspection, `_load_init_program` at `0xfffffff00a7a7718` loads an
8-byte constant from `0xfffffff007189f00` and stores it at `[sp,#88]`, the mount request flags;
that constant is `0x21` (`MNT_RDONLY | MNT_UNION`). The separate APFS mount-data flags at
`[sp,#136]` are `1`; confusing these two fields produced the incorrect conclusion. The manifest
call at `0xfffffff00a7a7850` receives configured root UUID `E89DE0E4-B66C-425D-BCD1-90F637F43F1F`
and SystemOS UUID `6948C338-7E76-495E-A9AA-C779BBC15098`. These static observations do not prove
runtime success, filesystem contents, or dyld cache mapping. An initproc failure alone does not
identify the specific cache rejection. Verify using the extracted shipped image before acting.

### IMMEDIATE NEXT ACTION

**Metal44780591353 capacity-only failure:** job136441933074 cancelledzero steps;
annotations runnernotacquired/macOSarm64capacity. glslcfix notexercised. Newserial
addonrun44783163622 https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44783163622
watcher sh_1f9fe06e1001mPWSsMbhjDvoD6 registered with existingkernel44780948861watcher.
No codechanges/duplicatekernelrun/phoneops.

**Nov21 native link config fix6a3b0f3 / kernel44780948861:** actual44707306443
missingnative_legacy symbols caused amethyst_defconfig lacking legacydisplayflag,
not GPUmissingimplementations. GPUprep nowCONFIG_XNU_NATIVE_LEGACY_DISPLAY=y,
existingarchiveforce-load/macros active; parentregressionPASS confirmsGOPbeforeGPU,
exactstorage/Data/payload preservation andfinaldisplay-symbolcheck. Scoped diffPASS.
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44780948861
watcher sh_1f9f19ad2001k1hztxIj7xm0Ci registered. Metal44708357980 failedmissingglslc;
6d90356 installshaderc +command-vcheck; retry44780591353 watcher
sh_1f9efa6c2001njnGEjgRxFmULM active. Bothindependentbuilds pending,no phoneops.

**Metal actualios27 build dispatched44707958724 at a919fd2:** newworkflow uses
documentedpinnedcore/NativeAdreno/NativeMetal/Rust/Mesa helpers, arm64e target,
strictsignedbinarychecks/PACevidence, --publish --no-daemon addon, encryptedlogs
evenfailure. Parentselftest/YAML/allrunblocks bash-n/diffPASS. NoD94trustreplacement.
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44707958724
watcher sh_1f83597870017BqE1JZN7nsBCQ registeredalongside kernel44707306443watcher.
Both independent builds pending; actualnewbinaryimportaudit/signatures stillneeded
before packing. No phoneops/newboot. Commit onlynewworkflow/helper, usereditskept.

**Policies44695786913SUCCESS byteidentical, GPUkernel resumed44707306443:**
download /tmp/opencode/aston-audit/policies44695786913. Both OS/SystemOS trustedkey,
manifest.header,manifest.bin byteexact vs retainedroot-export ios27-policies.
Verification images4b8719fe.../f6d10101..., UUIDE3F4.../8B599..., keysame4a3f9012.
No changedroot/signingidentity. NEWfullGPUkernel same8630711 settings with fresh
donor_policy_run44695786913:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44707306443
watcher sh_1f8314b7b001kgnEWuRdvlrkb0 backgroundwaitregistered. Metalworkflowagent
ses_e07cfcf50ffevWsWzyr1Z4x2J0 implementing pinnedexistingios27 addonpipeline,
no dispatch/commits/device. ExactD94Metal discovery validated; superclass656vs
old536 requires existingarm64e dynamicfrontend, notV52arm64guard. No phoneops.

**GPUkernel44695534584 blocked expiredrootpolicy BEFOREcompile:** log actual
LoadverifiedD94rootpolicies failed 'no valid artifacts found to download' for
43184772128. Newer43369226652 artifacts API also empty. GPUprepare steppassed;
no native/link result yet. Dispatched sameD94policy regeneration44695786913,
export_root_inputs=false usingexistingownerkeysecret (no newtrust bypass):
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44695786913
watcher sh_1f7df9d78001PN2iSAvTQh4fl6 backgroundwaitregistered. Onsuccess verify
donor/UUID/keyidentity matches installedroot, usefresh policy_run for GPUkernel.
No newrootflash/phoneops. Prior kernelrun completed before dependencyrefresh.

**Fullnative GPU kernel dispatched44695534584 at8630711:** opt-inexistingworkflow
gpu_full_link true,pinnedcoree6795bfe,amethyst_defconfig as archivequalification,
Xcode26.6/D94/rootpolicies43184772128,kernelonly. Parentactualqualificationarchive
preparationtestPASS,YAML/diffPASS. Force-load real39memberGPUarchive, append real
GPUhook/registration patch, preserveSourceStorage/BootLog;runtimebootarg absent/off.
STRICTprelink may expose existingfiveprivateimports; don't confuse with newGPU
native link failure or fabricate their implementation. No payload/hardwareenable.
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44695534584
watcher sh_1f7ddbfd9001HlbfOkXuS6wd4p backgroundwaitregistered. Metaladdon agent
continues independent exactD94 discovery/importaudit on restoredread-onlymounts.

**Metaladdon audit restored D94 prerequisite:** agent d94-metal-addon.md verifies
V52signedcodepagehashes/trustmembership/wireABI only, NOT D94compatibility.
Existingcore ios27Turnip/plugin/RustPAC rebuildroute found; no newbuilderneeded.
Agentfound OS/SystemOSmountdirs empty; parent confirmed notmountpoints thenmounted
existing exactOS.raw/SystemOS.raw read-only at/tmp/opencode/aston-audit/{os-mount,
systemos-mount}; check-d94-render-requirements.py PASS exactD94UUID/controlflow.
Resumed sameaddonagent ses_e08278fd6ffeaRVtxFXpu6WzLC for exactdonorimports/Metal
selection with restoredinputs. Fullkernelintegrationagent independentstillpending.
No phoneops/CI/newboot or V52payloadqualificationclaim.

**Continue fullGPU integration:** parent rerancheck-gpu-full-link-closure.py PASS:
39members/no strongduplicates/onlymissingstrongOSKextRegisterBoardClasses supplied
by realGPU patch;55overlaps generatedweakdefaults;267external32localized native
coverage,8weakptrauth references requireactual link. No inventoryfix justified.
Subagent ses_e08281a57ffe2m03o6LrVc8W3K owns new fullkernelintegrationhelper/workflow
(or minimaloptinexistingworkflow),no dispatch/device/children/commits. Independent
ses_e08278fd6ffeaRVtxFXpu6WzLC owns D94userspaceMetal addon compatibility/preparation;
kernelarchivealone insufficient,workingfallbackV46neededsheng-gpu-addon1, V52
addonCI41999151153/pins retained .scratch/gpu-addon-ci-v2. DoNOTassumeoldiPad17plugin
D94compatibility or weakentrust. Awaitreportsd94-gpu-kernel-integration.md and
d94-metal-addon.md. No newCI/phoneops while implementation pending.

**GPU44635510510 SUCCESS fullarchive:** downloaded/decrypted qualification at
/tmp/opencode/aston-audit/gpu44635510510/decrypted/qualification; parentverified
allrecordedobjectSHA andarchiveSHA. Full37sourcecompile/acceleratorPAC/latector/
39memberinventorypassed. WARNING import_closure.available_in_native=[] because
bootstrap comparator absent; unresolvedlist includes ordinarykernelimports,
NOT evidence hundredsnewmissingbindings. No finalkernellink/runtimequalification.
Subagent ses_e09e4be2bffeC9jIt0KgnObG4f owns targetedinventory/closure fixes in GPU
builders/tests, comparing actualarchive with knownnative44460351079 andworking
main recipe; check omittedservice vs intentionallyincludedPublish architecture,
duplicateexports andrealaddonimports. Reportgpu-full-link-closure.md expected.
No CIduplicate/phoneops; nextfullkernellink after true dependency fixes.

**GPU44634338728 compiled37units+acceleratoraudit; archivecheckfixa742f25:**
failure AFTERcompile at members equality: Apple ar exposes __.SYMDEF SORTED
metadata. Downloaded/decryptedgpu44634338728; parent verified38recordedobjecthashes,
39uniqueactualmembers via llvm-ar incllateaccelerator (no earlyobject). ExactD94
acceleratoraudit168slots/136bytes passed. FilterONLYrecognized __.SYMDEF names,
retain exactorderedmembercheck. selfcheckPASS. Newrun44635510510
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44635510510
watcher sh_1f6148610001qrHoBQUZ3TAm5r backgroundwaitregistered. Fullimportclosure
report stillpending; no runtime/prelink/GPUbootclaim. No phoneops.

**FullGPU inventory compile dispatched44634338728 at2bff65b:** qualification now
compiles37realC/C++units, strictacceleratorPAC/latector staging, archive membership
and actualimportclosure report (not runtimeapproval). Parent fixedruntimebuilder
ShengGmuVotes.h provenance to pinnedSM7635helper, not nonexistentBOARDPINpath.
NonLTOfinalflag/regression retained. selfcheck(fullclosure)/archivetests/diffPASS.
Sameencryptedinputbundle, no newupload. Run:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44634338728
watcher sh_1f60d81c4001cyq5vbJk7zGfrE backgroundwaitregistered. Actual37unitcompile
pending; no GPUenable/phoneops/newboot. Next diagnose actualcompiledlogs onfailure,
or validatearchive+undefinedsymbols on success before integratingkernel.

**CompiledGPU ABI review PASS,fullinventory next:** parent reran
check-gpu-compiled-abi.py exit0 hashes1159headers/3objects; IOExternalMethodArguments
248bytes,186clientPACslots matchnative,externalMethod168/0x540/diversityb352 matches
D94. Threeobjectunion387imports,5selfresolved278native,104remaining inclfullGPU
dependencies/weakdiagnostics. Notfullarchive/runtimecontract approval.
Parent changed archivecompile_command final-fno-lto (requiredMH_OBJECT inspection/
PAC andconstructorsection removal), added thinLTOoverride regressionPASS;diffPASS.
Subagent ses_e0a1e23c4ffegO9f8aVGUyekKw owns d94-gpu-compile.py extending complete
existinginventory C/C++ compile+archive/importclosure using samepinnedinputbundle;
no dispatch/children/device/commits. Reportexpectedgpu-full-compile.md. No redundant
CIuntil actualfullinventory wiring ready.

**GPU44624988332 REAL OBJECT COMPILE SUCCESS:** downloaded/decrypted qualification
under/tmp/opencode/aston-audit/gpu44624988332/decrypted/qualification. Actual
bootstrapexit0,1159exportedheaders; all3objectSHA match qualification.json:
UserClient07f800a0315575c3c77e0921240f478114957a235c5eaea38c672fdbda5e034b,
Servicef5d633077097330d69aee169ecbec45c9e7061f239699bafbd998cc639e49c52,
Probec6ed774cadcce3d3cf5740553665e42047d14fd69e1a5528025becaabf33cf7d.
Runtimequalifiedfalse; nofullarchive/link/GPUenable yet. Subagent
ses_e0a222156ffeaDIf8vdfn223mB reviewing compiledABI/PAC/importclosure/liveheaders
againstb13native andD94; owns tmpcheck/report gpu-compiled-abi.md, nochildren/
CI/device. Parentcheckedfullarchive include logic alreadyuses eachinputparent,
so prior missing nestedkitinclude bug is specifictoobjectqualification helper.
No redundantbuilddispatch. Next acton actualcompiledABI findings/fullarchiveinputs.

**GPU44623740019 actual compile progressed; d4bdca1 include fix:** encrypted
evidence decrypted /tmp/opencode/aston-audit/gpu44623740019/decrypted/qualification.
NativeAdrenoUserClient object compiled; NativeAdrenoService fatalShengGmu.h missing.
Pinnedheader exists Sm7635Platform/kits/Sm7635Platform/ShengGmu.h; helper only
included checkoutroots. Added exact kitinclude+fileguard, selfcheck/diffPASS.
New serialrun44624988332 https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44624988332
watcher sh_1f5d6be630013rvbev30yO1I6H backgroundwaitregistered. Liveexportheaders
nowretained in evidence; no claimservice/probe/fullarchivecompile yet,no phoneops.

**GPU44590901019 runner acquired, input fetch fixed:** actualXcode26.6 passed;
failure 'release not found' for draft inputrelease using readonlycontents token.
Published same encryptedasset as prerelease gpu-inputs-d94-20261120 (notlatest),
retainedreadonly workflowpermissions; verified isDraftfalse/isPrereleasetrue and
ciphertextSHA3c26de3a... unchanged. No plaintext/vendorfiles published. Newrun
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44623740019
watcher sh_1f5cfaa0f001pa52FGjT6Unm7s backgroundwaitregistered. No compiler result
yet/no phoneops. Prior44588158133 wascapacityonly too; allpriorruns completed.

**GPU44585376099 third capacity-only failure:** job135776768584 zero steps,
annotations confirm hostedrunner notacquired + macOSarm64 capacity, not compiler.
Prior complete before serial NEWrun44588158133, unchanged pins/inputs/Xcode26.6:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44588158133
watcher sh_1f5113e32001ktnAuaiMcITB4w backgroundwaitregistered. No code/phonechanges.

**GPU44582617216 also capacity-only failure:** job135767011187 cancelled with
zero steps; fetched annotations same hostedrunner notacquired/macOSarm64capacity.
No build/input failure demonstrated. Completed before serial newdispatch44585376099
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44585376099
Same pinnedinputs/Xcode26.6,no workflowcodechange. Watcher
sh_1f502e780001AR6wLBEbw8eEZi registeredbackgroundwait. No phoneops.

**GPU44579862378 runner-capacity failure, no steps executed:** job135757270379
cancelled before acquisition. Check annotations explicitly: 'The job was not
acquired by Runner of type hosted even after multiple attempts'; macOS arm64
capacity notices. Not compiler/input error; no code change justified. Priorrun
completed before NEWdispatch44582617216 sameinputs/macOS26/Xcode26.6:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44582617216
watcher sh_1f4f49b47001lLrRN4pgaA8UCu backgroundwaitregistered. No phoneops.

**Real GPU object compile DISPATCHED44579862378:** validated local completeinput
bundle c5d99b3538909136eeb4f23cbd26487c868281dbb7d7a6ce6ed1a271b6064824,
actual stage exit0 all6cleanindependentgitpins. Encrypted stdinpass only; draft
release gpu-inputs-d94-20261120 asset ciphertext SHA
3c26de3aa45b27ae52e45b40fdbde01ca5135ff0a74d511cfa44a77aa0f0c11a matches
GitHub assetdigest768637892. No plaintext uploaded. ScopedGPUfiles committed
ac96fe8;workflowregistrationeda366e (push registers,job manualonly). Firstdispatch
404before registration,no run; successfulsinglemanualrun:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/44579862378
watcher sh_1f4e63b83001FBim4kdrgFgTFJ registeredbackgroundwait. ActualmacOScompile
pending, no GPUenable/phoneops. gitdiffcheck warned patch-file context whitespace
(embedded diff context tabs/blanklines), hookapplytestPASS; don'tclaimcleancheck.

**Nov20 concrete CI staging in progress:** subagent ses_e0b1c58b9ffeCXT1qMU3daii8k
owns /tmp/opencode/aston-audit/gpu-ci-inputs* (and optional staging helper), building
self-contained pinned clean git closure + sixgeneratedheaders+firmware+exactdonor
tar and running real d94-gpu-compile.py stage guards. No upload/dispatch bychild.
Parent added optional SAMErepo release-tag encrypted asset channel to new
d94-gpu-compile.yml because localinputs have no existing actionsartifact. Exclusive
run/artifact vs release, ciphertextSHA beforedecrypt retained; localselfcheck/YAML
exclusivechannel/hashordering assertions PASS. Nothing uploaded/dispatched yet.
Next await actualstagePASS, encrypt usingstdin secret, authorized encryptedCI
input delivery then commit scopedGPUfiles/dispatch actualmacOS objectcompile.
No phoneops/qualificationcontracts fabricated.

**RealGPU late hook implemented/checkpassed:** new native-d94-release-gpu.patch,
prepare-d94-gpu-hook.py,test-d94-gpu-hook.py invoke realprobe after successful
IOKitBSDInit,macroXNU_NATIVE_AMETHYST_GPU + explicitnonzero sheng-gpu-power gates.
Uses existing real locksafeOSKextRegisterBoardClasses, failure semantics intact.
Parenttest exit0 (expectednegativeOSKextapplyfailure prints error), archive host
test exit0. Notwired/enabled. Exactcompiledexportheaders absent localartifacts;
sourceIOUserClient signatures+native nm agree but ABIqualification stillpending.
Subagent ses_e0d607f1effe4j5PUfpEo5Xi84 owns NEWisolatedmacOScompile workflow/helper,
no dispatch/commits/device, no fakecontracts. Goal realclient/probeobject compile
against liveXNUheaders/recipe before bootenable. Reportd94-gpu-compile-integration.md.
No phoneops/CI dispatch byparent; GPUkernelarchive notyetbuilt.

**GPU votes prerequisite now generated/rechecked:** prepare-amethyst-gpu-votes.py
reuses hashpinnedexistinggenerators with exactamethystliveDT4c159b30; generated
ShengGmuSource.h/VotesSource/CommandDbSource+manifestunder/tmp/opencode/aston-audit/
amethyst-gpu-votes. Parentcheck-amethyst-votes.py exit0 deterministicregeneration,
foreignDT/nooverwrite/hash/fallbacksemantic/SKUtests. Correctamethystbin0first;
runtimeSKU NOTassumed. Reportd94-amethyst-votes.md exacthashes/provenance.
Nextsubagent ses_e0d6519c0ffemD07vBvcmGNC9t owns NEWlateGPUhook helper/patch/tests
and D94importreview; no core/displayprobe edits/device/CI. ParentXNUsource confirms
arm64/arm_vm_init.c1836-1854 gPhysBase=argsphysBase,real_phys_size=argsmemSize,
gPhysSize rounds DOWN supplied window;supports reservedGMUpool outsideallocated
loaderwindow not being reclaimed. Still validate actualcompiledpatchedpath before
marking contract complete. No runtimeGPUclaim/newboot.

**GPU prerequisite implemented, parent checks PASS:** new board firmwaregenerator
prepare-amethyst-gpu-firmware.py creates pinned actualamethyst GMU/SQE/ZAP headers;
archivebuilder verifies headerbytes == firmwareblobs (not merely independenthashes).
Parent reran test-amethyst-adreno-archive.py and check-amethyst-gpu-inputs.py exit0:
roundtrip/corrupt/foreign/nooverwritechecks; shippedb13FD reservedGMUpool records
PrePi+3FVMAIN/noheapoverlap; all3arrays match retainedfallback. Report
/tmp/opencode/aston-audit/d94-real-gpu-next.md. No archivecompile/runtimeclaim.
Votes derivation subagent ses_e0d68d8a9ffe1qirbs6QwrJE82 now owns newamethystvotes
generator/test or tmpoutputs; don'tduplicate. Parent memoryhandoff source check:
DarwinBoot HEAD exactly3c75734014cdab0acd9541e88057b7606ee7ae08; AllocateWindow
line521 onlyConventionalMemory,RunEnd mergesonlyConventional; ArgsMemorySize and
MemorySizeActual=allocatedWindowSize lines747-748. This supports reservedpool
exclusion at loader allocation, still need actualXNU allocator consumption proof.
No phone/CI/newboot. GPUintegration stillblockedvotes/userclient/latehook/realbuild.

**CRITICAL correction / actual render prerequisite:** validated raw slot boundaries
against headers for BOTH b12 andb13: all52 GPU/Metal name hits are in stale slots
483+ beyond current421/422 writes. ZERO currentGPUrows. RETRACT earlier claim
SpringBoard/backboardd reached Metal on these boots. No GPU runtime qualification.
Exact-D94 static audit /tmp/opencode/aston-audit/b13-real-render-repair.md and
check-d94-render-requirements.py PASS establishes AppleDisplay::new_server creates
AccelServer->CAMetalContextCreate->MTLCreateSystemDefaultDevice; inspected null
device path returns without software renderer. CPU/GOP copies finished surfaces,
does NOT implement compositor rendering. Focus actual amethyst GPU/Metal bringup,
not more timer/crashfilterboots. Upstream IOMFBNumLayers MaxNumLayers1 avoids
separate scaler dependency but does not supply Metal device; not standalonefix.
GPU implementation subagent ses_e0db09ff3ffeJudLZ8szcrVxd7 interrupted then resumed
on usercontinue; ownsGPUbuilder/tests/scopedGPUfiles only, no device/CI/children.
Do not duplicate its work. Report expected d94-real-gpu-next.md. No newboot.

**Nov21 TOUCH/BUTTONS PORT PLAN (in progress, uncommitted):** recipe = sheng build-gop-legacy-display-27.py input_objects:
NativeHidInput (source API, IOHIDFamily-2238.120.5 public headers, -DNATIVE_HID_SOURCE_API=1; local copy
.scratch/ShengHidInput d1ffc9d incl. In Range fix) bound to the ORIGINAL IOHIDDevice. D94 fixture DONE:
scripts/gen-hid27-abi-d94.py -> scripts/hid-device-24A446-d94ap.json (found by content: unique vtable 0xfffffff0082b26b0
with the iPad's 207 diversities + pure slot 180; gMetaClass 0xfffffff00b954580, C2 0xfffffff00a0b61f4, D2 0xfffffff00a0b622c,
size 0xd0; iPad copy kept as hid-device-24A446.ipad.json). IOHIDFamily is retained in D94 roots (IOHIDEventDriver class ships
inside it on iPhone). Remaining: vendor NativeHidInput.cpp, port sheng HID binding generator, compile iPadOS 17 touch/buttons
(UniXNU-amethyst/scripts/log compile-touch.py SOURCES + GeniSpi.c + AmethystButtonsHid/AmethystButtons/SpmiRead), start
amethyst_touch_snapshot()+amethyst_buttons_hid_start() from the late probe; args amethyst-touch-snapshot=2
amethyst-touch-events=2 sheng-touch-hid=1 amethyst-buttons=1 (+loopback/goodix flags per pack-log.py). NOTE iPadOS 17 smoothness
came from the GPU hand-off presenter (sheng-fb-gpu=1, 120 Hz): CPU copy is ~170 ms/frame; the ios27 LegacyDisplayProbe
(ishtar copy) refuses sheng-fb-gpu - porting the amethyst presenter is the later fix for lag.

**Nov21 b17 HW (governor) + b17n BOOTED 19:25:16:** CI 44872841968 OK; prepare-b17.py (trust fn moved: old read at its
own address) -> kernel e9b113c2. b17 log untagged-54c1fc31d: [cpu gov] works (d0 590MHz 10% busy, d1 812 3%, d2 582 1%)
- CPUs mostly IDLE, so slowness is waiting, not compute. Swaps on a strict ~5 s beat with 40-80 s gaps (27 swaps in
544 s), vsync sent=0 arms=0. GPU: backboardd user-client open res e00002e2 macf 1 (sandbox deny, V47 issue: sheng adds
"*" wildcard in IOUserClient native_sandbox_grant), MetalPluginName read 28x by SpringBoard, no addon. b17n = b17 args
minus sheng-gpu-power/irq (GPU off) to test whether GPU publication causes the 5 s waits (img 1809b120).

**Nov21 b16w HW + b17 BUILDING:** b16w: text overlay gone (it was -textconsole), user: everything very slow. NOTE:
log-display=1 is a BootLog row group (display/swap rows in the log), NOT a screen overlay - removing it emptied b16w's
slots (untagged-428a1f8f has no display rows). Keep log-display=1 (+log-power=1 for [cpu gov]/[clocks]). f297d4d: EPSS
cpufreq governor (iPadOS 17 CpuFreqProbe.cpp + sheng zzye-sheng-cpu-idle.patch as native-d94-cpu-idle.patch), started
from late probe; CI 44872841968. Touch/buttons NOT ported to iOS 27 yet (HID input + Goodix/GENI stack).

**Nov21 b16v HW: LOCK SCREEN REACHED ("press home to open"), b16w BOOTED 18:46:24:** b16v log untagged-40c1c959 (396s):
refresh=1 (120 Hz published) but vsync sent=0 arms=0 (no vsync client ever registered; check kLegacyVsyncClients offset
for the iPhone ABI), 15 swaps, CPU copy. User: text log overlays UI, laggy, home-button lock screen. b16w = b16v +
ADT /buttons (compatible/device_type "buttons", button-names hold/voldown/volup, home-button-type=2, from sheng
board.toml) and args minus log-display=1 -textconsole -enable_kprintf_spam (img SHA fc39fc89).

**Nov21 b16 HW: SCREEN UPDATES (AKS stand-in unblocked presentation), b16v BOOTED 18:37:42:** b16 log untagged-815793e1
(1138s): 57 swaps validated/retired, copy ~100ms each, but swaps arrive every ~5 s (gaps 40-90 s); vsync sent=0 arms=0
refresh=0 (sheng-vsync=60 publishes no IOMFBDisplayRefresh: getRefreshAttributes/set_refresh only for hz>60). User: text
console still scrolling slowly, no boot screen. b16v = b16 + sheng-vsync=120 sheng-vsync-fixed=1 (sheng values), SHA 39228285.

**Nov21 b15g/b15/b15h HW + b16 BOOTED 18:15:04:** b15g display ProbeForbidden: amethyst LegacyDisplayProbe refuses
sheng-fb-gpu/sheng-dpu-scanout (SM8550 args) - never pass them. b15 (GPU off) and b15h (GPU 19, no fb-gpu): user saw
the GPU RGB test bar (GPU works), but still ONE swap (~234/242s) then none for ~17 min; no "rejecting class open".
Cause candidate: AppleKeyStoreState component compiled but never registered (LTO-dropped; no native_aks symbols).
e61b357: port sheng KeyStoreProbe.cpp into package/, link it + NativeKeyStoreEvents.late.o into the late display
archive, call native_keystore_probe() first in native_legacy_display_late_probe. CI 44868406884 SUCCESS (missing 0,
stubbed 0, 220 adapters, native_keystore_probe linked; __DATA_CONST fit). prepare-b16.py PASS -> kernel 6aec18c0.
b16 = b15h args + offline-key-events=1 sheng-aks=1 (img SHA 021ae712).

**Nov21 b15/b15g PACKED, b15g BOOTED 17:07:46:** commits 30c3b20+f77fc90 (ios27-d94-build-audit): GPU relink
accepts ios27_data_defconfig; native-d94-dataprotect.patch = sheng owner-dataprotect get/setattr xattr emulation
+ class-carrying VNOP_CREATE. CI 44862843405 (gpu_full_link, ios27_data, policy 44695786913) SUCCESS: missing 0
stubbed 0, 220 adapters, candidate e9c98674. /tmp/opencode/aston-audit/prepare-b15.py (from b14: ADRP/low12 pairs
checked to same symbol, renderer panic-text MOVN re-encoded (+0x310), caves moved to __TEXT_EXEC zero tail
0x...b316000/b316f00) PASS -> kernel-d94-b15.macho d6049890. Payload = b14d (maxvnodes ADT) + new kernel.
b15 args = b14c + owner-dataprotect=1 (SHA 66228795); b15g = b15 + sheng-gpu-power=19 sheng-gpu-irq=0
sheng-fb-gpu=1 (SHA 91d8aa00). No Metal addon in payload (sheng-gpu-addon off).

**Nov21 b14c/b14d HW (b14 kernel, boot args/ADT only):** b14c = b14b args + sheng speed args
(kt_fixed_zones=120 -no_slto_panic use_contiguous_hint=0 sheng-smp=1 armcpu-spi-ipi=1 armcpu-self-ipi=1
sheng-lite=1 sheng-disable-watchdogd=1): SMP did NOT hang; user saw endless "vnode jetsam" (CONFIG_VNODES=1024
clip; sheng board.toml fix). b14d = b14c + ADT /defaults kern.maxvnodes=78848 (u32, read by unix_startup.c
before the CONFIG_VNODES clip; edited with /tmp/opencode/aston-audit/adt_set.py), img SHA b7358763..., booted
16:14:54. Log untagged-421265ac boot_id 1108501932, writer 806s/0 errors: FIRST SWAP at kernel 233.9s (b14b
644s, 2.75x faster), offer/done 159ms, frames=1, then NO further swaps through ~864s. msgbuf tail: no
vnode/jetsam/protected/lowdisk/panic; bluetoothd/fairplayd/gamed respawn every ~5s (pids ~790), liveactivitiesd
SIGTRAP (ui-exit status 0x5), cameracaptured[62] running despite builtin sheng-disable-jobs.

**Nov21 b14b HW (b14 kernel, args minus sheng-diag/sheng-diag-export; RAM boot 15:44:25):** boot_id 4657858355
(untagged-115a14733), writer 468s/235 rounds/0 errors. Writer clock starts ~kernel 242s. Display stage=10 at
writer 2s, PM client calls 5 (last pid 84 @w198s), FIRST AND ONLY SWAP at kernel 644.026s (w402s): offer/done
98ms, 13234560 bytes, frames=1, retires 1, blank 1; nothing after through kernel ~710s. msgbuf only last 16KiB
(665..710s): sandbox/corpse noise + apfs_lowdisk_check_volume disk1s1 lowdisk 0x20000 free 473069. User saw
on-screen disk spam about "protected" = likely apfs_is_valid_class "rejecting class open (class 2) because
we're not content protected" (1 hit in b14 raw window, none in b14b's 16KiB tail). sheng fix:
owner-dataprotect=1 (PE_builtin_boot_args) + kpi_vfs VNOP_GET/SETATTR xattr emulation in
zzzz-sheng-ipados27-bringup.patch; NOT in our kernel (no strings). Covers get/setattr, maybe not class opens.

**Nov21 b14 HW RESULT (RAM boot 15:30:54 NZDT, recovery pull 15:40):** fresh boot_id 2486832592
(untagged-943a0dd0), uptime 380s, 191 rounds, 0 write errors, GPT 1/1, state 2. msg_bytes=0 (b13 had 352).
Diag export NEW (blob 2675f0ed.. != b13 2c4c4310..; read 9 blocks rel 33480704 only) -> /tmp/opencode/
aston-audit/recovery-d94-b14/diag: kernel.rings.txt + diag-skipped.txt, same as b13 except the
boot_diag_shot_tick slide ref. display prepare calls=0, vsync start=1 sent=0, swaps/kicks 0;
CrashReporter + frames.trace ENOENT. No panic. Shims bound but NO observable change vs b13; the
log carries no AppleARMPlatform/Image4 evidence either way. Raw log.bin strings (t>380s) are stale
older-run slots, not b14. User: "boot takes forever". Phone returned to fastboot.

**Nov21 b14 SHIM IMAGE PACKED, NOT BOOTED (device absent):** ios27_data run 44830852453 (board
8778291, root policies 44695786913) SUCCESS: missing{}, stubbed 0 (b13 had five), 218 adapters;
the five iOS 27-only imports bind reviewed to native-d94-ios27-shims.patch (Image4 helpers return
0xffff/3, IOService RPC 0xbf66f4287c60ef85 returns kIOReturnUnsupported with *out=NULL). Candidate
d0d5d599...; segment layout identical to b13. /tmp/opencode/aston-audit/prepare-b14.py (from b13,
trust/trap checks generalised to same-symbol branch re-encodings) PASS; renderer data addresses
unchanged, _panic moved +0xc8 (hook re-encoded). Final kernel b6ba23699b48...; payload-b14.fat =
b13 payload with kernel only. staging/amethyst-d94-b14/Mu-amethyst-d94-b14.img SHA f652a8e4f3f7...
Shipped FAT verified: kernel == prepare output; adt/vroot/trustcache/seed/unixnu.toml byte-equal to
b13 (persist0, autoreset0, diag-export300). Next: save recovery log, RAM boot, wait >=300s, pull.

**b13 recovery417c9b02 pulled/export extracted:**843s422rounds0errors,GPT1/1.
headerdiag_bytes34105,export_below65536,diag_first0. Read ONLY9blocks fromunixnu
relativeblock33480704 (33554432-8192-65536), trimmed34105, unpack_export PASS:
/tmp/opencode/aston-audit/recovery-d94-b13/diag/{files,manifest.json},2files33992bytes.
Only kernel.rings.txt anddiag-skipped.txt; no selected crash reports. Rings at300s
show timer60start1 but swaps0/kicks0; rootCrashReporterENOENT andframes.traceENOENT.
latestslot onlylatency-issues7 line: diagnostic mode filters out display rows.
Retainedmsgbuf late~769..883s stillcontainerwarnings/liveactivitiesdSIGTRAP,
oneAPFSflush501us (not absurd). Export at300s tooearly for later failure evidence;
absence of reports is NOT proofno crash. Raw32MiBlog contains26GPUrows resembling
olderruns: MUST validate boot_id per-slot before treating these as b13 current
telemetry (oldslots survive). Earlier rawb12subagent conclusions likewise need
bootidqualification. No newboot/devicepersistentwrites; recovery blockread only.

**b13 RAMboot issued explicit userboot:** saved existing recovery4fcea552 under
/tmp/opencode/aston-audit/recovery-pre-b13 (840s421rounds0errors), verified Windows
copy SHA7d4d3f74...,adb serial42fe0af2 rebootbootloader then fastboot.exe boot.
Sending49168KB OKAY1.186s,BootingOKAY0.303s,total1.506s. No flash. Wait >=300s
logtime for explicit diagnostic export, then recoverypull/extract archive. Handoff
only confirmed, no display/diagnostic export success claim yet.

**44460351079 SUCCESS / b13 capture image PACKED NOTBOOTED:** decrypted candidate
b6141467d25cb84f731046b96c26e7c9235b7832c8b4dc2920e398334746304d exact,
missing{},five stubs218adapters. prepare-b13.py repeat exit0 independently rematches
full trustCFG/PAC hooks/data/new executable zero caves, verifies actual compiled
nine crash names/index<9/counterzeroing and all218emittedadapterdestinations.
Report b13-readiness.md/b13-qualification.json in/tmp/opencode/aston-audit.
Finalkernel2e107ad39d24bf7815220f168420c6b1f6634855463344d7794c1f3d79af817d.
Copied b12FAT, newkernel only+args sheng-diag1/sheng-diag-export300 (5min logtime),
autoreset0,persist0,timer60 retained. This uses existing approved diagnostic
export region, NOT persistent Data. Broader diagnostics add overhead; crash
reports alone don't diagnose noncrashing hangs. Packedimage
staging/amethyst-d94-b13/Mu-amethyst-d94-b13.img size50348032 SHA
7d4d3f7410745e4d36aed05b5f2377e0737000e0fd99f0edede50bf6d18ad72d.
Extracted shippedkernel/ownershipseed byteexact,571byteargs capture300/autoreset0/
persist0 verified. Renderer retained diagnostic text B11PANIC intentionally.
No phoneops this turn. Next explicit RAMboot + at least300s logtime then recovery
pull, require diagbytes>0/extract actual relevant reports (absence not success).

**User requested two subagents + continuous work; Nov19 audit outcomes:**
upstream-b12-registration.md and d94-registration-dispatch.md/check show exact
selector72 dispatch40bytes->slot211->type5 list1ab8->noop334->ownedkick307 intact
in SHIPPED b12 inclPAC/adapters. Upstreamc031d882 rendered without subscriptions:
zero ticks NOT proven blank cause. Rawlog GPU sections (omitted extractedslots)
show both backboardd/SpringBoard reached Metal discovery, no captured startup
failure for either; don't call liveactivitiesd647 SIGTRAP SpringBoard.
Second migration audit b12-migration-policy.md/check proves ADTephemeral1 plus TWO
actual D94 datamigrator early exits before marker handling. Marker alreadyabsent;
Aston firstbootpersistent fixinapplicable. Do NOT lie about storage/runfake migration.
Concrete capture bug fixed in board BootLatencyFiles.h/BootDiagExport.c: add exact
SpringBoard/backboardd/containermanagerd/com.apple.datamigrator crash prefixes and
derive counters from same9entry list, preserve2/process4MiB/file8MiBtotal bounds.
New scripts/test-boot-crash-filter.py realC+UBSanPASS;dispatch/migrationchecksPASS.
Fullkernel compile pending. Export must be explicitly scheduled sheng-diag1+
sheng-diag-export seconds whennoautoreset; b12diag_bytes0 cannot recover RAMlogs.
Crashreports alone do NOT diagnose noncrashing wait; live unifiedlog/wait evidence
still needed. No phoneops/newimage. No broadupstreampublication requested/performed.

**Continue registration audit:** reran scripts/test-d94-display.py with exact core
and D94 kernelcache env: PASS379PACtargets/discriminators/adapters/RELEASEguards.
NativeLegacyDisplay kickSwapQueue lines555-556 calls armVsync BEFORE kicking/
rekick/transfer gates, so downstream swap rejection cannot bypass arm within this
override. Selector72->this override remains unproven. Scanned original IOMFB code
a180000..a1b0000; only direct this+1ab8 loads found at a18c228/a1937ac, no direct
stores/adds there. Wider add#ab8 hits a28691c/a286974 are ADRP string references,
NOT client-list registration (disassembly saved registration-sites.bin/.o).
Do not label them register/unregister or patch. No source edits/CI/deviceops.
Next locate async-notification dispatch table/registration writer from actual
IOMobileFramebuffer userclient rather than guessing selector mapping.

**b12 follow-up upstream+exact D94 client-list audit:** fetched GopFramebuffer
origin/main b0d812d, read NativeLegacyDisplay diff since pin e47c1b8. New changes
macOS support/argument rename/format counters, no iOS timer/client gating repair.
Exact donor SHAfa07057b: native_legacy_vsync_notify a193754+58 loads x19 from
[x19,#1ab8], then next pointer at+0; original a18c228 also loads this+1ab8.
Matches board ABI vsync_clients6840, so offset mismatch hypothesis DISPROVEN.
Runnable /tmp/opencode/aston-audit/check-d94-vsync-list.py PASS exit0, asserts
exact instructions/hash. Full original IOMFB region disassembled in iomfb-d94.asm.
Registration selector72 and actual active client presence NOT established; zero
arms alone not proof list never populated (kick path may be missing). No driver
edits/unconditional ticks/fake clients/new image/phoneops. Next trace exact donor
registration dispatch and actual kick override before deciding instrumentation/fix.

**b12 recovery hardware4fcea552: timer constructed but no clients/ticks:**
Read-only pull serial42fe0af2 to/tmp/opencode/aston-audit/recovery-d94-b12/
untagged-4fcea552. uptime840s,421rounds,0loggingerrors,GPT1/1. Final display
stage10fail0/init1/start1/register1/surfacefound1; clientcalls5status0;oneaccepted
blank frame,transfer1status0,copy0,retired0. vsynchz60/start1 BUT arms0/sent0/
idle0/late0; bootarg correction effective,not sufficient. Current source
NativeLegacyDisplay.cpp:401-435 gates armVsync on nonnull client-list head;
selector72 registration expected to kick queue. Need exact D94 client registration
ABI/list offset and userspace render progress audit;don't force unconditional
ticks/fake clients from this evidence. Retained msgbuf tail has no184467/negative
APFSflush warning, not full-boot monotonic proof;containermanagerd75 still stuck,
missing group/system container messages persist. No UFSfailure established.
No reboot/phonepersistentwrite this turn. Ownership repair did not by itself
resolve container state; do not infer guessed next metadata fix.

**Nov19 b12 RAMboot issued on explicit user request:** serial42fe0af2 already
fastboot, no adb/recovery available for preboot log pull. Windows image copy
SHA cfd0b557bc0ba85814328dc6eebb4eec3f74603b698aa59a45ee8f56af0c425d verified.
fastboot.exe -s42fe0af2 boot: Sending49168KB OKAY1.182s,BootingOKAY0.308s,
total1.505s. No flashing/persistence changes. This proves handoff only; await
user display report/recovery logs for synthetic-vsync progress/frame copies and
clock/ownership runtime effects. Do not claim visible UI from fastboot success.

**Nov19 b12 COMBINED IMAGE BUILT, NOT BOOTED:** host runnable
/tmp/opencode/aston-audit/prepare-b12.py reconstructs reviewed b11 postlink patches
with exact old/new candidate hashes, identical segment layouts, rematched symbols,
same renderer data objects/addresses and zero caves. arm_vm_prot_init0x800 window
identical except ten BL immediates shifted+32; panic symbol shifts+32,hook reencoded.
Final kernel SHA826fba4ff27ecd83d5e4955b7d85ab5876bb2e4895d479d114cdaabd947e3da7.
Packed b12 from b11 FAT COPY, newkernel+verified ownershipseed47e4aa29...,
sheng-vsync=60 (existing SYNTHETIC timer),log-display1,persist0. pack-ios27.py
samei180container/sameGMUpool firmware. Outputstaging/amethyst-d94-b12/Mu-amethyst-d94-b12.img
size50348032 SHA cfd0b557bc0ba85814328dc6eebb4eec3f74603b698aa59a45ee8f56af0c425d.
Extracted FAT /tmp/opencode/aston-audit/shipped-b12.fat: shipped kernel and seed
byteexact, timer60/persist0/log-display1 PASS,3other payload files unchanged.
Whole FAT raw comparison differs due packer formatting; file-level checks PASS.
No phoneops; next explicit RAMboot after Windows copy/hash + recovery log save.
Runtime goals vsync60/start/notify/client progress,nonblankcopies,clock monotonic;
no UI/GPU success claimed from packaging.

**Nov19 packaging recovery progressed, NOT packed:** restored/decrypted successful
kernel43632634633; exact82fd4213 candidate +missing{}+five stubs verified. Restored
old build43624330838 and extracted shipped b11 kernel from surviving FAT; exact
a79412e7 hash verified. Recovered COMPLETE prior postlink diff2437 bytes, all
accounted for in five ranges, saved /tmp/opencode/aston-audit/b11-recovered-patches.json.
Symbolized old emitted sites with OLD report segment_layout: panic_trap_to_debugger
+4 atab514e8, arm_vm_prot_init+6ac at ac8fa68, panic+4 atb270c50; old trust cave
b2e2000 length16 and rendererb2e3000 length2864. These are historical identities,
NOT approved new addresses. Need reconstruct/rematch new CFG and renderer absolute
references/cave qualification; don't copy old machine code blindly. No phoneops.

**Nov19 resume: ownership43683383319 SUCCESS (local watcher SIGTERM only):**
Confirmed remote completed/success at302d3e3, no duplicate dispatch. Downloaded
and decrypted artifact /tmp/opencode/aston-audit/seed43683383319/decrypted.
Packed seed SHA47e4aa29b612fa53a55f9b1e07b2816b367ce6b198ac1da6c9591cdde8acbed1
matches audit, statusverified,242applied,unknown[],original_hashes_unchanged=true,
two recorded host journal additions. UUID9EE3A4D1-60A0-49AD-9D49-B960370FF230,
2147483648bytes,Data role. Independently unpacked sparse raw, ran local
apfs-dump-quick (exit0); raw inode dump apfs.txt proves mobile501:501 mode40711,
mobile/Library501:501 mode40711,root0:0 mode40755,tmp0:0 mode41777.
IMPORTANT /tmp/opencode prior scratch tools/reports/builds no longer exist after
environment gap; only newly restored artifacts present. Re-downloaded encrypted
kernel43632634633 to/tmp/opencode/aston-audit/build43632634633 (not decrypted yet).
Need restore/rematch trust-placement and panic hooks using surviving repo and
.scratch artifacts; NEVER assume old temp scripts or cave addresses survived.
Next combined image clock-fixed kernel+verified newseed+sheng-vsync=60 remains
UNPACKAGED, no phoneops. Seed APFS host verification now succeeded, not hardware.

**43682500560 proves only new host journals;302d3e3 scoped fix:** decrypted report
/tmp/opencode/aston-audit/seed43682500560/report.json. Exact differences are ONLY
added .fseventsd/0000000008edc453 and ...c454,regular uid0/gid99/mode0600.
No existing inode/content differences; persisted UUID/size/Data role identical;
original hashes unchanged. Added narrow post-detach exception for NEW single-level
16lowerhex journals with these exact owner/mode/type checks, record full hashes in
host_journal_additions. Existing journals/other files/removals still exact; negative
scope/type/owner/mode tests PASS. New run43683383319:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43683383319
First dispatch CLI typo rejected locally (no run); confirmed prior latest completed
before successful dispatch. No duplicate, no phoneops. Seed output still pending.

**43677604517 failed persisted remount check; dc5f52a diagnostics retry:**
decrypted failure report /tmp/opencode/aston-audit/seed43677604517/report.json:
242 changes applied to host COPY,unknown[], immediate inventory/content and
UUID/size/role checks passed; post-detach/read-only remount verification failed.
Original hashes unchanged=true; UUID9EE3...size2147483648roles[Data]. No output
published. Do NOT assume journal churn or accept failed persistence check.
Added exact expected/actual per-path inventory diff plus persisted identity to
encrypted failure report and error; added added/removed/changed diff test PASS.
New run43682500560 https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43682500560
All checks remain strict; no phoneops. Kernel already verified; seed still blocked.

**Resume state check + final prelinked clock check:** ownership43677604517
confirmed in_progress atddc26ce; no duplicate dispatch. Added runnable host check
/tmp/opencode/aston-audit/check-d94-clock-final.py and ran exit0: exact candidate
82fd4213..., exact five expected private stub identities,218adapters, reviewed
clock binding. Uses report segment_layout to translate native->emitted address;
checks preserved arithmetic and relocated ADRP/ADD divisor target, not raw native
address (native VAs are not final prelink VAs). Full-frequency multiply/divide
tail verified in actual final kernel. Ownership watcher still pending notification.
No image/phone writes; remaining seed success and fresh-layout packaging gates.

**Continue:43638725790 exact refused paths reviewed;ddc26ce repair retry:**
downloaded/decrypted failure report /tmp/opencode/aston-audit/seed43638725790/report.json;
original_hashes_unchanged=true. Unknowns only host .fseventsd dir/eight hex journals/
fseventsd-uuid and donor hardlinks db/DarwinDirectory/local/recordStore.data,
msgs/bounds. Allow metadata reads of readonly donor hardlinks, still reject ALL
mutable seed hardlinks. Explicit preserve-only rule for host journal dir and
single-level 16lowerhex/uuid regular files; no invented ownership, no content
changes, unknown names/symlinks still refused. Host tests cover source aliases,
seed rejection and scoped journal/unknown refusal PASS. New run43677604517:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43677604517
No phoneops; repaired seed still awaiting actual APFS verification.

**Ownership43635995382 mounts PASS, planner refuses unknown paths:** reached
line238 unknown-path guard before metadata edits; both disk4/disk2 detached.
No path list in prior error, output JSON not uploaded, so exact unknown paths
cannot be inferred from this run. c8125a0 prints unknown path/reason list on
refusal and encrypts failure JSON artifact; no planner fallback/guard changes.
Host planner and workflow YAML/diff PASS. Workflow-path push auto-started single
run43638725790 (no manual duplicate):
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43638725790
Review exact refused paths against source/generator before adding any rules.
No metadata repair success/no phoneops. Verified clock kernel remains unchanged.

**Ownership43634807816 still rejected source;5921999 fixes actual mount query:**
runner diskutil omits BOTH read-only key spellings (VolumeReadOnly null too).
Image matches1/APFS/ownersON confirmed. Replaced plist read-only dependency with
os.statvfs(mount).f_flag & os.ST_RDONLY (actual mounted filesystem state), never
treat missing property as false. Tests cover absent plist state, actual read-only
and writable modes and mismatch refusal; planner/diff PASS. Source failure was
before any edits, detached disk2. New run43635995382:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43635995382
Ownership artifact still pending; no phoneops, no relaxation of image/owners guards.

**Ownership43633844648 diagnosed/fixed7682330:** actual source mount has
image_matches1/APFS/GlobalPermissionsEnabled=true; rejection was wrong plist key
ReadOnlyVolume (absent). Existing prepare-ios27-root-images.py already uses actual
VolumeReadOnly/MediaReadOnly names. Corrected key, no relaxed guard. Added host
positive tests for read-only and writable identities plus opposite-mode rejection;
planner/identity tests and diffcheck PASS. New ownership run43634807816:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43634807816
Prior failure before metadata edits, source detached; no repaired seed/hardware
claim. Kernel43632634633 qualified above remains ready for packaging gates.

**43632634633 SUCCESS, reviewed clock binding restored:** decrypted artifact at
/tmp/opencode/aston-audit/build43632634633/decrypted; candidate SHA256
82fd42132b34417458f1d62c28801461927f0be00912f611d6193675152d6bb4 matches
kernel.json. Asserted missing{}, FIVE stubs, 218 original adapters. Actual map
0xfffffff00ad4d8dc -> _absolutetime_to_microtime native0xfffffff007367518,
how=reviewed. Fresh native disassembly confirms remainder*1000000/frequency
(mul at...7540, udiv at...7544) and correct seconds/usecs stores. Build source
045753e9b1dbd74112d3f0d7775947357f502be8. No hardware claim; not yet packed.
Remaining next-image gates: fresh-layout trust-placement/panic rematch and shipped
verification; ownership diagnostic run43633844648 still awaiting notification;
enable sheng-vsync=60 in packed arguments. No phoneops.

**Ownership43631575579 failed source identity guard before edits:** raw attach
now works; mount_identity rejected source OS at line143, detached disk2. Existing
error did not distinguish image association/owners/read-only/filesystem check;
do not infer which failed or relax guard. Commit055321e adds selected diskutil
fields and hdiutil image/device associations to rejection error; host negative
test verifies still rejects and reports failed inputs. Planner/diff PASS.
New diagnostic ownership run43633844648:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43633844648
No repaired artifact yet; all original identity/ownership guards intact. Kernel
43632634633 independent watcher pending; no phoneops.

**43632413807 failed pre-compile, corrected045753e:** prepare-d94-build.py:27
fixture SHA assertion still pinned old377e220a after reviewed microtime addition;
counts also needed173/22 before quarantine,172/18 after. Updated SHA toe0e6f727
and counts, kept all guards. test-d94-selection.py now executes actual preparation
fixture guards/quarantine read-only and requires microtime binding. Selection
PASS, conversion8742/UBSan PASS,diffcheckPASS. New serial ARM/Xcode26.6 run
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43632634633
must verify returns to5stubs/218adapters then fresh-layout packaging. No phoneops.

**Clock build43630565056 SUCCESS, not ship-ready:** downloaded/decrypted artifact;
candidate SHA256 92a5fd475322fe0cd470a02faf642d5fa33a14bdf47841abe2e7a717b54c418d
matches report; missing {}, 218 original adapters, but SIX stubs (prior five plus
AppleARMPlatform 0xfffffff00ad4d8dc). Exact prior maps identify this as
_absolutetime_to_microtime: arithmetic repair intentionally defeated reference
matching. Fresh native symbol 0xfffffff007367518 disassembly confirms full-frequency
remainder*1000000/frequency and unchanged x0 ticks/x1 uint64 seconds/x2 uint32 usecs
ABI. Donor 44-byte function SHA fad03cfbcf5be1084ede621235dc62c8cfe930169c80e2e563880716d993e4dc
verified by segment extraction/disassembly. Added explicit reviewed donor fixture
identity by native symbol, no fixed native address. Extended existing runnable
clock test to require this binding; 8742 cases/UBSan PASS. Needs NEW prelink build;
do NOT package six-stub candidate or claim runtime clock repair. Ownership retry
43631575579 watcher remains independent; no completion received yet.

**Upstream-first verifiedrepairbatch599924b building:** displayhistoryreport
upstream-display-repair.md confirms pinnedGOPe47c1b8reads`sheng-vsync`,upstream
fa2fa49renamedto`vsync`withoutalias. Actualb11packedargsneither=>existingtimerOFF,
notnewupstreamtimercodefix. Nextpack MUSTsheng-vsync=60 forCURRENTpin,synthetic
worklooptimer nothardwarevblank;expectedvsyncstatusstart/hz/clientsmeasurednext.
ClockRule0checked334upstreampatches/historiesnonefixmicroconversion; minimal
native-microuptime-precision.patch usesremainder*USEC_PER_SEC/frequencyinstead
truncatedticks/us. ActualCconversionUBSan8742cases/7freq/uint64boundsPASS,
old19.2MHzboundary-10526usreproduced/newnondecreasingPASS. WiredD94series,
NEWARMData-configkernel-onlyrunhttps://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43630565056
watcheractive.
SeparaterawAPFSinodeaudit upstream-userspace-repair.md confirmsseedmobile99:99
vsdonor501:501/rootandtmp99vs0. NotFUSEownerdisplayartifact. NewisolatedmacOS
copyrepairscript sourcebackednumericmetadata fromverifiedOS/private/var,ownersON,
explicitgeneratedpathrules/unknownrefusal/symlinkancestorprotection/originalhash/
UUID/size/role/contentinvariance;hostplannernegativecasesPASS. NoSetup/migration
orjobspolicymutations beyondpreservedwatchdogoverride. NEWownershipworkflow
autoPushrunhttps://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43630564596
watcheractive,don'tduplicate. ActualAPFSrepair/fullkernelbuildpending. Nextvalidate
bothartifacts/rawinodes thenpackcombinedtimer60/clockfix/seedownership withsame
GOPdiagnostics. No phoneops. No bulkupstreambump/blanketdaemondisable.

**b11hardware numericdisplaystatus definite stage10butoneblank/noVSYNC:** fresh
recovery57b5c80b604s/303rounds/errors0. Firstslotstage3pending,fromt2s stage10
fail0/meta0/init1/start1/parent1/probe1/released0/surface0found1/DT1/register1.
PMstage4status0calls4callback1ordinal1,client5status0,accepted1rejected0.
GOPphysicale3940000/virtualfffffff8b5307000,length13234560,width1220height2712,
depth32stride4880display1rotate0. Finalt604s transfer1status0bytes13234560,
retirement1retired0frames1copy0;timelineW1~481ms98msdone,blank1,vsynchz0/sent0/
arms0/start0refresh0. Waiting30minnotjustified:realproviderpublishesearlybutno
continuedframeprogress/vsync. Need auditactualtimer/vsyncbootarg gating+userspace
renderprogress next;do NOTinferdiskbottleneckfromnoimage.
Tailkernel580..679sexistingdaemonsrespawn/containermanagerdstuckdemotions.
APFSunsignedelapsed18446744073709s+negativeflush-9657us repeatsknownmicrouptime
truncation/backwardsstep,notliteraldisktime. NoUFSreaderror/timeoutsestablished
fromretainedtail;loggingwrites0errorsnotrootreadbenchmark. No reboot thisturn,
read-onlyrecoverypull. Userconcernslowboot acknowledgedneedmeasurement.

**Nov15b11RAMbootissued explicituserboot:** serial42fe0af2alreadyfastboot,
WindowscopySHA828ea1d3...verified. WindowsfastbootbootSending49244KBOKAY1.172s,
BootingOKAY0.284s,total1.470s. No flash. Awaitrecoveryfreshnumericdisplaystatus
slots,don'tclaimvisibleoutput/GPUfromhandoff. NoADBdevicephonewhilefastboot.

**43624330838SUCCESS / b11displaystatusbuiltNOTbooted:** artifactdecrypted,
candidatee582bc3c...hashverified,missing{},5stubs218adapters. Independent
check-b11.py exit0actualcompiled44weaknumericguards/statusfirstrows/stage0/8/10
filtertests,safepropertiesstub(no diagnosticdanglingptr),failure/consoleproducers,
379PACslots/all218adaptersverified. Trustplacement/paniccapturefreshlayoutrematch
2437byteexactdiff;finalkernela79412e713770251d5b901c6ca58d2208eb83dd85e5f27721afcb5dbaa76f72d.
Packedstaging/amethyst-d94-b11/Mu-amethyst-d94-b11.img size50425856 SHA
828ea1d352625ec4e8e3c2617cb379d7e5b93adb4bb55dc013e7b6a764e09d5d.
Shippedkernelcmpexact,newRAMseed2e0525e0...exact,534byteargslog-display1/GOP/
CPU-present/persist0PASS. NOTBOOTED,no phoneops. NextsingleRAMrun persistent
slots numeric[displaystatus] willidentifyactualfailuregate insteadwrappedmsgbuf.
No displaydrawing/GPUruntimeclaim.

**b10blankdisplayaudit/statusfix6696a88 CI43624330838:** userexplicitno display
drawn;longuserspace notUIqualification. b10-display-path.md provesnoargument/
RELEASEstartup mismatchbutmissinglog-displaygroup+numericlifecycleexportmeans
earlystageswrappedandemptyheaders. Alsoactualstaleobjectreadnative_legacy_
properties_now onreleaseddiagnosticpointer latentbug. AddedexistingBootLogearly
compactnumeric [display status]rows stage/firstfail/meta/init/start/surface/DT/
registration/PM/client/swaps/transfer/GOPmap/console values,zero/-1retained,weak
guards,existinglog_display_on only. Probe recordsfirstfail+realconsoleoutcomes;
unsafeoptionalpropertyserializerdisabled,nounretainedpointerdereference.
Hostextractedcompose/filterteststage0/8/10PASS,379fixturePASS,diffPASS.
No framebufferownership/newthreads/hardwaretouch. Commit6696a88pushed,NEWARM
Data-configkernel-only: https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43624330838
watcheractive. OnsuccessnewcontainerMUSTaddlog-display=1 andsamewatchdogdisabled
RAMseed,verifyshippednumericglobals+safepropertyreaderbeforeboot.
Separate d94-vmstats-abi.md:104wordcapacitylegitimateD94butactualMIGclient accepts
native62wordreply/copies248bytes/returns62success;observedconsumersprefixonly.
WarningNOTprovenmemorycorruption/displaycause,nofakeextendedstatspatch.

**Nov15b10freshrecoverylog520aa37a sustaineduserspace:** serial42fe0af2recovery,
pull396rounds/errors0,writeruptime790s,GPT1/1,state2. Tailkernel730..858s
userspacedaemonactivity;no retainedwatchdogpanic/reboot/PID1exit. This supports
knowncriticalwatchdogloopavoided,notproofabsenceearlierwatchdog/UIcrashes because
msgbufonlylast16KiB andslotscontainonlyheaders (noearlydisplayrows).
26retainedwarnings cameracaptured62 host_statistics64 count104>native62,clear
newDarwinABIcompatibilitylead;doNOTassumememorycorruptionwithoutreplycontract
audit. Repeatedfairplayd.H2/bluetoothd/gamed/pfdrespawn+missingcontainers,
containermanagerdstuckdemotiontimeouts. NoSpringBoard/displayinitrecordsremain,
cannotclaimCADisplay/renderingfrom13minuteuptimealone. Recoveryfiles
/tmp/opencode/aston-audit/recovery-d94-b10/untagged-520aa37a. Nextauditactual
HOST_VM_INFO64newstructure andboundeddisplaytelemetryretention without another
blindboot. No write/reboot/CI thisturn,read-onlylogpull only.

**Nov15b10RAMbootissued userexplicitboot:** serial42fe0af2alreadyfastboot(no
recoverypull),WindowscopySHAe012ab4c...verified. WindowsfastbootbootSending49244KB
OKAY1.197s,BootingOKAY0.298s,total1.509s. No flash. Awaitruntime/freshrecoverylog
watchdogoverrideeffect/realGOP/CADisplayprogress;noUI/GPUclaimfromhandoff.

**43564663521temporaryRAMwatchdogpolicySUCCESS/b10builtNOTbooted:** encrypted
artifactdownload/decrypt,newseedSHA2e0525e0b7d936a80ef9e8579e32780e83882e0de1228aefa37e7942d4b665a0
verified,originalb981e18d...provenance,logical2GiB/newestAPSBDataUUID9EE3...role40
unchanged. Reconstructedrawsparsehostreadonlyapfs-fuseactualdisabled.plistONLY
watchdog/watchdogdtrue anddisabled.migratedpresentPASS. Originalseedpreserved.
Packedb10SAMEb9kernel92336b28...nootherkernelchanges,newRAMseedonly.
staging/amethyst-d94-b10/Mu-amethyst-d94-b10.img size50425856 SHA
e012ab4cc5ea0fde279cce46e4227b9e18f23212e9969b614ccf9564a26f6ffe.
Extractedkernel/newseedcmpexact,displayargs/persist0PASS. NOTBOOTED,no phoneops.
WatchdogbackendstillUNAVAILABLEnotimplemented;explicittemporaryuserspacejob
override reversiblewhenbackendready. NextsingleRAMtestmeasuresdisplayprogress
withoutknowncriticalwatchdogrespawn;don'tcallUIrender/GPUproven yet.

**b9hardwarecriticalwatchdogSIGTRAP;RAM-onlyexplicitpolicycopygenerating:** user
watchdogd168criticalexitns2code5panic. Freshrecovery41debe7e60rounds/errors0:
watchdog56pc100288be8-main10027a000=ebe8sameIOWatchdoglookupassert asb8,
154/168respawns thencriticalpanic. UI earlierrecordswrapped,request_power_change
fId17displaytype0seen166s (notfullCADisplay/swapqualification). Noassumptiondisplay
fixedyet. ExistingBootWatchdogPolicy linkedbutnotcalled: hookgatedXNU_BOOT_PROGRESSn.
b9-watchdog-policy.md verifiesCPUbinary+policyharnessASAN/UBSANPASS. Ratherthan
fakeIOWatchdogbackend/disablekernelchecks,controlledtemporaryRAMseedcopy disables
ONLYcom.apple.watchdogd/com.apple.watchdog+disabled.migratedmarker,reversible by
originalseed. Originalhashb981e18d.../UUID9EE3...enforced;script nowexplicitUUIDarg.
Commitsdffd26b/702b969pushed,firstdispatch404becauseworkflownewregistration;
featurebranchpushtriggeradded thenexplicitdispatchsuccess:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43564663521
watcheractive. Checknoconcurrentduplicatepushrunbeforelaterdispatch;nonewaslisted
atstart. No phonewrites/reboots,DataremainsRAMonly. HostseedcheckPASS/YAMLPython
parsePASS;actualAPFSmutationvalidationpendingCI. MustlabelwatchdogUNAVAILABLE,
notimplemented;removeoverridewhengenuinebackendexists.

**b9RAMbootissued explicituserboot:** prebootrecoverylogsavedID443ee55751rounds/
errors0,WindowscopySHAb2688eda...verified,adbrebootbootloaderthenWindowsfastboot
bootSending49308KBOKAY1.197s,BootingOKAY0.296s,total1.509s. No flash. Await
runtimeGOPdisplaypublication/CADisplayresult andfreshrecoverylog;don'tclaimGPU
orUIbootfromhandoff. b9firstrealD94displayprovider path.

**DisplayCI43561683220SUCCESS,b9realproviderbuiltNOTbooted:** artifactcandidate
c982ce08...hashverified,missing{},5stubsunchanged,218originaladapterrecords.
Independentcheck-b9-display.py verifiesactualRELEASElifecycleaftervfsinit/IOBSD,
379PACvtableentries/D94C2D2/metaclass/userclient/IOSurfacefactory,all218adapters
fixeddonortargets;optionalGPUofferfalse/DPUmap0state0,noSM8550hardwareenabled.
Newtcplacementcaveb2e1000,DeviceFBpanicrendererb2e2000rematched,2436byteexactdiff,
userspacefaultdiagretained;check-b9.py/build-b9-panic--checkexit0.
Finalkernel92336b288b1dd83c504d2038c4c09c73619c7e5f4f127295be772c946b71d65c.
Packedstaging/amethyst-d94-b9/Mu-amethyst-d94-b9.img size50491392 SHA
b2688eda1e793cb4fec8fcc2e826d459a0a805d73d735c5f69413de922ec9522.
Extractedshippedkernelcmpexact,520byteargsenablemobilegraphicsload/legacyregister/
phase4/gop/pm/validate/transfer/CPU-present,log1/persist0/uifaultdiagretained,
noGPUdirectscanoutargs. NOTBOOTED,nextRAMtestmustmeasureactualdisplayenumeration/
start/GOPgeometry/CADisplaycount,nothardwareGPUclaim. No phoneops thisturn.

**43560522151displayarchivecompilepassed/optionallinkhooksfixed:** finalnative
linkundefinednative_adreno_fences/fences_detail,native_gpu_trace_enabled/record,
native_ufs_dpu_map. CPU/GOP-onlyarchive hasoptionalweakimports butkernelldneeds
definitions. Addedweak truthfulunavailablehooks(traceoff,fencesfalse/outputs0,
foreignDPUmap0);realstrongGPUbackendsoverride,no fakecompletion/securitygrants.
FirsthosthelpertestWerrorfailedmisleadingindentation;parentmistakenlycommitted/
dispatched47bc454beforecheckingallcommands. Immediatelyfixedguards98fad04,
exacthelperC++WallWextraWerrorcompile+executionPASS,ownsuperseded43561644928
cancelled. NEWonlyactiveARMData-configdisplaybuild:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43561683220
watcheractive. 379slotfixturetestPASS,noactualdisplay/GPUhardwareclaim. No phoneops.

**Display43559313957source-selectionfailure fixed/new43560522151:** compiled
otherarchives thenlegacybuilderclang missingtree/drivers/GopFramebuffer/
NativeLegacyDisplay.cpp. RootcauseD94componentsdidn'tselectGopFramebuffer,
archive recipealone doesn'tlinkcomponent sources. D94prep nowaddscomponentONLY
disposableD94board,defaultfallbackunchanged. Actualdisposablecore+boardgitinit,
defconfig+unixnutreeresolvePASS confirms realNativeLegacyDisplay.cpppresent.
Firsttestcopywithout.git failedtreeprovenancecheck,correctedtestsetup,notclaimed
compiletest. Commit187680bpushed;NEWARMData-configdisplaykernel-only:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43560522151
watcheractive. ParallelGPUagentimplementednewbuild-amethyst-adreno-archive.py/
test+packageAmethystAdrenoDiagnostics.c (uncommittedparentreviewpending),derived
realD94acceleratorC2/D2/metaclass and168PACslots;hosttestsPASS,butnoarchive
successwithoutfirmware/votes/commandDBprovenance+pool/startupgates.
Reportd94-a810-acceleration-work.md. Do notclaimreadyGPUruntime. No phoneops.

**UserrequestsAdrenoimplementation/orgupstreamaudit;displayfirstCI43559313957:**
Inspectedactualboardmainhistory9a9a7cb,b2a18ee,8bd7b0d,2d12043 etc;reports
gpu-main-history.md/gpu-upstream-components.md: realA810fallback existsbutlocal
compile recipes;don'tstockenableSM8550Adreno740component. Existingorgforks
NativeAdreno/NativeMobileDisplayalreadyrelevantnetworks,no duplicateforkneeded.
Upstreamchangesidentifiednotbulkmerged(Adreno116/39divergentcommits). Stock
NativeAdreno pin26c1da7deferredfreefix retainedtarget,notstale scratch5e2b23e.
f658561 integratesdisplay-onlyIshtar379slotD94fixture/builder/probe withattribution,
RELEASErecipekernel_target,KITamethyst,exactkernelhashgate,allPACaudits,
no nativeslidecompensation,SM8550directGPUargumentsrefused;staleprobeoffsets
omitted. Data defconfiglegacyDISPLAYy,Adreno/mobileDISPLAYremainoff. D94-only
RELEASEearlyfamilyload/probeaftervfsinit +lateprobeafterIOKitBSDInit wired;
archivebeforeLTOreference. Checks test-d94-display.py379targets/diversitynames/
fixedadaptersPASS,test-amethyst-adreno negativeprovenancechecksPASS,selectionPASS.
AdrenoNEWscriptisVALIDATORONLYexplicitlyarchiveblocked(realD94acceleratorABI/
generatedfirmwareprovenanceunreviewed),notfakeGPUenablement. No newforks/deviceops.
NEWARMData-configkernel-onlydisplaycompile:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43559313957
watcheractive. NextinspectC++ABI/archive/startup/prelink/layout thenb9packdisplay
argsfromd94-display-integration.md,notSM8550DPU/GPUflags. ActualCADisplaycount
runtimeunverified. ContinueA810source/acceleratorqualificationafterdisplaygate.

**b8ACTUALfaultsidentified,notgenericSIGTRAP:** freshrecovery443ee55751rounds/
errors0. SpringBoardpc1a88e263c-cacheSlideb6c000=1a7d7663c,exactcacheUUID
1892d244-891c-3c95-a09a-2810410fdcf8 verified. FrontBoardServices
FBSDisplayMonitorinitializercold6 BRK0 assertion `[displays count] > 0`,caller
CADisplaydisplaysnil/zero. FramesSBSystemAppMain->FBSystemShellInitialize->
FBDisplayManagersharedInstance->FBSDisplayMonitor. b8-springboard-trap.md/check
exit0. Rootcauseboundarydisplayenumeration,wehavelegacy/mobileDISPLAYn.
Watchdogpc100820be8-main100812000+static100000000=10000ebe8BRK1,_os_crash
string`connect_to_kext() unexpectedly failed - unable to lookup watchdog KEXT
service`;IOWatchdoglookupabsent,notopen/PAC/hardwaretimeout. b8-watchdog-trap.md
checkexit0. Launchdconsecutivecrashpolicyknownbutrebootcauseconditional.
No bypassassert/disabledwatchdog thisturn. Rule0displayintegrationagent identifies
IshtarD94stage1display-onlyarchive+exact379targetfixture+existingGOP(noAdreno),
needsRELEASEstartupwire/buildercorrections,obsoleteoriginalnative-slidecompensation
mustremoved,D2metadata mismatch resolved beforecompile. Report
d94-display-integration.md,runnablecheck-d94-display-inputs.pyPASS,7prelinktestsPASS.
NextimplementminimalactualGOPlegacydisplayservice,beforeanotherboot. No device
opsbeyondrecoveryread,noCI/sourcechangesthisturn.

**b8RAMbootissued explicituserboot:** prebootrecoverylogsavedID42dfba4b56rounds/
errors0,WindowscopySHAf61649cd...verified. adbrebootbootloader thenWindowsfastboot
bootSending49348KBOKAY1.200s,BootingOKAY0.295s,total1.510s. No flash. Awaitfresh
runtime/recoverylog ui-fault exceptionpc/cacheUUIDslide andPID1rebootstate,
preservenormalexceptionbehavior;noSIGTRAPcauseclaimuntilsymbolication.

**43552874536SUCCESS / b8faultcapture builtNOTbooted:** artifactdecrypted,
candidateSHAdac9846e...verified,missing{},5stubs,5PMGRoriginaladaptersPASS.
Independent b8-readiness.md/check-b8.py exit0 actualcompiledfaultcapturebefore
exceptiondelivery,TOLOGBSDwrapper,PC/LR/ESR/FAR/regs/cachetelemetry/boundedcopyin/
gates+authorizedPID1rebootstate verified. Fresh rematchedtcplacement+DeviceFB
bothpanicpaths,2436bytesexactdiff,allotherfeaturespreserved. Finalkernel
e45b15ca28fc8b218637d9cb35e131598323c4ef1cc44a1687d4bf43868ff9eb.
Packedstaging/amethyst-d94-b8/Mu-amethyst-d94-b8.img size50532352 SHA
f61649cd4636949fedc6177d52c158b5c7084b02ea327f63768d94fa7a575c51.
Extractedkernelexact/effectiveui-fault1,ui-exit1,pid1diag1,hold10,log1,persist0
PASS. NOTBOOTED. NextsingleRAMtestthenfreshlog ui-faultpc/slideuuid attribution,
notguesscommonSIGTRAPcause. No phoneops thisturn.

**43552275700compileconflict fixed/new43552874536:** Gitpatchnowapplies,exception.c
116conflictingproc_best_name types. Prior615085cdeclarationwaswrong/redundant:
includedvm/vm_protos.h55ALREADYdeclaresconstchar*(structproc*);sys/proc.h has
differentchar*header view. Removedmanual declaration,useexistingMachAPI.
Gitapply--check+applyexactthreepatchchainPASS,existingconstsignatureassertPASS.
Commitabd322fpushed;NEWARMData-configkernel-onlyrun:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43552874536
watcheractive,samepolicy43184772128,no phoneops. RealTUcompilepending.

**43552014036Gitpatchparsefailure corrected/new43552275700:** beforecompile
gitapplyerrorcorruptpatchline186;handeditedhunkcounts/blankcontextGNUpatchaccepted
butGitparserrefused. Regenerateddiff fromactualbefore/afterfiles;gitapply--check
exactpid1/ui-exitchainPASS,gitapply--numstatPASS132+9+1+1lines. Commitde33dee
pushed;remaining gitdiff--check warnings are literal blankcontextlines inside
patchfile (notCsourcewhitespace),notclaimedPASS. NEWARMData-configkernel-only:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43552275700
watcheractive. No phoneops. Need matchCIgitapplyandrealTUcompile,nextartifact.

**PreemptiveMachAPIdeclarationfix615085c/new43552014036:** while43551720372active,
sourceauditfoundproc_best_name notdeclaredbyMachheaders (existingexception.c only
declaresproc_name_address). Addedexactsys/proc.h char*(structproc*)signature,
noBSDprivateheaderinclude/processselectorchange. Cancelledownsupersededrun
43551720372 deliberately,notinferredcompilefailure. Exactthreepatchchainfuzz0
PASS,signaturesmatch,diffPASS. Commit615085cpushed;NEWARMData-configkernel-only:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43552014036
watcheractive. Olderwatchernotificationcancellationexpected;don'tretryoldrun.
No phoneops.

**43551008466realheaderfailure fixed/new43551720372:** exception.c113 fatal
sys/subr_prf.h unavailableMachincludeview. MovedONLYui_fault_log variadicTOLOG
wrapperintoBSDkern_exit.c(existingheaderalreadyworks),Machexceptioncodecalls
narrowexternwrapper;privatetask/registerlogicstaysMach. Appliedexactpid1/ui-exit/
ui-faultseriesintmpcopyPASS,diffcheckPASS. Commitd020126pushed,
NEWARMData-configkernel-onlysamepolicy43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43551720372
watcheractive. No phoneops/repeatboot;realTUcompilegateexplicitpending.

**UIexceptioncapture e3ee51e CI43551008466:** noSIGTRAPcauseguessed. Reused donor
exceptiontriagehookinMachTU to capture first8selectedprocessfaults and separate
PID1acceptedrebootstatebudget. Optinui-fault-diag1 defaultoff,TOLOG PC/LR/SP/FP/
savedESR/FAR/x0..7/mainloadaddr/cachedsharedregionUUIDslide/12boundedcopyinframes,
no guessedPACmask,contextguardbeforecopyin;normalexceptiondeliveryunchanged.
BSDonlygetsrebootcaptureprototype,noMachprivatetaskstructleak. Patchdryrunfuzz0/
APIguards/sourcechecksPASS;actualTUlocalprobeblockedpreexistingtypes.h,soCI
compilegateexplicit. Selection/diffPASS,2targetedfilespushed.
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43551008466
ARMData-configkernel-only,policy43184772128,watcheractive. Onsuccessverifyactual
symbols/callsite,packnewimageui-fault-diag1+ui-exit1;freshlayoutpatchesrematch.
No phoneops/repeatboot thisturn.

**b7freshlog42dfba4b attributesUItraps+launchdreboot:** recoverypull56rounds/errors0,
uptime110,GPT1/1. ui-exit SpringBoard145,liveactivitiesd121/134/143,watchdogd122,
devicerecoverytool154 status5signal5ns2code5none=SIGTRAP (NOTpriorPID1SIGABRT).
LaterSpringBoard156/backboardd65signal15shutdown;PID1launchdexplicitreboot
accepted/kernel-entryhowto0,Dataunmountcompleteclean. No retainedvnodeunderflow,
userpoweroffreported;encouragingownershipfixbutnotfullbacktraceproof.
configd41PACviolation167.233021alsoseen;don'tconflateBRK/PACwithoutregs.
b7-shared-sigtrap.md agent actualcachetraps os_variant/lockdown/pthread/locks
verifiedsignaturesbutnonecausallyidentifiedfromsignal5. Nextcombineduserexception
capturePC/LR/ESR+regs+boundedframes/slide BEFOREexit viaexistingdonorhook,
preservenormalfailurebehavior;don'tguessbypasssysctlvalues/lockchecks.
Onlyrecoverylogread thisturn,no repeatboot/CI/sourcechange.

**b7RAMbootissued userexplicitboot:** recoveryserial42fe0af2present,prebootlog
savedID8c3e1500(52rounds/errors0);WindowscopySHA2bb28e8a...verified,adbreboot
bootloader thenWindowsfastbootbootSending49348KBOKAY1.199s,BootingOKAY0.308s,
total1.521s. No flash. Awaitfreshlog/UIexit+rebootattribution andvnodepanicresult;
do notclaimownershipfixruntimeconfirmedfromhandoffalone.

**43545935005SUCCESS / b7ownership+attribution builtNOTbooted:** downloaded/
decrypted,candidateb4d5a73e...hashverified,missing{},5stubs,5originaladaptersPASS.
Independent b7-readiness.md/check-b7.py validates compiledkernprocborrowedroot
specialcase BOTHnative/LTO,UIexit/rebootdiagstrings/budgets/callers,rematched
freshtcplacement+bothDeviceframebufferpanicpaths,PACpreserved,no unsafeUFSflush.
Finalkernel2531ad7c52434d061e876b6fc289be693c0616d797312571dd191535e4715ab1,
strictrepeatcheckexit0,2436changedbytesexactintervalsrecordedb7-evidence.json.
Packed staging/amethyst-d94-b7/Mu-amethyst-d94-b7.img size50532352 SHA
2bb28e8a2d5510019dbaca2362f0efafe5fd7b67867caf3351a0bc37c02e5b73.
Extractedshippedkernelcmpexact;effectiveargsui-exit-diag1,pid1diag1,hold10,
log1,persist0PASS. NOTBOOTED/no deviceops thisturn. NextRAMtest thenfreshlog
ui-exit/reboot attribution. Ownershipfixreal,paniccausality/runtimeUIunverified.

**b6hardware progressesUIstartup; unionrootownershipfix CI43545935005:** userpanic
vnode_rele_ext vpfffffff7ec4e8000 usecount-1=4294967295/tag0/type8(VBAD)/flags84801,
vfs_subr2688. FreshrecoveryID8c3e1500,52rounds/errors0,uptime102. Manydaemons+
SpringBoard/backboarddstarted;SpringBoardcorpses/relaunches,shutdownAPFSmarker
222.419669,TCP/corpsecleanup. PreviousPID1SIGABRTgate cleared thisrun,notfullUI.
105retainedvnodeGOTslots/73identitiesauditPASS,norefle/badputsubstitution.
Independent b6-checkdirs-ownership.md verifiesACTUALfreshnativeb6checkdirs bug:
set_rootvnode lendskernprocrootwithoutusecount;callbackreleasesborrowedroot,
thencheckdirsreleasesactualrootagain. Existingasteroidsnative-union-root-checkdirs
sourcefixport,cannotprovehardwarepaniccausalitywithoutstackbutdefectreal.
ChooseownershiprepairNOTAstonRO-unmountskip/underflowcheckbypass.
Combined boundedui-exit-diag1attributionpatchselectedUIdaemons32exits,reboot8events,
realstatus/reason/ns/codeflags/sanitizedboundeddescTOLOG,accepteduserspacereboot+
kernelshutdowncaller;nosleeps/holds/policychangesforUI. HelperASAN/UBSANtestsPASS,
dryrunfuzz0PASS,fullTUlocalsyntaxblockedtypes.h soCIcompilegateexplicit.
Commit739aa94pushed;NEWARMData-configkernel-onlyrun:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43545935005
watcheractive. Onsuccessinspectownershipcompiled+diagthenb7packageenable
ui-exit-diag=1;freshlayouttcfix/rendererrematchrequired. No phoneboot thisturn,
onlyrecoverylogread. Source/displaycapabilitystillheadless/noGPUdriverclaim.

**b6RAMbootissued userexplicitboot:**42fe0af2alreadyfastboot(no recoverylogpull),
WindowscopySHA3d7fd56a...verified. WindowsfastbootbootSending49344KBOKAY1.200s,
BootingOKAY0.299s,total1.513s. No flash/partitionwrite. Awaitruntimeandfresh
recoverylog;PID1diag1/hold10shouldretainabortframesifcontextchecksallow. Do not
claimpubliclockdownfixresolvedSIGABRTuntilruntimeevidence.

**CI43501699268SUCCESS,b6builtNOTbooted:** downloaded/decryptedartifact,candidate
ae8de942...cmphashPASS,missing{},5stubsunchanged. Independent b6-new-features.md
verifies realpubliclockdownsysctlstartupregistration/actualstatehandler (inlined
getter),PID1specialexitdiagnosticcalled/defaultoff/hold10cap30/contextchecks/
realmsleepcompiled,ALL5PMGR.original BTI/B adaptersfixedretainedtargetsPASS.
Rematchednewtrustplacement b6tcfix20bytes,caveb2d6000..6010;oldb5cavenowlive
hibtextDONOTreuse. Newb6rendererzeroexecb2d7000..7b30,no refs,devicewriteATgate,
PANIC_FLUSH0,bothhooksafterPACIBSP,scopeddiffchecksPASS. Freshkernelfinal
500b5d7168f0da923bd13094be5575931d6ce95ed97640d3bb6246d7a5309bfd.
staging/amethyst-d94-b6/Mu-amethyst-d94-b6.img size50528256 SHA
3d7fd56a7ef7dc73099cb69a6537313e106bb3a028d3a01686f4790ea285c10a.
Shippedkernelextractedcmpexact;argsPID1diag1/hold10/log1/persist0PASS. NOTBOOTED.
This tests realmissingAPIcompatfix and suppliesbroadabortframesifstillfails;
don'tclaimSIGABRTcauseconfirmeduntilruntime. No phoneops thisturn.

**43499286969capacitycancel/new43501699268:** job132140532276no steps,
annotationhostedrunnernotacquired/ARM64capacity. ONEnewserialARM/Xcode26.6
Data-configkernel-onlyattempt327dd09,verifiedpolicy43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43501699268
watcheractive,no sourcechanges/deviceops. Awaitactualcompiler/linkerresult.

**43496859769capacitycancel/new43499286969:** job132132117033no steps,
annotationhostedrunnernotacquired/ARM64capacity. ONEnewserialARM/Xcode26.6
Data-configkernel-onlyattempt327dd09,verifiedpolicy43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43499286969
watcheractive,no sourcechanges/deviceops. Awaitactualld/prelinktest.

**43494468661capacitycancel/new43496859769:** job132123837562no steps,
annotationhostedrunnernotacquired/ARM64capacity. ONEnewserialARM/Xcode26.6
Data-configkernel-onlyattempt327dd09,verifiedpolicy43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43496859769
watcheractive,no sourcechanges/deviceops. PMGRtwo-objectfullldtestpending.

**43493029811actualPMGRassemblerfailure fixed/new43494468661:** PID1VMAPI
compileblock passed bootstrap,archive06adapterassemblyfailed `fixup value out of
range` b*.original because sameassemblyunitdefinesfarN_ABS targets. RULE0existing
shengbuild-gop-legacy-display-27.py compilesadaptertext andabsoluteaddresses as
SEPARATEobjects. Adoptedexactpattern,archiveboth;retains externalBRANCH26untilld,
prelinkBTI/B originaladaptervalidationunchanged. Installedclangarm64eMachOprobe
assemblesbothPASS;llvmobjdump relocation ARM64_RELOC_BRANCH26 _pmgr_test.original
verified,diffPASS. Commit327dd09pushed;NEWARMData-configkernel-only:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43494468661
watcheractive,policy43184772128same. No phoneops. Need full ld/prelink check next,
notclaimactualnativebuildPASSfromisolatedassemblyprobe.

**43490564476capacitycancel/new43493029811:** job132110337448no steps,
annotationhostedrunnernotacquired/ARM64capacity. ONEnewserialARM/Xcode26.6
Data-configkernel-onlyattempt5028b47,verifiedpolicy43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43493029811
watcheractive,no sourcechanges/deviceops. Awaitactualcompilerresult.

**43488061697capacitycancel/new43490564476:** job132101680398no steps,
annotationhostedrunnernotacquired/ARM64capacity. ONEnewserialARMXcode26.6
Data-configkernel-onlyattempt5028b47,samepolicy43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43490564476
watcheractive,no sourcechange/deviceops. ActualTUcompile correcteddiagnostic
andpubliclockdownfixstillpending;do not infercodefailurefromcapacity.

**43485631938capacitycancel/new43488061697:** job132093200964no steps,annotation
runnernotacquired/ARM64capacity. ONEnewserialARMXcode26.6Data-configkernel-only
attempt5028b47,samepolicy43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43488061697
watcheractive,no sourcechanges/deviceops. CorrectedVMAPIpatchnotyetcompiled.

**43484566427realcompileAPIerror fixed/new43485631938:** correctedARMheader
accepted,nowkern_exit.c2010undeclaredvm_map_min/vm_map_max (VM-private macros
hiddenfromBSD). Removedprivate-mapdependency,uses explicitlylabelled configured
MACH_VM_MAX_ADDRESSuserceilingwithmach/vm_param.h;actualmembershipvalidatedby
copyineachframe,alignment/monotonicity/32frames/1MiBboundsunchanged.
Patchdryrunfuzz0PASS,sourceassertprivate macrosabsent+copyinretainedPASS,
diffcheckPASS. Commit5028b47pushed;NEWARMData-configkernel-onlyrun:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43485631938
watcheractive,samepolicy43184772128. No phoneops/repeatboot;actualTUcompile
stillpending. CorecryptonoXNU symbolssecondaryagain,notnewcryptodefect.

**43482065801capacitycancel/new43484566427:** job132080803605no steps,exact
annotationhostedrunnernotacquired/ARM64capacity. ONEnewserialARMData-config
kernel-onlyattempt9052ca5,samepolicy43184772128,Xcode26.6:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43484566427
watcheractive,no codechange/deviceops. CorrectedPID1diagnotyetcompiled.

**43479466461capacity-onlycancel/new43482065801:** job132071872694no steps;
annotationhostedrunnernotacquired/ARM64capacity,notcompilefailure. Userstanding
serialretryinstructionapplied,ONEnewARM/Xcode26.6Data-configkernel-onlyrun9052ca5:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43482065801
watcheractive,sameverifiedpolicy43184772128. No codechanges/phoneops. Inspect
actualfailurebeforefurtherretry;lockdown/PID1captureTUcompilationstillpending.

**Actual CIcompilefailure43477938430 fixed/new43479466461:** bootstrapfatal
kern_exit.c80 machine/thread.h nonexistent,corecryptolaterNoXNU namessecondary.
Correctinclude arm/thread.h wherefind_user_regsactuallydeclared; sched_prim.h
alreadyincludedforpreemption_enabled,thread_status.h provides savedstate helpers.
Patchdryrunfuzz0PASS,14hostmockcapturecasesPASS,diffcheckPASS. Commit9052ca5
pushed,NEWARMData-enabledkernel-onlyrun:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43479466461
watcheractive. Realfullcompile stillpending,don'tclaimhostmodelprovesTU.
No phoneops orrepeatboot. Onsuccessvalidatepublicsysctl+PID1diagnostic/PMGR
adapters and rematchnewtcplacement/panicrendererbeforeb6pack.

**PID1abort concrete missingAPI fix+exitcapture CI43477938430:** read-onlyagent
lockdown-sysctl-audit.md verifies actualD94launchd calls os_lockdown_mode_enabled,
actualSystemOSlibsystem_darwin queriessecurity.mac.lockdown_mode_state_public and
_abort_report_np onlookupfailure. Freshnative/ref lackname/symbol/registration,
security.mactree+realgetterexist. Add read-onlySYSCTL_PROC real
get_lockdown_mode_state(),no forcedfalse/enablebypass. Causalityfitsbutnotproven
untilruntimeframes/progress. AddedoptinPID1exitdiag source beforeoriginal
coredump/sync/panic: contextchecks,ARM64regs,32frames/1MiBboundedcopyin,rawPAC+ret47,
TOLOGmsgbuf output,10s schedulablemsleephold max30outsidelocks;notpanicUFSflush.
Exactpatchdryrunfuzz0PASS,14hostmockcapture/bounds/context/holdcasesPASS,
selection/sanitizermodeltestsPASS,diffcheckPASS. RealTUcompileunverified untilCI.
Commit7ca2b80pushed (includes earlierdurablePMGR.originaladapterc6ca706).
NEWARMkernel-only DataUUIDbuild:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43477938430
watcheractive. OnsuccessvalidatePMGRadapters/native_original_branches,realpublic
sysctl,diagnostic symbols beforepack;enablepid1-exit-diag=1hold10innewcontainer.
Need re-matchtrustplacement tcfix/caves/screen addresses forNEWnative,notoldb5.
No phoneops/newboot thisturn,preserveb5SIGABRTevidence.

**b5visiblepanic PID1SIGABRT confirmed:** usertranscribed `initproc exited -- exit
reason namespace2subcode0x6descriptionnone`; source reason.h OS_REASON_SIGNAL2,
signal.h SIGABRT6. FreshrecoverypullID58396801,33rounds/errors0,roots/Data mounts
andAMFI24return0confirmed,tailinit_exclavekit140.761191,nopanictextretained.
RendererDevicequalificationfix nowexposesactualpanic;do NOTcallinit_exclavekit
causefromtail. Read-onlyagent b5-launchd-abort.md: targetinit_exclavekit ordinary
pathsreturn0evenunsupportedExclave;launchdservicecache processing follows it.
Donorcacheframepair46618/1bba4potentiallead;explicitNoServiceCacheabortnamespace7
code1doesNOTmatchours2/6,notproven. Read-onlyOSmounttmpaston-audit/b5-os-mount.
Next capturePID1abortbacktrace+boundedconsole/syscall/childstatushistory, preserve
checks. Noautomaticreboot/newimage/no policybypass thisturn.

**Nov14b5RAMbootissued userexplicitboot:** serial42fe0af2alreadyfastboot,not
recovery so no logpull/reboot. WindowscopySHAmatchb131f1ae...;fastbootbootb5
Sending49344KBOKAY1.198s,BootingOKAY0.305s,total1.518s. No flash. Awaitactual
panicdisplay/runtime thenfreshrecoverylog;do not assumeATDevicefixguaranteesUI.

**b4freshlog424e0427; concrete panicrendererDevice-mapbug fixedb5NOTbooted:**
33rounds/errors0sameearlyuserspacetailAMFI24success theninit_exclavekit121.616592,
nocapturedpanic. NewAPFSunsignedelapsed18446744073709warning offlineclockaudit
provesmicrouptime19.2MHztruncated19ticks/us createsbackwards~10526usstep atsecond
boundary. All7APFSclockGOTs correct;warningcontinuesafter,notpaniccause. No clock
changecombined b5. Reports b4-clock-audit.md,b4-framebuffer-audit.md.
Renderer concretebug:B4reusednormalmemoryreadablegate forframebuffer, rejects
PARDeviceattribute nibble0;actualconsolemapsframebufferVM_WIMG_IO7Device.
B5only correctsframebufferqualificationAT S1E1Wsuccess (acceptDevice+writeperm),
strings/argsstrictreadgate unchanged,PAC/adapters rebuilt byELFsymbols,cave/diff
checksPASS;no guessedframebufferphysaddr,unsafeUFSflushdisabled.
Hostkernel9c048134c8787de39fb435324ed4a7341f898bb514dc82168344dd2233ef2961.
staging/amethyst-d94-b5/Mu-amethyst-d94-b5.img SHA
b131f1aecd53874900765f9f42c6332d6bb8b62aaf8a999514bc9c4ed77ab2c5,
extractedshippedkernelexactPASS. NOTBOOTED,nohardwarescreenvisibilityguarantee.
No devicewrites/reboots thisturn,onlyb4recoverylogread. Nextsingleb5RAMcapture
withuserapproval/currentstandingcycle,oruseexistingpanicscreenifavailable.

**b4RAMbootissued userexplicitrebootfastbootboot:** recoveryserial42fe0af2present,
savedprebootlog againID53ecf98a33rounds/errors0. WindowscopySHAmatch05bc50c5...
adbrebootbootloader thenWindowsfastbootbootb4 Sending49344KBOKAY1.199s,
BootingOKAY0.295s,total1.508s. No flash/partitionwrite. Awaitpaniccapture/runtime;
b4broadpanicscreen diagnostic,notnewfunctionalfix. Ifscreenreadablecapturefirst
message before recovery;periodicUFSlog maystillomitpanic (flushdisabledsafe).

**Userconfirmsb3PANIC; broadb4capture builtNOTbooted:** freshlogprovesroots/Data/
AMFIclient passed but missespanic. Offline b3-panic-capture.md proves no calls to
boot_log_panic_flush, weaknative_ufs_log_panic returns-1 misinterpretedassuccess,
normalUFSblockinglock unsafeatpanic. DO NOTwireflushblindly. Donorresearch
b3-donor-next-stage.md no proveninit_exclavekitfix;boundedspawnENOTSUP45 is
conditionalhypothesisonly. Needactualpanic beforechanges.
Read-onlyagent builthostcapturekernel4081476b3ae7c668ae5a7eb1bc9437dd77a9098c459ad643a3bfc599fb6904ac,
build-b4-panic.py --checkPASS. HooksBOTHpanicentry/commonfunnel AFTERpreserved
PACIBSPbeforeSPchanges,formattoRAMbeforeframebuffer,framewalkomitted,
PANIC_FLUSH=0. Zerocaveb2d5010..preservestcfix5000,PMGRallotherbytesunchanged.
Staticcompiler/ABIadapter/relocation/caverefscanchecksPASS;noQEMU/hardwaredisplay
guarantee(_vinfofallbackrequiresvalidmapping). Reportb4-panic-renderer.md.
Packedstaging/amethyst-d94-b4/Mu-amethyst-d94-b4.img SHA
05bc50c5e1edae78490a8edf39e580821c8fb5063c95369aff5deb70d697abfa;
shippedkernelextractedcmpexact4081476b... . NOTBOOTED. Usermayalreadyhavepanic
photo;useitfirstifavailable. No phoneops/CI thisturn;b3milestonepreserved.

**HARDWARE b3freshD94userspace + AMFIopen SUCCESS:** recoverypull now
/tmp/opencode/aston-audit/recovery-d94-b3/untagged-53ecf98a (NOToldi176),header
bootid1408039306/53ecf98a,uptime64s,33rounds/errors0,GPT1/1,state2. NativeUfs
publishes/mountsSystemOS8B599D21... andOSE3F4AED0...;Data9EE3A4D1... mountcomplete
newestcheckpoint15,logical2GiB. AMFIquerymodeliPhone17,2. Launchdearlytasks run.
Exact finalmilestone msgbuf lines175/176: AppleMobileFileIntegrityUserClient::
isCdhashInTrustCache Process24checkingcdhash; ReturningIOReturn0x0toprocess24.
OLDlaunchd_cache_loader AMFIopen/SandboxEPERMblocker cleared THIShardware run.
No retainedUNMAPPED/panic/fatal inthiscapture. Tailends136.910169init_exclavekit
nonfatalAMFIacceleration65285; do NOTcallitnewcausewithoutmoreevidence. No fullUI
result or finalpanic established. No moreidentityprobesneeded. Missing /filesystems/
fstabwarningsremain; earlyData/root mount allcomplete so not assumedfatal.
Next audit broad userspaceprogress/logdurability/Exclave expectation using donor
reference existingfixes first, preserveb3successfulmilestone;no blindnewboot.
No devicewrites/reboots thisturn,onlyread-onlylogpull.

**b3RAMboot hardwarehandoffOK:** userexplicitboot. InitiallyAndroidserial42fe0af2
listed,WindowsimageSHAmatch60aa857d...;adbrebootreturnednotfoundbecausephone
transitioned,subsequentfastbootdevicesconfirmed42fe0af2. Windowsfastbootbootb3:
Sending49340KBOKAY1.200s,BootingOKAY0.287s,total1.502s. No flash/devicepartition
write. Awaitruntime/recoverylog,newbootIDrequired (b2logwasstalei176). Notproof
PMGRfixruntime/userspace;noautomaticrepeatbootuntilnewresult.

**b2userconfirmsPANIC; recoverylog stale; b3PMGRrepair builtNOTbooted:** recovery
pull succeeded after WindowsADBwrapper explicit -s fix (emulatoralsoattached,
Windowsadbdoesn'tinheritWSL ANDROID_SERIAL). PulledID4984e91f,msgbufSHAd2ce2ad8...
byteidenticali176,noNEWb2log; pstoreempty. Don'tinferoldSandboxfailure. Offline
b2audit found12native->retainedPMGRbranches wrong by+3944000 (native textmove),
includingC2x5,D2x3,nubcreate/readx2/write. All6metaclassGOTuses+2fixedpointers
alreadycorrect. Existingprelinker supports BTI/B .originaladapters, directN_ABS
bindingshadbypassedthem. Durablebuilderfixc6ca706pushed. Strict hostpostlink
fix-d94-pmgr-emitted.py guardsnative/inputhash/targets and fixes12branches only,
36changedbytes,tcfix/PAC/locksunchanged;normal/-OchecksPASS. Candidatekernel
dc41a345e0cb210ba1b868a33d347f4d4623918dbf102c2f6ad3c3959332288a.
b3containerstaging/amethyst-d94-b3/Mu-amethyst-d94-b3.img SHA
60aa857d39f7349332a03a34ef7f26bcfff8c60154cd8df2b649eae7f28bc708;
shippedkernelextractedcmpexact,Windowscopyready. NOTBOOTED thisturn. Userreports
b2panicbutno freshpersistentlog; broadpanicscreenmaystillneededbeforeUFS.
No devicewrites/flash,onlyrecoveryreads. PMGRprecedesSourceStorage/logger, verified.

**D94b2RAMboot issued successfully per explicituserrequest:** userperformed
unixnuflash independently (don'tclaim readbackverified). Parent noflashcommands.
tcfix-d94.py exactfreshinputhash adapts RELEASETrustCacheplacement only: output
ac8f628panicpad->b2d5000zeroCAVE,restoreslength/extradata thenjoinsac8f150recording
trustcachebase/size;20changedbytes,lengthsame,no signaturepolicy bypass.
PAGE_SHIFTaudit verified actual nonSPTMentrypath unconditionallystores12 at
b27fa90..fa9c into7de4120;11retainedPAGE_SHIFTreadersalreadybound,AMFIstackshift
independent. DO NOT applyold15pageshift binarypatch.
Repackedb2 image50524160bytes SHA6df5c3d706535a0e1ce6c81b2356d36cbf224992f6ebe64548c876e19511baee,
pathstaging/amethyst-d94-b2/Mu-amethyst-d94-b2.img. Unpacked shippedkernel matches
tcfixcandidate08e1db01...,Data/trust/vroot/ADTcmpPASS,firmwarecmpPASS,packshim
geometryPASS,WindowscopySHAmatch. Noactivefastbootflashprocess,device42fe0af2
fastboot,is-userspace=no. Windowsfastboot.exe bootC:\...\Mu-amethyst-d94-b2.img
Sending49340KBOKAY1.198s,BootingOKAY0.311s,total1.528s. This confirms RAMhandoff,
NOT userspace/fullboot. Await runtime result/recoverylog;loggingenabled,persist0.
Fiveprivateimports remainstubs. No old15diagnosticpatches copied blindly.

**User requests D94bootcontainer while their fastbootflash runs: hostb1built.**
No devicecommands/reboot/flashinteraction. Existing owneruniquechipid recovered
from old shippedADT locally (notprinted); freshschema2D94ADT generated from
amethysthardware/profile withfilesystemsnode,ephemeralData/rootUUID/trust/vrootmaps.
Used verifiedData-enabledkernel555f1c52...,newseedb981e18d...,originalD94three-module
trust873ce430...,ownerpolicybundle01a9287c... and existing testedshim/GMUPool
firmwaredb3c75734. FATtemplate old180 geometry ONLY,all donorfiles replaced.
staging/amethyst-d94-b1/Mu-amethyst-d94-b1.img size50524160 SHA
499474ddc0c2be33b6baa3f4a46a8532406ffc234cc17774596fd0022db70871.
Packedimage unpacked,fatextracted,allfourinputfiles+ADTcmpexact;ADTparser validates
iPhone17,2/rootUUID/ephemeral/DoNotUseSEP=1/maps,arguments persistence0/autoreset0.
NOold15binarypatchscripts applied. Need freshnative PAGE_SHIFT_CONST init and
trustcacheplacement guards audited before recommendingboot;old15MANDATORY
postlinkgates cannot just be copied byaddresses. Fiveprivateimports unresolved.
b1is EXPERIMENTAL PACKAGING CANDIDATE not hardwarequalified/bootreadyclaim.
Userflashinprogress separately; don't interrogatephoneuntiltheyfinish.

**Real D94rootcontainer assembled+fullyreadback PASS:** hostassembler exit0;
path .scratch/amethyst-d94ap-stage/root-export/assembled/userdata/userdata-root.img
size16375611392bytes,SHA61f1af1110f91817ac28bba344fe6c64f46442706a0b9f90776f097823c2f29b.
RawOS/SystemOS hashes match profile; VROOTtableslices+signedheaderdigests match.
Full assembled range readback PASS:OS1048576/10223616000,SystemOS10224664576/
6023020544,OS.vroot16247685120/79872000,SystemOS.vroot16328425472/47054848.
HeaderSHNGROOTv2/count4/imagebytes/hashPASS,1MiBalignment/4MiBguard+1GiBtail
clearance under recorded128GiBcapacityPASS. Final userdata-root.json removes
generic core flash_command,explicit targetunixnu,device_write_authorized=false,
live_geometry_verified=false,host_only=true. Core builder printed legacy
`fastboot flash userdata` in log: NEVER EXECUTED/NOT applicable to splitdevice.
All Apple-derived files remain ignoredhostscratch. No phoneops/partitionwrites.
Next create matching bootpayload/ADT with real owneridentity and explicit D94trust/
Data/policies;five privateABI unresolved. Phoneavailability/currenttargetbounds
check needed before any separatelyauthorized boundedrootdeployment;backupwaived.

**Real D94hostassembly active:** decrypt/extract completedexit0. Started
assemble-d94-roots.py source root-export/decrypted,out root-export/assembled,
log root-export/assembly.log; hashes rawinputs+signedtables first,then existing
SHNGROOTbuilder+fullrange readback,reservechecks. Backgroundcompletionresumes,
do not poll/duplicate. No imagehash/result claimeduntilfinish,no deviceops.

**Rootdownload complete/decrypt extraction active:** encrypted artifact now
.scratch/amethyst-d94ap-stage/root-export/encrypted/d94-root-inputs.tar.gz.enc
(~11GiB),426GiBdiskfree. Streaming openssldecrypt->tar extraction on disk
root-export/decrypted inbackground; both subprocess exitcodes checked,secret
stdinonly/no output. Completion resumes rawhash verification+hostassembly via
/tmp/opencode/aston-audit/assemble-d94-roots.py source=decrypted output=NEWdiskdir.
No deviceoperations. Do not duplicate extraction or poll active shell.

**Rootdownload restarted onto disk to avoid tmpfs exhaustion:** artifact14012539380
size11407319350bytes,nonexpired. gh download stagesZIP inOS TMPDIR beforeextract;
original process55091 had /tmp/gh-artifact.808497126.zip open, /tmp14/24GiBused,
outputnotyetcreated. Parent TERM'd only own download process and restarted SAME
artifact with TMPDIR=.scratch/amethyst-d94ap-stage/root-export/tmp and output
root-export/encrypted on436GiBfree disk. New backgroundcompletion willresume;
noCIrerun/deviceops. Avoid polling. Nextdecrypt/extract to root-export/decrypted
on disk,run existing hostassembler on actualverifiedrawroots.

**Rootexport43369226652SUCCESS/download active:** all rawprepare/Csignature+
blockverify/encrypt/upload stepsSUCCESS,keycleanupSUCCESS. Encrypted artifact
download running background to/tmp/opencode/aston-audit/roots43369226652;
completion notification will resume. Avoid polling. /tmp is24GiBtmpfs(16GiBfree),
so DECRYPT/EXTRACT/ASSEMBLE must use .scratch on436GiBfree disk,not tmpfs.
Host-only assembler /tmp/opencode/aston-audit/assemble-d94-roots.py prepared:
verifies rawsize/SHA,verifiedpolicytable digests,uses existing userdata.build,
checks exact16375611392layout/reserve,readsback every range,removes wronggeneric
flash_command fromreport. py_compilePASS; realassembly notexecuted untildownload.
No deviceops/rootwrite/repartition,backup waived asuser directed.

**Host assembly regression while rootexport watcher active:** tmp
/tmp/opencode/aston-audit/test-root-assembly.py uses EXISTING userdata.build with
synthetic local rawwindows and manifesttable objectskip544. PASS v2header/hash,
readbackwindowbytes/hashes,table-onlyobject,1MiBalignment/reserve arithmetic.
Initial mock lackedremote attribute,errorfixed testonly; finalexit0. Builderprints
generic `fastboot flash userdata` suggestion: NOT executed and NOT valid for split
amethyst; ignore/remove from any deployment plan. No realdonor image assembledyet,
no sourcechanges/deviceops/CIpoll;43369226652watcher still provides completion.

**Root export transport failure/new43369226652:**43367837301 failed in IPSW
RangeFile block download,URLError Errno8DNSnodename not known;didn't reachsigning/
export;ownerkeycleanupSUCCESS. No identity/hash failure,unchanged code. NEWrun
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43369226652
donorD94/export_root_inputs=true,watcheractive. Nextdownload/decryptverified raw
rootinputs then hostSHNGROOTassembly. No deviceoperations/repartition/backupgate.

**User correction/docs read: existing unixnu container,no repartition/backup gate.**
Read live unixnu.com/porting/dual-system-partition and /concepts/userdata + local
dual_boot_system/vendor/unixnu-dual_sys/{gpt,partition}.py and coreuserdatabuilder.
unixnu is GPTpartition holding SHNGROOT windows (super-like concept,NOT Android
super dynamic format). Partitioner does NOT format/install; current partition
already exists; don't rerun splitter. User explicitly waivesunixnu backup; drop
backup requirement, preserve bounded target checks/logtail/Android safety. No phone
available, no deviceops. Proceed hostrootassembly not repetitive approval prompts.
ca8bf8b adds optional encrypted verifiedrawroot export to existing policyworkflow,
after full image/hash/Cblockverification; no deploymentcommands. YAMLordering/
encryptionchecksPASS. New D94run with export_root_inputs=true:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43367837301
watcheractive. Download/decrypt new artifact oncecomplete,verify rawpins,assemble
SHNGROOT using existing builder with correcttable544skip;host-only. Root export
doesn't changeuserdata/GPT/phone. Backup waived doesn't silently enableflash.

**Bounded D94root storage proposal audited host-only:** detailed
/tmp/opencode/aston-audit/d94-root-storage-plan.md. Existing host builder layout:
OSoffset1048576size10223616000;SystemOSoffset10224664576size6023020544;
OS.vrootoffset16247685120size79872000;SystemOS.vrootoffset16328425472size47054848;
imageBytes16375611392(15.251GiB). vroot objects are tablesONLY,full signed
manifest544byte envelope excluded. Slice digests match signed headerPASS.
Fits RECORDED128GiBunixnu33554432x4KiB blocks with4MiBguard and FULL1GiBtail
reserve. No GPT change necessary. Arithmetic independently rerunPASS.
NOT deployment qualified: currentGPT/MBR/manifest/objectinventory/backups not
reread,rawD94rootbytes absenthost. board userdatafirst/lastLBA still historical
unsplit bounds,unsafe as deployment target; generic builder flash suggestions
must NOT be used. NewOSoverlaps oldSystemOS so cannot swap individual windows;
roots+tables+manifest one replacement/rollback unit. Full region/tail backup and
fresh geometry verification required before explicit boundedunixnu write approval.
Persistent root replacement NOT authorized. No phone/read/write/CIcommands this
iteration. Further hardware migration blocked on storage scope and private5ABI.

**D94 ADT ephemeral SEP contract repaired e5e2f63:** source/binary audit
d94-sep-adt.md verifies D94APFS still checks /filesystems/DoNotUseSEP presence
before rejecting unencryptedData. Schema2generator only allowed standardplatform
nodes and omittedfilesystems. D94prep now extends existing typed-node allowlist
one node;profile[adt.filesystems.u32]DoNotUseSEP=1. No SEPsecurity/encryption claim.
Hardware DTS compiled via dtc, temporary core/board setup then generated ADT with
TEST seed1 and data/trust/rootpolicyflags; parser checks exact1u32,ephemeral flag,
donorOSUUID,iPhone17,2identity,complete tree extent PASS. TEST ADT not shipped or
owneridentity; no secret requested/used. Selection regression/diffcheckPASS,
3targetedfiles committed/pushed. No kernelrebuild required (ADT-only),noCI/phoneops.
Future persistentData must NOT inherit this ephemeral assumption without separate
encryption/SEP/backend qualification. Rootstorage/private5imports remain blockers.

**D94 consistent host input set staged, NOT boot-ready:**
.scratch/amethyst-d94ap-stage/qualified-inputs contains independently verified
Data-enabled43357450810kernel,donor2GiBseed,newUUID,original3trust modules bundled
with existing bundle-ipsw-trust.py (95728bytes,entries3821/130/34,unchangedbytes),
1152byte VROOTB01 two signed544byte owner-policy headers,inputs.json filehashes/
product/class/build/blockers. No owner/plugin trust hashes invented. Full root
tables/image blocks are NOT in header bundle. Donor root image storage still not
authorized/present onphone; five privateimports unresolved; ADT/image not built.
This is reproducible host staging,not authenticated runtime root/mount evidence.
No newCI/phoneoperations,stagedApple assets excludedgit.

**D94Data-enabled native/reference CI43357450810SUCCESS:** artifact downloaded/
decrypted locally, candidate SHA555f1c5219b45e0551a12e2d475ebeccf13d6babf8098dfe36e9c41ca4dc2150
verified. Native26169cf6fe75ae580e33214d56fefff63e4f2825867e97ba3100e9fb9e34e4c2,
referenceb384635adbbab1daee07b912134d7b7a769e09b806d5aa339f0aba5709241d74
match exact map/report hashes. inputs.json proves XNU_OWNER_DATA_MOUNT=1 and UUID
9EE3A4D1-60A0-49AD-9D49-B960370FF230; candidate contains UUID, native ownerData
symbols present. missing{},5stubs unchanged, Sandbox/AMFI{}both. Verified newRAM
seed hashes/UUID in prior section. No payload/phoneboot yet; rootUFS mismatch and
five private interface backends remain hardware readiness gates. CI baseline now
complete native/reference+ownerData compilation; don't request duplicate build.

**D94Data43354434582SUCCESS independently qualified/native rebuild:** encrypted
seed downloaded/decrypted locally, packed SHA b981e18d13b4cc8fb63dc58169bc54bf9291fb37d14760e253d2d80025bff5d2,
logical SHA0263cb3ad8403bbd148074969cad5370289940691e25bd9486d19b86e578bf4d,
logical2147483648bytes/encoded1597632. Product17,2/build24A446,source launchd
c640246d... (same across donors measured,not assumed). UUID
9EE3A4D1-60A0-49AD-9D49-B960370FF230. SixAPSBcopies inspected,newestxid15role40;
historicalxid2role0 remains (first overlystrict all-checkpoints-role40 assertion
failed; active/newest check passes,not corrupt-current-volume evidence).
Packed+full sparse logical hash checks PASS. Updated only ios27_data_defconfig
mount y/NEWUUID,defaultamethyst config remains ownerData off. Commitfdec5fe pushed,
selection regression/diffPASS. PersistentData remains0,no phone ops.
NEWARMmacos26/Xcode26.6 kernel-only build with ios27_data_defconfig:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43357450810
watcher active. Next verify actual compiled DataUUID/mount and closure/current
candidate. Rootstorage D94replacement still separate authorization blocker.

**D94 seed creator minimum fixed/new43354434582:**43353062898 downloaded/decrypted
donor OS, failed make-data-seed.py exit2 `--gib must be in3..1024`; direct2GiB
request was parent error. c98e411 creates3GiB then discovers APFS Data UUID from
packed APSB, invokes existing set-ram-data-role.py (diskutil assignsData,shrinks
container2GiB,verifiesNXSBbounds before truncation,repack), asserts final logical
2GiB/roleData/UUID before outputreport. No replacing installation UUID.
Selection regression/YAML/embeddedPython/diffchecksPASS. NEWseedrun
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43354434582
watcher active. User ASKED whether persistentData fine, did not authorize writes:
explained technically possible but needs reserved-region/recovery plan, does not
solve D94 root mismatch. Keep persistence0,no phone ops. Wait seed qualification
before newDataUUID/native config. Creation3GiB is temporary host input, final2GiB.

**User requests all iOS27 inputs move17,2 / canonical D94 commit94d6622:** active
kernel/root-policy workflows only D94 (including push fallback), vendored profile
and exact pinned original fixture now board-owned; prep offline installs these,
quarantines old bad entries then applies reviewed additions181func/21data.
Canonical profile includes chained fixups; disposable real defconfig resolution
PASS. New test-d94-selection.pyPASS identities/rootUUIDs/fixturehash/workflow
defaults/oldData disabled/2GiBseed; sanitizer regressionPASS. Historical15binary
scripts/images unchanged, iPad17fallback retained. Board adds explicit D94 mapping;
prep still transforms disposable board fragment/root/policies/reference/series so
fallback untouched. No global rewrite of historical addresses or verification.
Data workflow now loads D94profile, verifies full sourceOSimage hash, records new
launchd provenance,creates2GiBseed and D94specific artifact. Role workflow no old
run/hash/UUID pins: explicit inputs required and product validated. Old15Data mount
disabled UUID0until donor role/UUID qualification. New seed dispatch:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43353062898
watcher active. Next verify seed metadata, role40/exact2GiB, set new Data UUID
and build matching native before packaging. RootUFS replacement still NOT
authorized; no phone ops/flash/oldData relabel. Five private imports unresolved.

**D94 userspace migration additionally BLOCKED by root storage scope:** independent
d94-payload-readiness.md verifies candidatef15a6079...,owner policy headers/key,
actual native SourceStorage/UFS routing. Existing phone OS/SystemOS are15,4; D94
roots require distinct raw10,223,616,000+6,023,020,544bytes=15.13GiB, exceed8GiB
DRAM/4.75GiBowned memory, no existing compressed-RAM-root route. Kernel+RAMData
alone cannot migrate donor userspace. Do NOT boot against15roots, rename UUIDs,
or write replacement roots under current RAM-only/log-tail-only permission.
D94 root image bytes not staged locally (remote signing verified then discarded);
Data previous2GiBseed explicitly15,4, fresh native owner Data mount disabled/UUID0.
Need separate donor seed audit+owner Data config/build and an authorized storage
plan before genuine D94userspace boot. Host preparation possible, but no safe
hardware migration completion under current storage scope.

d94-early-reachability.md: bounded static/module init search found no immediate
last5stub calls; Image4matching personality absent actualcandidate,ARMPlatform
personalities remain. Not proof unreachable. Image4nonce34call is UNGATED once
service start obtains digest, stub PANICS (not unsupported return). Native actual
code_signing_configuration genuinely reports monitor0/config7c000000, not stub;
gates objectload operations but not nonce init. ARM RPC target property runtime
absence unproven. No adapter/grant/reachability assumption approved. No phone ops.

**Last5imports audited: external private ABI gate, no safe fixture shortcuts.**
last-platform-rpc.md + platform-rpc-backend.md identify b2ab224 as IOService
dictionary platform-function IORPC IDbf66f4287c60ef85 (OSString,bool,inputdict,
outputdict**,optionalmethod); ARMPlatform name callAppleARMFunction, gated on
AppleARMFunctionDextTarget==kOSBooleanTrue. Found upstream CallPlatformFunction
declaration but unsupported-only backend; real IOUserPlatformFunctionHandlerGet
provider registration/lifetime and context layout not implemented in native.
Legacy raw callPlatformFunction/CreatePMAssertion incompatible. Reachability not
fully disproved: leave unresolved, no success stub.
last-image4-monitor.md + txm-public-source-search.md establish selectors34nonce
digest,35roll,36supplemental/Diavlo root,37localpolicy with distinct status namespace
and transfer ownership. Existing xnu_image4 local APIs duplicate local mutations,
do NOT satisfy newer monitor state; ENOTSUP namespace equivalence unverified.
Public Apple/UniXNU/Aston/Ishtar/Sheng sources/headers searched exact pins, no
qualified matching backend. Need matching private ABI/backend or symbol-bearing
donor to finish truthfully; do not call allow-missing candidate full-prelink/boot
compatible. Reports and runnable evidence under /tmp/opencode/aston-audit.
Last verified candidate remains f15a6079... zero missing data,5stubs. No new
source changes/CI/phone ops this iteration. Blocked on private interface/backend
evidence, not user hardware photographs or passphrase.

**Remaining import batch reduces13to5 (offline no new CI/boot):** independent
remaining-platform-stubs.md + remaining-image4-gpu-stubs.md under tmp aston-audit
support8identities, exact bodyhash/start/extent/source/caller checks PASS:
ad3d814 ml_get_wake_timebase;abff7fc sched_enable_acc_rail;abffae8 disable;
add41bc vnode_clearmountedon;ad449d0 ml_stack_remaining;ac26044
thread_set_allocation_name;aca1d30 vm_map_page_size(native-owned maps required);
b4182c8 kernel_image4_storage_data(real backing storage,not signature grant).
Saved evidence in scripts/d94-reviewed-additions.json,commitac5411d pushed.
Full fresh rematch+emit exit0,89522176bytes,missing{},5stubbed imports:
ARMPlatformb2ab224 RPC unknown (CreatePMAssertion guess rejected:arg/replyID
disagreement),Image4b11beac/b11bf24/b11bf90/b11bfb0 TXM selectors34..37 no native
equivalence proven. Do NOT stub-success or guess corresponding monitor wrappers.
Candidate .scratch/amethyst-d94ap-stage/d94-reviewed-final.macho SHA
f15a60799d80737b77cd373cc231a1fc522adfb488ab822830a4766f6ba20898 verified.
Sandbox/AMFI/APFS/PMGR/IOGPU no remaining stubs in closure. Still allow-missing,
runtime pending. Next determine unsupported monitor/RPC reachability and matching
native ABI or explicit correct platform implementation; donor root/Data packaging
not yet migrated. No device operations/no boot compatibility claim.

**ARM43317687721 SUCCESS; rebuilt ABI verified; zero missing DATA/Sandbox stubs:**
Decrypted via secure file input (never record passphrase; root AGENTS contains
user-added secret, do not copy into git/logs). Original CI candidate654aeb73...
checker PASS; native636f67990bff10a1a13cccc826dc8e7eb79595c09bf766c63979b1e7d7bc1556,
LTOref1a2db227a9f3b5034362619a1da5b09ec580274ed1a99065c0ffa4500cfa53fd.
Read-only independent fresh audits fresh-sanitizer-43317687721.md and
fresh-panic-hook-43317687721.md under tmp aston-audit: native/LTO getter byte+688
(not donor6a8) retained, create init/fork inheritance/exec entitlement recompute,
fixedc8spawn decode/error behavior/apple-string transport verified compiled.
Valid nonzero security requests still ENOTSUP: bounded ABI, not full syscall parity.
Hook writable RW/RW zero-fill native/LTO, runtime NULL guard+IA/noaddress0x2abe
reader, LTO inlined paths verified; original writer/CAS/previous-hook chain agree.

Added two reviewed fixture bindings ac11bd8->_unixnu_task_get_sanitizer_config and
7044478->_PE_arm_debug_panic_hook ONLY after checks. Full fresh match+emission exit0,
89522176bytes,missing{},13stubbed owner/imports, Sandbox/AMFI both missing/stubbed{}.
Candidate .scratch/amethyst-d94ap-stage/d94-qualified-source.macho SHA
ebe17522f13a18c9bf5fc66234333d2965f36cae8be3aa63dbc2556196fc7a46 verified.
Emitted Sandbox slot83c23b8->ab99aa4 exact native getter bytes, Watchdog7f0dec0->
b760020 zero writable-hook target verified; final symbol table relocated layout
not directly exposed by top-level Image.symbols (use native symbols+byte evidence).
Commit0bf02be pushed. Remaining13imports: ARMPlatform2,PMGR2,APFS1,Crypto1,IOGPU2,
AppleImage4 1,Image4 4. No CI redispatch needed for fixture-only offline emission.
No packed image/phone ops/runtime compatibility claim. Next audit13remaining
imports and donor payload/root/Data consistency before any hardware qualification.

**Serial retry43317687721 active:**43315173629 job131521186230 cancelled before
steps; exact annotations runner not acquired/ARM64capacity. ONEnew identical ARM
run per standing request: https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43317687721
watcher active,samef5fa2a6,policies43184772128,kernel-only. No compilation/phone ops.

**Serial retry43315173629 active:**43312687616 job131512579599 cancelled before
steps; annotations explicitly hosted runner not acquired/ARM64capacity. Per user
standing instruction dispatched ONEnew identical ARMmacos26/Xcode26.6kernel-only
run, policies43184772128,headf5fa2a6:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43315173629
watcher active. Continue inspect/serial dispatch on capacity, diagnose real failures.
No phone ops/no new build result.

**Standing user direction: retry ARM CI serially until it works.**43310079456
capacity-cancelled before steps, annotation confirmed. User explicitly requests
continued repeated attempts. NEW run43312687616 same featuref5fa2a6,
ARMmacos26/Xcode26.6,verified policies43184772128,nonkernel outputs false:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43312687616
watcher active. On completion inspect exact failure; if hosted capacity dispatch
ONEnew run, never parallel duplicates. If actual compile/prelink failure diagnose
before retry. On success inspect artifact bytes/codegen/closure. No phone boots.

**User requested continue/new ARM attempt43310079456:** confirmed verified D94
policy43184772128 artifact nonexpired; feature HEADf5fa2a6676d1599b265427e6dc73f658eff54acb.
New kernel-only macos26ARM/Xcode26.6dispatch (no rerun/host switch):
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43310079456
watcher active. Re-ran sanitizer65539/1024+BSDAPI visibility checks PASS, root CDN
retry regression PASS,diffcheckPASS; these do not substitute fresh compile/codegen.
No device operations. Await completion notification, do not duplicate active run.

**BLOCKED hosted ARM capacity, no active build:**43297524111 again cancelled
before any steps. Check-run131459702854 explicitly says runner not acquired after
multiple attempts, macOSarm64capacity notices. No compiler failure/no artifacts.
Stop immediate redispatch cycle: repeated identical requests do not advance source
validation. No further run launched. User explicitly requires ARM Mac; do not
silently switch Intel/toolchain/paid runner. Need hosted capacity recovery or user
authorization/access to another ARM Mac runner before fresh compilation. Latest
featuref5fa2a6 includes b371671 sanitizer API fix; no kernel build with sanitizer/
panic hook has completed. Last verified experimental candidate remains
d94-reviewed-candidate SHA16ed105d...,not runtime qualified. No phone operations.

**ARM capacity failure43294910554 verified; new43297524111:** job131450520628
cancelled before steps, annotation `job was not acquired by Runner of type hosted
even after multiple attempts` + ARM64capacity notices; no compilation. Policy
artifact43184772128still available/nonexpired. NEW dispatch on requested ARM
macos26/Xcode26.6,samef5fa2a6/source/policy,all nonkernel outputs false:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43297524111
watcher active. Do not change kernel code to address capacity or claim source
failure. No device ops; codegen/native/reference gates remain pending.

**User explicitly wants ARM host; Intel failure diagnosed:** restored macos-26
in commitf5fa2a6, new ARM run43294910554 at that head, same policies43184772128;
watcher active. Parent initially saw Intel43284074560failure but failed to inspect
before dispatch; acknowledged to user. Subsequent exact check-run annotation
131412693143: `The hosted runner lost communication with the server...`.
Intel cross-target probe, donor setup/policies/defconfig/cache all SUCCESS; Build
kernel step started but has no completed timestamp, subsequent steps pending.
No artifact. gh --log-failed reports log not found; direct job-log endpoint404
BlobNotFound. No compiler diagnostic available, cannot claim source failure or
assert OOM/CPU cause from generic runner message. No further duplicate dispatch.
ARM latest source includes corrected BSD sanitizer APIs/panic-hook patch and
test guards7af9d31; native/emit/codegen validation still pending. No phone ops.

**Repeated hosted capacity gate / same-Xcode Intel host:**43281267667 cancelled
before steps, annotations hosted runner not acquired + macOSarm64capacity. Policies
43184772128 still nonexpired. Rather than another identical queue attempt, checked
actions/runner-images current README: macos-26-intel is x64, Xcode26.6build17F113
same as arm64 pool. Commit76b063c switches ONLY D94 workflow to that host; default
15runner unchanged. Explicit xcodebuild/clang version and arm64e-ios27 object
cross-target probe before build. YAML selection assertions/diffcheckPASS.
NEW latest-source build including corrected sanitizer/panic hook:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43284074560
watcher active. No compiler result yet. Host architecture change NOT kernel target
change; target remains arm64e, release, Xcode26.6. No phone ops.

**Sanitizer BSD/Mach boundary corrected before CI compile:**43277634618 again
cancelled by hosted-runner capacity (check annotation confirms,no steps). New
43280328185 atfc2deea requested, then cancelled intentionally after independent
source reviewer found real compile blocker: task.h macro/prototype inside
MACH_KERNEL_PRIVATE invisible BSD, direct task field dereference invalid opaque
task_t. Fixed inb371671: macro/getter/setter declarations KERNEL_PRIVATE section,
task-side setter writes private field, BSD exec calls setter. Storage remains
Mach-private. Exact patch dry-run fuzz0PASS, declaration/opaque access assertions
PASS,65539/1024sanitizer model checks PASS,diff checkPASS. Report
/tmp/opencode/aston-audit/source-review-fc2deea.md records prior diagnostics,
not a real full build. Pushed targeted source patch; NEW run latestb371671:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43281267667
watcher active, same verified policies43184772128, kernel-only. Do not duplicate
dispatch or infer capacity cancelled job as compiler failure. No phone ops.

**Runner capacity failure/new43277634618; sanitizer source prepared:**43274861303
never acquired hosted runner, job cancelled/no logs/artifacts. Check-run annotation:
`The job was not acquired by Runner of type hosted even after multiple attempts`,
macOSarm64capacity notices. NEW dispatch43277634618 samebbd7518 inputs/policy
43184772128, watcher active. No source failure inferred.
While waiting agent completed bounded sanitizer proposal: D94-only fixedc8spawn
prefix conversion, secword invalid mask=>EINVAL/valid nonzero unsupported=>ENOTSUP,
cached task byte init/fork inheritance/exec recompute, ASAN2/TSAN4 plus preserved
opaque8, apple-string error/count handling, used/noinline private-export getter.
Source patch applied fuzz0 in copy; runnable scripts/test-d94-sanitizer.py PASS
65539security words+1024sanitizer cases, NOT kernel execution. Commitfc2deea
pushed source/prep/test only; normal15profile unchanged; no fixture getter binding.
Current running43277634618 headbbd7518 DOES NOT contain sanitizer patch. First
inspect panic-hook rebuilt storage/reader, then dispatch fresh latestfc2deea run
for actual C/layout/LTO sanitizer checks. Neither source probe proves hardware.
Native-layout spawn callers unsupported in D94-only kernel; no attr_size heuristic.
No phone ops, no sanitizer getter grant/boolean substitution.

**Sanitizer identity resolved / authenticated RELEASE hook source rebuild:**
Read-only agents task-bitmask-resolution.md + sanitizer-spawn-resolution.md prove
ac11bd8 returns sanitizer_config byte; ASan entitlement security.developer-tools.
address-sanitizer OR2, thread-sanitizer OR4; spawn raw prefix byteb0 &0xe includes
opaque bit8, apple string sanitizer_config=0x%x. Donor imgp+2e0 IS ip_px_sa,
fixedc8byte spawnattr copyin; nativec0prefix mismatches. Earlier alleged task writer
b298488 is actually per-CPU storage: retract task provenance from offset-only match.
No complete sanitizer port yet; must implement task init/inherit/exec recompute,
spawnattr translation/security validation and apple-string transport; no zero stub
or boolean mapping accepted. Existing sheng translation silently omits sanitizer
and truncates secflags, not a complete safe port. Reports/checks under tmp aston-audit.

Panic hook patch integrated D94-only via prepare script: writable RELEASE NULL
storage+declaration with explicit __ptrauth(IA,noaddress,0x2abe), native reader no
longer const-folds away. Development tracing stays gated. Isolated clang probe
and exact source patch dry-run PASS; full rebuild/PAC reader/storage inspection
required BEFORE fixture binding, which deliberately remains absent. Commitbbd7518
pushed feature; new kernel-only run including65c91ba reviewed additions:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43274861303
watcher active. No phone ops, no runtime success claim.

**Nov13 decrypted fresh build verified + reviewed local emission:** secure file
passphrase received, artifact43192925885 decrypted to
/tmp/opencode/aston-audit/build43192925885/decrypted. Do not record secret.
Candidate checker PASS bytes/hash/exact inputs/reference/signature/slide/quarantine;
fresh native SHAa3c0ab0db7d8db160c054a15d926753408bc74c3361aba1d7da131660abd0ea8,
reference308255db7d4af7e64f3057e20bb789941c2a5592547a54ace8587c81c25a32ca,
PMGR object audit177/439PASS. Two read-only agents resolved Sandbox audio_active
originalb739538 and IOHID kext_assertions_enable b72fce8 (source/consumer/assert
metadata), thread_tid ac2534c (NULL-safe64-bit thread_id getter, writer/consumer
evidence). Reports d94-remaining-data.md,d94-ac2534c.md under tmp aston-audit.
Added reviewed symbol entries scripts/d94-reviewed-additions.json, applied by
CI prep. Full fresh rematch+emit offline exit0:89522176bytes,1unresolved data
(Watchdog7044478),14stubbed owner/function imports; AMFI none, Sandbox data none,
only deliberately quarantined ac11bd8 task bitmask remains stubbed. Candidate
.scratch/amethyst-d94ap-stage/d94-reviewed-candidate.macho SHA
16ed105dbcb9acd159a26caa3eaa815dfaf7fb593066b7130dbbeeddfad1b4ed verified.
Commit65c91ba pushed. This is allow-missing experimental NOT boot-qualified.
Watchdog target identified PE_arm_debug_panic_hook but RELEASE native constNULL
vs retained CAS installs callback: do NOT bind to read-only storage. Task getter
bitmask still needs native semantic support. No phone operations. Next resolve
those two ABI gates; remaining non-Sandbox stub imports require audit before boot.

**Fresh D94 kernel/reference + emission SUCCESS run43192925885:** API conclusion
success. CI prelink summary candidate_written=True, SHA256
`8d1a1e70fa5dc40a5d42f7366947a5681fdd666708b340b416d954b55afdb518`.
3missing owner/targets/references,19stubbed owner/function imports. AMFI missing{}
stubbed{}; Sandbox missing{b739538:1},stubbed{ac11bd8:1,ac2534c:1}.
ac11bd8 deliberately quarantined multi-bit task-state getter, NOT safe to substitute
boolean task_is_privileged. --allow-missing means emission success NOT boot proof.
Full CI log /tmp/opencode/aston-audit/run43192925885.log; encrypted artifact downloaded
to /tmp/opencode/aston-audit/build43192925885/ios27-build.tar.gz.enc.
Local byte-level/native-reference/object verification blocked by owner artifact
passphrase: searched narrow local keyfile paths none, secure request_secret file
card timed out. Do NOT guess passphrase or weaken artifact encryption. Next obtain
ARTIFACT_PASSPHRASE via secure card, decrypt to NEW directory, run
/tmp/opencode/aston-audit/check-d94-candidate.py on extracted kernel dir and inspect
fresh PMGR object-audit.json/native/reference records plus exact3/19closure.
No new dispatch needed, no phone ops, no hardware success claim. Active15,4/i180
preserved; migration candidate exists but remains experimental/incomplete binding.

**Fresh native/reference compiled; split-seg config restored/new43192925885:**
43188540569 passed verified donor source storage, all archives,9minute LTO reference
build and final native link. Prelink matched17025/21692functions,1419data,
2725/3052targets; emission exit1 `Native image has no split-segment relocation
information`. No candidate/closure summary. Root cause: D94 donor OS ios.config
omits CONFIG_XNU_KERNEL_CHAINED_FIXUPS (Aston device defconfig supplies it); old
amethyst15OSfragment supplied it. prepare-d94-build now appends y to donor
fragment. Baseline shell forwards flag to XNU, MakeInc.def adds split-seg info.
Actual disposable defconfig resolution exit0 and .config asserted y; diff check
PASS. Commitbcb1073 pushed. NEW kernel-only dispatch same verified policy run:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43192925885
Completion watcher active. No phone ops, no emission success claim.

**D94 owner policies SUCCESS / fresh kernel run43188540569:**policy run43184772128
conclusion success. Downloaded ios27-d94ap-root-policies to
/tmp/opencode/aston-audit/d94-policies-43184772128; independently checked reports
against profile hashes/UUIDs and trusted-key SHA4a3f9012e26d6787454a2aa2184f5b4779cf48701b576275dfdaa5edb53b89e0.
C signature+block verification true; OS2496000blocks/10223616000bytes SHA4b8719fe...,
SystemOS1470464blocks/6023020544bytes SHAf6d10101...; original images unmodified,
host_only=true, hardware_tested=false, boot_root_authenticated=false. No runtime
root claim. New kernel-only/Xcode26.6 dispatch with donor_policy_run43184772128:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43188540569
head featurec530dac, completion watcher active. Next inspect native/reference
compilation and actual prelink/closure results. No device operations.

**Policy CDN timeout/new dispatch43184772128:**43184371909 failed in RangeFile
initial bytes0-0 HTTPS probe, urllib.error.URLError TLS handshake timeout, before
image/signature validation. Commit6067c1a bounds IPSW constructor transport retries
to3attempts; identity/verification errors still fail immediately. Runnable
scripts/test-root-image-connection.py PASS transient retries/fail-fast ValueError/
exhaustion preserving original error (test commitc530dac). Both pushed.
NEW policy workflow run (not rerun):
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43184772128
watcher active. On success NEW kernel dispatch donor_policy_run43184772128; do
not use failed43184371909. No phone operations; no candidate success claimed.

**Policy workflow in progress / kernel reporting prepared:** user requested continue.
Run43184371909 API showed donor prepare, owner-key provision, verifier compilation
SUCCESS; preparing exact raw images in progress, signing/block verify pending.
No restart/duplicate dispatch. While waiting commite0e24e2 adds actions:read for
cross-run policy artifact downloads and always-on D94 prelink record summary:
candidate_written, missing/stubbed owner-target/reference counts, exact Sandbox/AMFI
unresolved targets, kernel SHA. YAML/embedded Python parse and diff checks PASS.
Feature branch pushed. Policy completion watcher still active. After success use
latest feature head and donor_policy_run43184371909 in NEW kernel dispatch.

**D94 PMGR fresh compile passed; donor policies now generating:**43183033987
passed two-pass PMGR/archive then source-storage verifier rejected mismatched old
15,4 OS policy (`require complete verification of the actual24A446 image`,exit2).
No bypass. Commit5a67b89 adds donor selector to existing root-policy workflow,
root image/sign scripts accept qualified17,2profile, retain actual raw hash and C
signature/block verification. Uses existing IOS27_ROOT_OWNER_KEY secret; no key
read locally. Kernel workflow now requires successful donor_policy_run, downloads
ios27-d94ap-root-policies into distinct directory, uses existing verifier unchanged.
YAML/Python parse and diff checks PASS. New owner-policy workflow dispatch:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43184371909
Watcher active. On success dispatch NEW kernel run donor=d94ap,
donor_policy_run=43184371909, xcode26.6, nonkernel outputs false. On failure inspect
diagnostic. No device/persistent storage ops; migration still incomplete.

**D94 PMGR port gate addressed/new run43183033987:**43180633630 compiled bootstrap
XNU and corecrypto/ArmDT/ArmCpu/pthread archives, then PMGR audit correctly refused
D94 vs pinned15,4 hash. Read-only subagent content-rematched D94 lifecycle/vtables,
report /tmp/opencode/aston-audit/pmgr-d94.md and runnable pmgr-verify.py PASS.
Integrated hash-selected D94 pins in scripts/pmgr-d94-pins.json, existing audit and
compiler; unchanged15,4 branch. Original D94 IOPMGR177/ApplePMGR439PAC tuples match
existing qualified ABI, class sizes88/738d8,nub740. Ctor/nub hashes, D2 unique derived
caller and metaclass constructor identity establish addresses, not offset guessing.
Emitter rebase_collection delta=native.base-cache.base: both D94/native7004000,
actual D94 slide0; rejects -1000000 for D94, existing15,4 binding logic unchanged.
Integrated audit cached run7objects PASS177/439; six lifecycle hash checks PASS,
wrong-slide rejection PASS. Fresh CI object checks still required, runtime untested.
Commitd45985b pushed feature branch (3targetedfiles); new kernel-only dispatch:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43183033987
Completion watcher active. No bypass of SHA/PAC checks, no phone operations.

**D94 config resolution fixed/new run43180633630:**43180370582 passed donor
preparation, failed Select device: missing donor configs/ipados.config. Donor
defines configs/ios.config but board's explicit os fragment overrides it. Script
now rewrites that one disposable board reference, preserving original15,4 path.
Actual isolated core+board copy ./unixnu defconfig amethyst_defconfig --os
ios-27.0.1-24A446-d94ap -D COPY resolved successfully exit0 (SM7635 feed fetched);
log /tmp/opencode/d94-ci-resolution/defconfig.log. This supersedes syntax-only
test. Commit95b189d pushed feature branch; new dispatch kernel-only/Xcode26.6:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43180633630
Completion watcher active, no build/emission outcome yet, no device operations.

**D94 CI initial setup failure fixed/new dispatch:**43180097156 failed before
compilation, prepare-d94-build.py FileNotFoundError writing kits/XnuPrelink/fixtures:
core checkout had not initialized that submodule. Commit45ac7e4 on feature branch
adds pinned `git submodule update --init --recursive kits/XnuPrelink` BEFORE fixture
staging; script now refuses missing fileset_match.py before any mutation. Test
uninitialized kit rejected exit1 with no profile writes; initialized copy full
preparation exit0; YAML/diff checks pass. Pushed targeted2files only.
New workflow dispatch (NOT rerun), kernel-only/Xcode26.6:
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43180370582
background watcher automatically resumes on completion. No compile/emission result
yet; active15,4 baseline and unrelated edits preserved, no device operations.

**Fresh D94 macOS CI dispatched:** targeted3file commit
`762376d0d0607195ea33ffa96bb7aba7668f2682` pushed feature branch
`ios27-d94-build-audit`, preserving unrelated local bringup edits. Existing
ios27-kernel workflow now accepts donor=d94ap (default iPhone15,4 unchanged),
rejects D94 payload/firmware/userdata=true, invokes prepare-d94-build.py in
disposable CI checkout. Script selects exact pinned D94 metadata and quarantined
171/18fixture, adds reference producer and stack patch, retains SM7635/RAM-only.
Existing root policies remain15,4: KERNEL AUDIT ONLY, NOT PAYLOAD READY.
Test: script exercised in disposable inputs exit0; YAML parses; git diff --check
passes; stack patch dry-run passes. Dispatch new run (not rerun):
https://github.com/IOS4Amethyst/UniXNU-amethyst/actions/runs/43180097156
head762376d, xcode26.6, amethyst_defconfig, all non-kernel outputs false.
Initial API status queued; no build result yet. Background gh run watch process
reports completion automatically; do not duplicate dispatch/poll. Next inspect
failure diagnostics or successful prelink/closure artifacts; no hardware boot.

**D94 LTO reference gap resolved; native build now actual gate:** read-only agent
downloaded Aston failed-run42832522940 prelink inputs (native4675b16f..., LTO
reference00eb1fea...) and successful279run42839449486 logs. Same staged matcher
version8/signature3b8f1037... plus quarantined171/18fixture gives amethyst native
with donor reference2832/3052named targets (vs1857without); donor native/reference
2835/3052. Successful original unquarantined donor log2836/3052. Reports and maps
/tmp/opencode/aston-audit/reference-build-next.md,current-native-reference.*,
artifact-reference.*. Original fixture bad task bitmask anchor remains quarantined.

Actual emission attempts, no --allow-missing success claimed:
- amethyst cached run7 RELEASE + reference: exit1 missing native symbol
  `_unixnu_kernel_stack_shift` from reviewed AMFI site. No output.
- cached Aston failed-run native+reference AUDIT ONLY: exit1
  `ValueError: Native __DATA overlaps a retained segment`, expected artifact defect.
  Never use Aston hardware-native kernel in an amethyst image.
Staged board ONLY now adds build-fileset-release-reference.py --tree . --lto after
prerequisite archives; native-kernel-stack-shift.patch ports sheng stack.c alignment
and SECURITY_READ_ONLY_LATE shift symbol (not constant guessed in binary).
patch --dry-run exit0 on prepared native source; default16KiB stack+3x4KiB guard
allocation rounds32KiB =>shift15. Other sizes checked; no native compile performed.
Current Linux reference path not established: toolchain linker --disable-lto-support,
reference script lacks Linux target/make setup. Fresh macOS26/Xcode26.6 build needed.
Source active profiles, i180, firmware, persistent storage unchanged; no phone ops.

**D94 migration exact inputs + first fresh match (offline only):** range-fetched
kernelcache, DeviceTree, StaticTrustCache, Cryptex1 SystemTrustCache/AppTrustCache
into isolated `.scratch/amethyst-d94ap-stage/core/out/ipsw/24A446`; kernel SHA
fa07057bf34f33af1ec26681ad0c87c13e5aaa548a300740eddb0e92876e5f95 verified.
No full IPSW/root images, root-policy signing, CI dispatch or phone operations.
Subagent exact donor audit:172/172fixture functions hash/start/extent pass;
data12backed+6zero-fill+4outside kernel (AGXG17P/AppleARMPMU). More importantly,
fixture ac11bd8->_task_is_privileged is semantically WRONG for native boolean:
original returns task+6a8 byte; Sandbox consumers extract bits1/2; original
writers OR2/4. Quarantined that function plus4wrong-entry data records in COPY
only, preserving pinned original under stage/provenance. Working171func/18data.
Runnable stage/review-fixture.py; evidence and verify-d94.py under
/tmp/opencode/aston-audit/d94-fixture-verification.md (copy report if tmp pruned).
Reference cache tests scripts/test-fileset-reference.py pass4tests (mock build,
NOT a real native compilation). Fresh fileset_match against existing run7 RELEASE
kernel SHA a6f7f60a77fbdc570ff71f32344c8ce99d7ea711ea239e44f005f3cb23945d89:
8727/21692functions,1285data,1857/3052kext-facing targets named,176double-bind
conflicts;171reviewed function and18data anchors loaded. Disputed task getter
remains UNBOUND. Map/log stage/d94-release-audit.{map.json,log}. No reference
kernel supplied; existing native compilation is not a newly configured D94 build.
Do NOT compare named-target count directly to emitted stub/closure counts or
claim Aston reported2836/3052 reproduced. Next: replicate/review donor RELEASE-LTO
reference/native source setup, then rematch; retain no-boot gate and original15,4.

**User-approved controlled iPhone17,2 migration: isolated metadata staged.**
Keep active iPhone15,4/i180 baseline. New directory
`.scratch/amethyst-d94ap-stage/{core,device}` snapshots present local source edits,
excludes .git/out/build/cache and contains no symlinks. No session/profile switch.
Distinct profile `ios-27.0.1-24A446-d94ap` extracted from pinned Aston282c1d6
patch (whole added files verified); fixture separately from27973a7,172functions/
22data, SHA377e220a2ab1d7d03265b6049a48dcf0273d6154cf70672e0ea9f7eaa87a07b0.
Critical provenance:282c1d6 does NOT contain279 fixture/workflow wiring; recorded
both pins explicitly in migration.json. Critical cache: IPSW output keyed by build
24A446, NOT product; separate copied core with absent out prevents donor collision.
Copied board root UUID set donor E3F4AED0-4454-4D5B-833C-E27E99C4F67B, new profile
defconfig mapping, separate donor policy directory. SM7635, amethyst hardware,
sheng-data-persist=0/persistent_data=false retained; fallback default unchanged.
Metadata asserts pass. Staging script `/tmp/opencode/aston-audit/stage-d94ap.py`;
source/input audit `/tmp/opencode/aston-audit/migration-inputs.md`; durable stage
README/migration.json preserve next gates. No Apple assets downloaded, native build,
fresh prelink/coverage measurement, policy signing, CI dispatch or phone operations.
Next: audit D94 task getter semantics; resolve RELEASE/reference path; only then
fetch matching donor assets into isolated output and measure full-match coverage.
Do NOT report improved measured coverage before a fresh donor match exists.

**Offline i180 subagent-audited repair batch, NOT booted:** user requested subagents
and fewer hardware iterations. Two read-only general agents audited entitlement
identity and independently reviewed178/179; reports under
`/tmp/opencode/thread-audit/{entitlement-agent.md,binding-review-agent.md}`.
Independent review supports existing7checked slots, no new wrong binding established.
Parent independently disassembled the selected original/native pairs before editing.

RETRACT earlier characterization `a8a7614` as a raw entitlement getter: it is
`_csproc_get_identity(proc) -> const char *`. Original152bytes checks CS_SIGNED,
uses validated csblob, requests slot0/magicFADE0C02 = CodeDirectory, reads BE32
identOffset at+20, returns cd+offset (NULL absent). Native `a78ae78` uses same
contract and CodeDirectory field with native vnode/csblob layouts. NOT an
entitlements blob: no output arguments or errno return. Source kern_cs.c:875/627.
Bound2previously stubbed slots: Sandbox `8174168`, APFS `8184600`.

`a8e0ca4` -> `_proc_csflags` native `a7bb048`: validated(proc,uint64_t*out),
NULL => EINVAL22, otherwise zero-extends proc_ro+0x24 to64-bit out and returns0.
Old return-zero stub left the required output untouched while reporting success.
Sandbox `a14d1fc/a14d2e8` subsequently test output bits; corrected Sandbox
`8174408` and AMFI `7ef0b90`. Do NOT bind scalar proc_getcsflags (different ABI).
`a8e7a40` -> `_proc_pgrpid` native `a7bfc78`: identical12-byte BTI/LDR w0,+36/RET,
upstream24A446 identity supports source ordering; Sandbox slot `8174470` corrected.
Three identities saved in bind.json;12-slot original-hash/target/metadata check.

i180 derives shipped179: exactly5aligned pointer changes (all replaced stub
targets), unchanged upper chain/PAC bits, no executable changes. Packed kernel
extracted/cmp-exact; original-image SHA/function hashes/checker pass. Expanded
checker rejects179 as expected. Image SHA256
`ad995a51fed1bd4a0d02d05d936654893ba0bac855e543e0541c0ba48c85f0be`
`staging/amethyst-ios27-i180/Mu-amethyst-ios27-i180.img`. No device operations.
Static audit establishes broken dependencies, NOT runtime EPERM resolution.

AMFI entitlement credential path already calls native mac_label_get; retained
cred+0x78/PAC0x8b90 matches native mac_cred_label. Runtime label-index globals
zero in offline files are NOT evidence to overwrite them; initialized at runtime.
Remaining candidates: persona_get_type original a8dd998/native a7b8f58 (magic,
locking and type field match; not yet parent-reviewed/applied); task_is_privileged
original a41e0cc cached byte needs writer/source proof before binding to native
uid-based implementation. Full-matcher fixture integration still pending. Old
sandbox-imports.txt is a pre178 inventory: re-decode current shipped slots rather
than trusting its stale target/name columns. No more identity-only hardware probes.

**Offline i179: IOService metaclass + RETRACT i161 proc_getcsflags identity. NOT booted.**
`8174810` -> original `7cc9660` -> object `af5c358` is IOService::metaClass,
not OSSymbol/OSSet. Original constructor `aae5efc`: object formed in x19 at
`aae5f00`, string x1 `70b1394` = IOService, superclass x2 `af5a328` = IORegistryEntry,
size0x88; calls OSMetaClass constructor `aa4ea18` at `aae5f20`.
Upstream iPad24A446 fixture independently records IOService::metaClass pointer
identity (different original addresses). Native pointer `7c70608` -> gMetaClass
`af25e00`. Stale shipped178 pointer actually reads `0x6800000061` from replaced
native DATA_CONST (offline), not a class object. Sandbox checks at `a14168c` and
`a161eb4` use it. Rebound65 untouched data slots across retained kexts.

**i161 claim a8e0db4=proc_getcsflags was WRONG.** Original `a8e0db4` NULL returns
UINT32_MAX, validated proc_ro+0x40 getter; subsequent same-shaped functions
`a8e0e54`/`a8e0ef4` read+0x44/+0x48 = min_sdk/sdk. Native proc_platform/min_sdk/sdk
read+0x38/+0x3c/+0x40; native proc_getcsflags instead reads+0x24, as does the actual
original csflags getter `a8e0c14` (already identified in kernel.map). Sandbox
callers `a14d2a0` compare return to6 (Mac Catalyst), `a13d8a4/a153bd0` to10
(DriverKit): independently establishes OS platform ID, NOT CS flags.
i179 overrides original `a8e0db4` -> `_proc_platform` native `a7bb0dc` at Sandbox
slot `8174488` (was incorrectly a7baff8) and AMFI `7ef0be8` (was stub). Retains
native proc_ro zone5 and backref checks. Added bind.json identity AND
got-override.json so fresh builds and already-bound mistaken slots are corrected.
Do not globally substitute csflags getter for another same-shaped field getter.
i175 direct native proc_getcsflags diagnostic remains a valid csflags observation;
i176 direct native csblob/task platform getters remain valid boolean observations.

i179 built offline from shipped178 using existing patch-got/patch-data, only2function
slots and65data slots changed. Extracted shipped kernel cmp-exact; checked67aligned
pointer-only changes from178 (102cumulative from176), original metadata upper bits
preserved, no executable changes. Expanded check-sandbox-bindings.py passes7slots,
verifies original hashes, deliberately rejects178. Image SHA256 verified:
`7fb67c6defc241d0548c64c67e7a00222d4e8e417960be661adbb1732f0a56c8`
`staging/amethyst-ios27-i179/Mu-amethyst-ios27-i179.img`. No device operations.
Still NOT proof EPERM resolved. Remaining unresolved raw entitlement getter
`a8a7614` has different contract from csblob_get_entitlements: left unbound.
AMFI credential-label path `8f6f43c -> 8f6aa9c -> 8f6e9f4 -> mac_label_get`
already reaches real native getter. Other inspected Sandbox data imports
`7fec298`/`7eec690` belong to retained CoreAnalytics/AMFI, not replaced kernel data.
Full matcher fixture integration and remaining unresolved helper audit pending.

**Offline i178 repair batch (NOT booted; user requests no more probe-only cycles):**
Audited Sandbox imports and evaluator against original iPhone24A446 + native symbols.
Concrete type-identity defect: slot `81747d8` still points to original `7c977a0`,
whose chain points to metaclass object `af5a328`. Constructor `aacb038` passes that
object and literal `70b1247` = IORegistryEntry to OSMetaClass constructor. Native
`__ZN15IORegistryEntry9metaClassE` = `7c70060`. Sandbox loads this pointer at
`a14933c` for its service-object casts (e.g. `a14bb84..a14bb90`); stale original
metaclass cannot identify native IORegistryEntry ancestry. This is a strong plausible
cause of failed IOKit filter matching, NOT hardware proof of the EPERM fix.

Additional reviewed missing dependencies:
- `aa510a8` -> `__ZNK11OSMetaClass13getSuperClassEv` native `a8dde6c`:
  identical12-byte BTI/LDR+16/RET, source OSMetaClass.cpp:1413. Sandbox callsites
  `a14c110` and `a161af4` walk class names/parents; old stub truncates traversal.
- `aa5b290` -> OSDictionary iterateObjects(block), native `a8e60c0`:
  upstream24A446 fixture identity re-matched per iPhone; original172bytes walks
  count+20, stamp+12, signed dictionary+32,16-byte key/value pairs and block+16,
  stops on true/stamp change, returns stamp equality; source OSDictionary.cpp:846.
  Sandbox `a161c88` previously never called block because import was stubbed.
- `aaa59d4` -> `_SHA256_Init` native `a92913c`: same crypto table descriptor+48,
  size guard and authenticated init discriminator0x73d8. Sandbox HMAC code
  `a15951c/a1595b4` initialized zero contexts via stub before real Update/Final.

Updated committed-input bind.json/data.json (no commit performed). Existing patch-got
rebound7slots (iterator4/SHA256_Init2/superclass1); patch-data rebound28 untouched
original IORegistryEntry pointer slots across retained kexts. i178 derives shipped176,
NOT i177: no evaluator SBDEC instrumentation added. Existing denial logs retained.
Whole-kernel diff: exactly35aligned pointer changes, all chain/PAC metadata bits
unchanged, no native or retained executable bytes changed. Packed kernel extracted,
cmp-exact; check-sandbox-bindings.py passes4reviewed Sandbox imports and deliberately
rejects shipped176. It verifies original kernelcache SHA, function-body hashes,
symbol targets and chain metadata. Saved assembly/audit under
`/tmp/opencode/thread-audit/{sandbox-imports.txt,evaluator.txt,sandbox-all.txt}`.
Image SHA-256 `758379bdb55160bd036d8974f75aa0eddea220f16521d4a22b1382f1b4bb0000`:
`staging/amethyst-ios27-i178/Mu-amethyst-ios27-i178.img`. Payload args verified retain
sheng-data-persist=0, sheng-log=1, sheng-no-lc=1. No device commands this iteration.
Remaining: inspect entitlement-object construction and remaining stale data imports;
`8174810` -> original `7cc9660` is NOT yet identified (do not guess OSSymbol/OSSet).
`a8a7614` remains unbound: do not confuse its proc-argument raw entitlement getter
with native csblob_get_entitlements' different arguments/return contract.
Durable full-matcher fixture integration remains pending; local bind/data inputs and
hash-checked runnable check preserve these reviewed identities meanwhile.

**i176 platform identity valid; i177 built, NOT booted:** retained log
`/tmp/opencode/recovery-log-i176/untagged-4984e91f/msgbuf.txt`:38rounds/errors0;
pid24 and pid7 Sandbox errno1, blob-platform1/task-platform1. Prior pid24 tc-ent1.
No evidence for missing platform identity; do not forge flags. Static trace: hook
`a132cd0` calls evaluator `a1489e0` for operation0x44; context callback `a1489cc`
routes to `a1491f0`. Evaluator callback callsites `a148ac8`, `a148b44`, `a148c14`
use original `blraa x8,x24`. `a14eccc` string reference is compared by `_strcmp`
(thunk `a167af4`, slot `81745b0`), NOT an entitlement getter. Entitlement object
path uses helper `a15ac88` → retained thunk `a167054`; identity still to trace.
RULE0: sheng references cover scoped client entitlements, no direct fix found;
XnuPrelink fetched origin/main `440589b`, changes since9b950f2 concern text placement,
macOS and26C5035r stack shift, not this decision path. Pin unchanged.

i177 adds optional `SANDBOX_DECISION=1` to mac-policy-open-log.py, calls each original
evaluator exactly once, preserves x0..x7 results and authenticated calls, logs only
operation0x44: SBDEC pid/raw/caller/profile/context flags. No sleeping inside policy
iteration; existing outer MACOPEN flush delay retained. Input instruction and empty
cave assertions caught/corrected an initial callsite typo before any file write.
Packed kernel extracted and cmp-exact; byte diff against shipped176 confined to
three callsites and zero cave `ab16300..ab16580`. Probe disassembly reviewed.
Image `staging/amethyst-ios27-i177/Mu-amethyst-ios27-i177.img` is NOT hardware-tested.
Preboot pull failed: adb exit1 `no devices/emulators found`; fastboot devices empty.
Phone unavailable: leave image unbooted, pull recovery log when reconnected first.

**i175 entitlement present / i176 actual platform getters:** log
/tmp/opencode/recovery-log-i175/untagged-4ad5cd4f:38rounds/errors0; pid24 Sandbox errno1,
cs22002301, tc-ent1 (private AMFI trust-cache check entitlement present). Do NOT infer missing
platform status from proc csflags: kern_exec intentionally doesn't copy CS_PLATFORM_BINARY
into ip_csflags, native csproc_get_platform_binary reads csblob, task getter separate.
i176 MACOPEN_PLATFORM=1 logs both actual getters instead of prior cs/tc fields, maintains
policy callbacks and errno. Original cache-loader signed entitlements include AMFI client
class and private check entitlement. Packed exact/checksum OK, SHA-256
`4073bef5a3ad2b5fe7e79a362774b923a19f5b474a8bec1766f88c25abf3caf3`.
After saved i175 log RAM boot i176 OKAY. Next recovery actual blob/task platform values;
do not forge platform flags or grant Sandbox opens unconditionally.

**i174 identifies Sandbox denying AMFI open / i175 task identity diagnostic:** recovery
/tmp/opencode/recovery-log-i174/untagged-49ac3116:38rounds/errors0, retained
MACPOLICY pid24 errno1 index2 nameSandbox for AppleMobileFileIntegrityUserClient. pid7 also
Sandbox denial. Original disk launchd_cache_loader entitlements (read-only APFS mount) include
com.apple.security.iokit-user-client-class array with AMFI client and private amfi trust-cache
check=true. Need runtime identity, not unconditional grant. i175 extends post-iteration denial
log with proc_getcsflags and IOTaskHasEntitlement(current task, private trust-cache entitlement),
retaining exact verdict and5s flush delay. Probe source mac-open-log.py MACOPEN_IDENTITY=1;
existing policy probe unchanged. Packed kernel exact/checksum OK, SHA-256
`d1b257ea3b1ab07eadca52dad09a7e2c8458f48d55c7d38020639f50b12b8293`.
Recovery log saved then RAM boot i175 OKAY; next MACOPEN cs/tc-ent values. No MAC bypass.

**i173 retained Sandbox denial; i174 preserves fatal denial tail:** recovery log
/tmp/opencode/recovery-log-i173/untagged-4a16954d 33rounds/errors0, MACPOLICY pid7 index2
name Sandbox, low errno1 for APFSUserClient. Printed 64-bit errno had undefined upper bits;
fixed probe to zero-extend w0. Still no pid24 final denial retained. i174 rebuilds same probes
on i172 kernel, adds optional5s sleep only after mac_iokit_check_open iteration returns a
denial and has released policy busy hold, preserving original errno. Do not sleep inside
policy callback iteration. Packed kernel exact/checksum OK, SHA-256
`f4aba0a240f02488517e330bd17c8df7c9e11f7972912bd324e4ae0532dd02f0`.
After log pull RAM boot i174 OKAY. Next recovery seek pid24 MACPOLICY module and errno;
no policy bypass, no claim APFS pid7 denial equals AMFI pid24 cause.

**i172 real EPERM / i173 per-policy logging:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-6adfe479-e120-4dea-a668-9fee9d0fb104.png`
MACOPEN pid24 errno1 for AMFI userclient. Recovery log
/tmp/opencode/recovery-log-i172/untagged-4b1f9685 33rounds/errors0 also records System Policy
denial pid7 AppleAPFSUserClient errno1. i173 mac-policy-open-log.py instruments both static
and dynamic mac_iokit_check_open policy callback calls (+78/+104), invokes original
authenticated callback once, saves return, logs nonzero verdict with policy name/PID/index,
restores original return for error selection. No policy bypass. Packed kernel exact/checksum
OK; SHA-256 `1b78dc63c850415c15bd9955367eec61b270a52bc74c90890aee9073ee517ffe`.
After log pull, RAM boot i173 OKAY. Next recovery MACPOLICY should identify denying module;
do not assume AMFI itself vs sandbox vs system policy until observed.

**i171 AMFI-userclient failure / i172 diagnostic+cleanup binding boot submitted:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-24a5ac28-bd89-4e26-8147-c5e83352d23d.png`
reports launchd_cache_loader exited, Unable to connect to AMFI, after MACF failure. Recovery
log /tmp/opencode/recovery-log-i171/untagged-49d914cd:33rounds/errors0, stops before fatal
denial. Do not blame nonfatal acceleration warnings. i172 mac-open-log.py hooks IOKit call
a9c4614, calls real mac_iokit_check_open and logs nonzero errno with PID/client/type,
returns exact original result; no policy bypass. Also slot7ef06b8 original ab5fb20 exactly
`bti c; mov x8,-1; str x8,[x0]; ret`, identifies OSMetaClassBase D2 native a8dc26c,
matching source destructor that poisons vtable; cleanup binding fixed (not the policy).
Packed kernel exact/checksum OK, SHA-256
`2557370a8fd53fe88313aaf8d75b8946e9b7399e6a4ec0f212b7627bdd1a48d8`.
After saved i171 log, RAM boot i172 OKAY. Next recovery pull MACOPEN; no security-check bypass.

**i170 sustained workers / i171 setup+wakeup batch boot submitted:** recovery log
`/tmp/opencode/recovery-log-i170/untagged-4c3846db`, uptime374/188 rounds/errors0/GPT1/1,
no retained ownership panic. New worker callers visible, Data mount completed. i171 binds
original a4317e0 -> thread_set_thread_name (thread/non-null name, tid, BSD thread-name call),
a43b8d8 -> thread_policy_set (flavor/count/policy guards match native), a8f9ad0 ->
thread_wakeup (sets args1/2=0, tails to original a405c60 thread_wakeup_prim identity already
reviewed override; native thread_wakeup obtains waitq then wakes all with result0).
Slots8184be0/8184bd8/8185160, review171.json. Packed diff only three slots, chain high bits
preserved, kernel exact/checksum OK; image SHA-256
`9597188393a15b41a7097154f9d9bcfb13ade75741bc462629682854551dea57`.
Pulled before reboot then RAM boot i171 OKAY. Next recovery full log; no photos needed.
Missing wakeups are concrete no-op defects, not yet proven sole cause of earlyboot stall.

**i170 runtime photo progresses past prior ownership stop:**
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-395ab83c-e663-480a-a59d-a72fef582932.png`
shows launchd hello/Darwin Bootstrap, entering on-demand mode, failed to find AppleKeyStore
service, exclaves-boot and fsck tasks, then unresolved calls. No ownership panic visible;
not proof full boot or final endpoint. ADB currently absent. Await recovery log pull before
attributing remaining stall to AKS (message alone insufficient) or another missing binding.

**i169 photo exposed missing wait/block contract; i170 fix boot submitted.** Photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-32c63d41-4310-4b1e-91f6-ea71215e3f18.png`
shows LOCKFAIL caller33a2670, frames3204b38/3204a70/34d65f0, then diagnostic IOSleep
panics "thread already waiting". Caller offset is after noreturn panic BL in native
lck_mtx_unlock_contended, not a function identity (_hw_lck_ptr_init starts at same boundary).
APFS path a208a84: assert_wait -> unlock x19+a28 -> callback block operation -> later
unlock. Block thunk a2bdf44, slot8184ba8 original a407018 was unmapped. Original wrapper
`bti c; mov w2,0; b thread_block_reason` preserves continuation+parameter; native named
thread_block_parameter a3bd29c has same ABI and branches to native thread_block_reason.
Stub returns without consuming waiting state, consistent with ownership failure and probe's
already-waiting panic. Bound slot to0x80110000033b929c on i166 baseline, no probes/delays.
i170 packed kernel differs from i166 only in this slot; metadata preserved/checksum OK.
SHA-256 `64e3bc7ea3e5386cf122992bfd1a0b9d297ce342fc8c7c737b1b45f2de5a5b9e`.
Pulled i169 log (one round, panic not durable) then RAM boot i170 OKAY. Runtime outcome
pending; next recovery log before claiming lock regression fixed. Do not reintroduce
IOSleep in an already-waiting thread or bypass lock ownership checks.

**i169 ownership evidence strategy correction, boot submitted:** static thread_create_internal
contract returns parent task/tasks_threads_lock held; kernel_thread_start releases both then
starts worker, source/disassembly consistent. No proven corrective patch yet. Found third
similar panic VM-entry-unowned (aabb160), absent from prior mutex/ticket probes; exact user
transcription insufficient to choose. Updated lock-owner-log.py covers all three, neutral
arg1 label (VM entry arg1 is lock state not thread). i169 on i166: ownership helpers log then
sleep, plus APFS-scoped pre-create 5s pause before native thread_start prologue takes mutexes.
Original i167/i168 sleeps under held mutexes can impede logger; do not reuse. Entry pause
helper /tmp/opencode/thread-audit/entry-delay.py, bytes/prologue checked. All three original
unlock calls untouched. Packed kernel equals input and checksum passes; SHA-256
`d8c6f1ab887073978b7432d060da9b700b7c071ed2818135e384fa936e8aa791`.
Pulled stale/current i168 window before recovery->bootloader->RAM boot i169 OKAY. Wait30s
then recovery; seek THREADSTART and LOCKFAIL. No photos; no lock assertion bypass.
This remains a diagnostic, not a lock fix; avoid claiming cause from absent durable records.

**i168 recovered 2026-11-12:** Windows adb serial42fe0af2 recovery; pulled to
`/tmp/opencode/recovery-log-i168/untagged-4d32713c`. Fresh boot ID4d32713c,
uptime0/one round/errors0/GPT1/1, ends at t67.464 after network initialization.
No THREADUNLOCK/LOCKFAIL/panic retained. Scoped probe did not supply usable evidence;
do not attribute the ownership failure to any particular unlock from this log. No new
boot sent; phone left recovery. /tmp old build assets cleared again; shipped staging images
and repository probes survive. Recreated Windows adb wrapper at /tmp/opencode/log-adb-bin.
Next inspect static thread-create native call contracts and secure a pre-failure durable
checkpoint without sleeping while creator mutexes are held; do not repeat i167/i168 method.

**i167 no fresh log; i168 probe scope corrected:** recovery pull i167 log.bin exactly
matches i166's (boot4ae81e05), so i167 startup/logging unqualified. Prior unlock probe
delayed all native kernel_thread_start callers including pre-logger startup. Updated
thread-start-unlock-log.py: compare PAC-stripped x20 continuation against APFS worker
a2abe7c; other callers direct-tail original unlock with args/LR unchanged. Only selected
worker path logs/delays. Packed scope branch and pass-through disassembly verified,
kernel exact/checksum OK, i168 SHA-256
`b29df950b00705aefb5c12af9e329e9e78e4ebf851a331b6f7ae84984fa885ea`.
Pulled before recovery->bootloader->RAM boot i168 OKAY. Wait30s then recovery; require fresh
boot ID and THREADUNLOCK before drawing conclusions. No photos requested.

**i166 reproduces ownership panic without thread_deallocate; i167 unlock tracing.**
User reports same not-owned panic. Log /tmp/opencode/recovery-log-i166/untagged-4ae81e05
only1 round/errors0 ends network init. Thus enabling kernel_thread_start is sufficient;
not proof worker vs creator failure. Native kernel_thread_start calls thread_create_internal,
then unlocks kernel_task, tasks_threads_lock, later locks/starts/unlocks new thread.
New thread-start-unlock-log.py hooks verified +58/+64/+a8 unlock callsites, logs caller and
lock offsets/words, IOSleep5s for writer, then tail-calls original unlock with original args/LR.
No ownership bypass. i167 packed kernel exact/checksum OK, SHA-256
`c47a07c2d90dc761724c7a1ded086c429c9d17df2a5a60fb175a8e13dd79aca0`.
Recovery log saved then RAM boot i167 OKAY. Wait >=30s before recovery, pull THREADUNLOCK.
Diagnostic delays hold existing locks and may perturb timing; do not treat absence of panic
under this probe as a fix. No photos/transcription required.

**i165 GC-pair sustained log / i166 thread-start-only split submitted:** recovery log
`/tmp/opencode/recovery-log-i165/untagged-4bb985a0`: uptime256/129 rounds/errors0, old
earlyboot tail reached, no retained ownership failure; GC unresolved offsets gone. i166
adds only kernel_thread_start slot8184840 on i165, leaves thread_deallocate stub, testing
worker activation independently of caller ref release. Worker at a2abe7c directly calls
bound lck_mtx_lock/unlock, but setup thread_set_thread_name slot8184be0 and priority-policy
slot8184c10 are still stubs (identities not yet verified). Do not assume worker failure cause.
Packed kernel exact/checksum OK, SHA-256
`e7bc16fcf6f6db66b377a3da93ae8b74de23b6130abf8aaf032c01397f7c43c1`.
Recovery log saved then RAM boot i166 OKAY. Next recovery result distinguishes thread-start
activation from deallocate path. This deliberately leaks caller thread refs during diagnosis;
not a production simplification. No screenshots requested.

**i164 sustained logging / i165 split test boot submitted:** recovery log
`/tmp/opencode/recovery-log-i164/untagged-4810c8c3`: uptime226s/114 rounds/errors0,
retained tail t125s reaches previous earlyboot area, no ownership-panic evidence. This
narrows i162 regression to its four additions, not proof no later panic occurred.
i165 = i164 plus only fs_buffer_cache_gc_register/unregister slots8184738/8184740;
kernel_thread_start/thread_deallocate remain unbound for this test. Packed kernel extracted
equals input, checksum OK, image SHA-256
`8f4e07729792e3c82e330afe127658218db606f0a2902722733359ab2bcca026`.
After i164 log pull, recovery->bootloader->RAM boot i165 OKAY. Next fresh recovery log
distinguishes GC pair from thread pair; no request for photos or lock assertion bypass.

**i163 probe did not retain lock evidence; i164 isolates i161 set.** Recovery log
`/tmp/opencode/recovery-log-i163/untagged-4ade5071`: fresh boot ID, 1 round/errors0,
no LOCKFAIL; ends after network initialization. Do not infer caller or assume the sleeping
panic probe worked. i164 is packed kernel-identical to i161: five process/sbuf/vnode additions
on i160, excludes i162 GC/thread additions and i163 panic probe. Logging/data seed unchanged.
Packed extraction comparison and SHA256SUMS pass; SHA-256
`d6e269e5f25cd08cfc3912650a2eda22e2a1d7b9e6525304e56cd253bf2ff249`.
After pulling i163 log, recovery->bootloader->RAM boot i164 OKAY. Next recovery logs distinguish
i161 set regression vs i162 additions. No lock assertion bypass and no photo request.

**i162 lock ownership panic / i163 durable diagnostic boot submitted:** user reports
"unexpectedly not owned by thread", no photos/transcription available. Recovery log
`/tmp/opencode/recovery-log-i162/untagged-7d0ad580`: one round/errors0, ends before panic.
Cannot attribute to a specific new binding yet. New lock-owner-log.py hooks named
mutex/ticket not-owned panic helpers at +4, verifies original instructions, prints caller
offset/lock/thread/lock words/three frame offsets then IOSleep loop so periodic writer can
persist it. Does not return to bypass assertion; diagnostic intentionally stops the failing
thread. i163 keeps all i162 bindings. Packed kernel exact, ownership hook branches and
checksum verified, SHA-256
`0e080a0e9aaad1135463be2e14ea239ae28a0d1ab83d51c1468469e58ffd04f7`.
Pulled log before recovery->bootloader->RAM boot i163 OKAY on42fe0af2. Next recovery pull
LOCKFAIL; no request for photos. Don't call this a lock fix or disable ownership checks.

**i162 batch repair requested; built, NOT booted (phone absent).** Based on i161 includes
five unbooted process/sbuf/vnode repairs, adds four further reviewed functions:
original a589230 -> fs_buffer_cache_gc_register (mutex-protected 16 callback/context slots,
empty slot installation and ENOMEM), a5893dc -> unregister (compare both fields, clear pair),
a42f7ac -> kernel_thread_start (kernel_task, priority -1, callback/parameter, returned-thread
start), a42d854 -> thread_deallocate (reference release then complete at zero).
Original/native instructions compared with corresponding XNU source; gate/ABI checks intact.
Rejected tentative a8e51b4 -> proc_isabortedsignal after branch logic mismatch; no binding.
Nine new repairs total relative to i160; four slots beyond i161 recorded in
/tmp/opencode/data-build/review162.json, packed diff limited to those slots and chain bits
preserved. Packed kernel exact, SHA256SUMS passes, image SHA-256
`84999e98eb0f05c341754321b17b00083d9bd5590f71bbb3fd69993e3d6062ca`.
Seed/ADT/log settings unchanged. No ADB or fastboot serial at last check; return recovery
to pull then RAM boot i162 per standing permission. No photo requests.

**i160 logs / i161 batch process bindings:** recovery pull
`/tmp/opencode/recovery-log-i160/untagged-49e6e823`, uptime270/136 rounds/errors0/GPT1/1.
Prior accounting/throttle repaired caller offsets absent from retained log; other stubs persist.
i161 binds original a8e0db4 -> proc_getcsflags (proc_ro zone5/backref validation, field read;
native same checks), a8dc320 -> proc_persona_get (proc lock, persona at a8, persona ref,
unlock), a927e84 -> sbuf_printf (varargs, overflow flag, capacity/vsnprintf loop),
a8e2174 -> proc_getexecutablevnode (ref/get/rele path; native p_textvp/getwithref),
a8e2294 -> proc_getexecutablevnode_noblock (getwithref_noblock verified via mapped callee).
Five reviewed slots in /tmp/opencode/data-build/review161.json, carried on i160 kernel.
Packed kernel exact; only five slots changed and chain bits preserved; checksum passes.
Image SHA-256 `2f609010217d787ae625f234438160c6b291e37a7c6fff3821aca8fb9e7880a6`.
No new boot: phone disappeared from adb/fastboot after log pull. Await recovery/reconnect,
pull once more if new data then RAM boot i161. User no longer supplies pictures; use logs.
Remaining unresolved identities not safely named are left untouched; don't equate log cleanup
with proven resolution of the earlyboot stall.

**i159 Data mount confirmed / i160 accounting bindings boot submitted:** log
`/tmp/opencode/recovery-log-i159/untagged-49ea3608`, uptime964/483 rounds/errors0/GPT1/1.
disk1s1 Data UUID072899DE... mount-complete at65.732; no device-not-ready in retained log.
Photo reaches STARTING EARLYBOOT SETUP; not full boot. Repeated +328e56c/+328e594 target
original a425864/a4258c8, identified task_update_physical_writes/task_update_logical_writes
by task fallback, flavor1/credit-debit, ledger updates and vnode external-device handling;
native functions a3d7bd8/a3d7c94 match source/ABI. +32a825c original a5ebf60 is
throttle_thread_io_tier_above_metadata, min effective tier/override then <1, native a550314
calls effective policy35 then <1. i160 binds slots8184b98/8184b90/8184c18; chain metadata
preserved, diff limited to those slots, packed kernel exact/checksum OK. Data seed/ADT/logging
unchanged from i159. SHA-256 `173cf603ead000f5cc7825561b4fe031f07f0076003ec4b60bd4767138dcf6cb`.
After pulling log, recovery->bootloader->RAM boot i160 OKAY. Next recovery inspect fresh log;
do not claim accounting bindings are the only blocker or full earlyboot completion.

**i159 2GiB writable-RAM candidate boot submitted:** repair run42912114577 success;
seed `.scratch/ios27/data-role-42912114577/data.dbr`, logical2147483648,
identity6dd1ab717f9b718f5ceec925960eba42ec774ee6d574296d7562975783522fd5,
encoded1560768/SHA2561e8a14c121464de4d42eabc3985de436e1beb4b4b91e6e0d3e6b7e6463d5b929.
Local inspect/NXSB bounds pass; latest APSB xid14 role40, UUID072899DE... unchanged.
i159 changes only payload seed from i158; packed extracted kernel and ADT byte-identical,
seed exact, checksum passes, image SHA-256
`a9222a1a09aac8c2b8c7d32d98ccadbeaec0fc70240cbdc73e4d3153f42dfd5c`.
Logging on/data persistence off/no-SEP explicit. Pulled old i158 log before reboot; recovery
to bootloader then RAM boot i159 OKAY. Next recovery log should establish disappearance of
disk1 NotReady and Data/tzinit progress; do not claim success until runtime evidence.

**i158 disk1 write refusal source cause:** fresh recovery log
`/tmp/opencode/recovery-log-i158/untagged-473b29b9`: uptime292/147 rounds/errors0,
APFS disk1s1 cluster_push failed16, block driver requests fail/not-ready. RamBlock
NativeRamBlock.cpp:219 logical>0x80000000 selects isBig; doAsyncReadWrite isBig&&!direct
returns NotReady for writes. This is guaranteed with 3GiB RAM seed and persistence0.
Fix keeps driver policy: set-ram-data-role.py now diskutil-resizes existing APFS container
to exactly2GiB, verifies NXSB geometry equals target before truncating, keeps UUID/role.
Commit42fd33d; repair run42912114577 in progress. Source threshold/assert/Python checks pass.
Await artifact, verify <=2GiB and identities, then payload-only replacement on i158 kernel;
do not enable Android persistence. Phone remains recovery; no repeated i158 boot.

**i158 runtime photo, await recovery evidence:**
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-068d4907-b0bf-4444-bb11-2b3732ea4323.png`
shows continued launchd boot tasks, disk1 not-ready errors and unresolved functions; no
unencrypted-Data panic visible. Does not establish Data mount/tzinit completion or disk1
identity. Phone absent from adb/fastboot; await recovery full log before another boot.
Preliminary trace: APFS +3261438 slot8184088 -> original abc0a70 (boolean global getter),
+3261348 slot8184758 -> a8f0488 (boolean global getter). Do not bind by guessed name.

**i157 unencrypted Data panic / i158 explicit no-SEP configuration:** user reports exact
`unencrypted data volume is not allowed`. Original APFS string at7bccb9a/xref a210d34;
routine a210c94 first queries /filesystems/DoNotUseSEP, then enforces role0x40 encryption
when property absent. Binary helper returns property object and releases it; a u32 value
is sufficient. UniXNU docs/devices/e5.md records the same fix with volatile Data RW.
i158 adds only ADT /filesystems/DoNotUseSEP u32=1; kernel unchanged from i157, seed unchanged,
no claim of encryption. This is intentionally unencrypted ephemeral RAM Data, no SEP-backed
Data protection, not a production security configuration. Android persistence still off.
Packed kernel and ADT round-trip checks pass; image SHA-256
`51924673cba8a74725918a97814c0cbdc8ca7c810e361ed2642a65862b3ff8b5`.
Pulled i157 log (1 round/errors0, panic tail not retained), rebooted recovery->bootloader,
RAM boot i158 OKAY. Next recovery pull to establish Data mount/tzinit outcome.

**i157 repaired Data-role seed + gate destroy, RAM boot submitted.** Role run42908758234
success; artifact `.scratch/ios27/data-role-42908758234/data.dbr`, encoded SHA-256
82e6c4a4c626da3008d8ddec400ef8e80aff2aab46cba3f05489bd7f4ca8f835, logical identity
7baf39e5348a0d63ebf1de6ac84e9c5f5cbde1afd958395ab116a9bd16c49798, 3GiB.
UUID unchanged072899DE..., latest APSB xid9 role0x40, xids6-9 role0x40; locally verified.
Gate-destroy original a3ea28c validates gate type/tag, rejects allocated gates and destroys
unowned gate under interlock; native lck_rw_gate_destroy a3ada04 matches checks and tails
to gate_destroy_internal. Shipped slot81848e0 now0x80110000033a9a04 (was stub), metadata
preserved. i157 otherwise i156 kernel/payload with corrected exact packed seed. Kernel and
seed extracted from packed container match inputs; checksum OK, image SHA-256
`b60a0b2f8a28f070026966a534ae95cfc7588b80930ab165ad3e994a4e2923ac`.
Pulled prior i156 log, rebooted recovery->bootloader, RAM boot i157 OKAY on42fe0af2.
Runtime Data discovery/tzinit outcome pending. Logging on; Android persistence off.

**Data-role verifier correction:** run42907898666 attached and edited the seed but failed
post-edit role assertion because the helper read offset0x3bc. Compiled offsetof against
installed apfs-fuse DiskStruct.h confirms apfs_role=0x3c4, UUID=f0, name=2c0, Data=40.
Original seed's three APSBs independently rechecked at3c4: still role0, so provisioning
diagnosis stands but earlier offset references are withdrawn. Commit a2096a6 corrects read;
synthetic APSB check and Python syntax pass. New repair run42908758234 queued.

**i156 recovery evidence / Data-role repair:** pulled fresh log to
`/tmp/opencode/recovery-log-i156/untagged-4c787f10`: uptime306s/154 rounds/zero errors,
early mount evidence overwritten by +322ce3c spam. Offline packed seed APSBs at a2000,
182000,185000 all have apfs_role (offset3bc) zero, not Data0x40. Added
set-ram-data-role.py/workflow: decrypt original known-hash seed, use diskutil APFS role D,
preserve UUID072899DE..., detach/repack, verify newest xid APSB role and logical size.
Commit7a5b23a pushed; role workflow42907394103 running. No hand APFS checksum edits.
Await artifact and verify, then replace packed seed only; kernel UUID unchanged. Still need
fix gate-destroy binding and retain early mount/log evidence before next hardware conclusion.
Phone left in recovery, no repeated i156 boot.

**i156 supplied runtime photo:**
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-f53f4eed-eddb-46e1-b193-2c305f841aeb.png`
shows launchd tasks including fips, finish-demo-restore, systemstatuscheck, prng_seed,
launchd_cache_loader and load_bootstrap, but tzinit still says unable to locate data and
resets system partition. No claim Data discovery succeeded. Repeated disk1 device-not-ready
and unresolved calls visible; identify disk1 from full boot log before attributing to Data.
No recovery/fastboot device currently visible. Await recovery and pull fresh i156 log; inspect
RAM provider publication, Data mount-complete/UUID/read-only state and role lookup before
altering seed/kernel. Do not infer fatal endpoint from this single screen.

**i156 RAM Data image built and boot submitted:** CI42902749140/eeb5d0b success,
native hash75fcf4edf32111211f500ab0aba21970e26f6b55559ea2e3e49e24f37cff6061,
fresh map /tmp/opencode/recovery/map-data.json and emit data-native.macho. Replayed mandatory
patches using verified empty wide-user caves, eleven reviewed bindings including
proc_find_ident_validated and correct APFS gate-open override. Kernel Data UUID equals
072899DE-BA32-4875-B776-41C1929F4002; mount failure gate/symbols present.
Payload includes exact data.dbr as scratch-data.img, initrd entry, and 16-byte zero
PackedRAMDisk memory-map property; other ADT properties preserved. Packed extracted seed
matches validated input byte-for-byte, kernel matches patched input; SHA256SUMS OK.
Image SHA-256 `a394f20a87d5cfb0ef42eefacfa225021c5818503970ff479723badf072372f0`.
Logging on, Data persistence/diag exports/autoreset off. Pulled old i155 log before reboot,
then adb bootloader/fastboot RAM boot i156 OKAY on42fe0af2. OS result pending; return recovery
and pull fresh log before claiming tzinit/Data success. Scratch build files /tmp/opencode/data-build.

**RAM Data seed verified / Data kernel queued:** run42899327883 successful, decrypted to
`.scratch/ios27/data-seed-42899327883/{data.dbr,seed.json}`. Packed inspect exactly matches
report: logical3221225472, encoded1138816, logical identity232e1864153e1c1acce8ff683372775ed3e11b54f5300eb455b077694e79c3da,
encoded7c24e514fbf6ab2d6e62f58560cc60cfad4e0e30b0004c0c158410f6b80f05b4.
Three APSB blocks a2000/182000/185000 agree on UUID072899DE-BA32-4875-B776-41C1929F4002,
name Data. Separate ios27_data_defconfig adds mount gate and exact UUID only; default/fallback
untouched. Committed5da979d, dispatched kernel run42901338253 with explicit Data defconfig.
Next validate kernel Data mount symbols and shipped UUID, add exact packed seed and ADT
RAMDisk handoff using existing payload tooling, preserve logging and reviewed bindings.
Do not boot Data kernel without matching seed; no Android/user filesystem persistence enabled.

**i155 hardware passed zone panic into Data boundary:** recovery log
`/tmp/opencode/recovery-log-i155/untagged-4c62fed4`, uptime64/33 rounds/zero errors/GPT1/1;
tail flooded by +322ce3c -> slot81848e0 -> original a3ea28c (gate destroy family).
Photo `...-8a104f4d-34bb-4fa5-88d5-00f3b9dd4b1c.png` shows launchd early boot tasks,
tzinit unable to locate Data, reset attempt read-only, tzinit exit. This matches documented
owner-manifest boundary in ROOT-BRINGUP.md, needs RAM APFS Data not writable root or Android
userdata. Added dispatch-only ios27-data-seed workflow: read-only selected iPhone OS, exact
launchd hash c640246d..., existing make-data-seed 3GiB logical packed RAM seed, encrypted
artifact. No skip-setup or persistence. Kernel Data UUID/mount and payload integration await
seed metadata; don't enable Data mount before matching input exists. Phone left in recovery.

**i154 panic photo / i155 process-reference binding:** repeat-boot photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-53c884ed-aa93-43f6-955b-f18d9d8b79c1.png`
appears to report zone_require address not in a zone, addr0; exact panic transcription is
photo-limited. Immediately preceding UNMAPPED +31443d8 -> sandbox thunk a1677c4,
slot 8174418 -> original a8df638. Original code matches proc_find_ident_validated:
EINVAL for null arguments, pid lookup, pidversion/uniqueid checks and out-process ref.
Native export a7b7c3c is a validated wrapper over the matching lookup helper. Zero-return
stub reports success without initializing output; bind real function, no zone-check bypass.
i155 differs from shipped i154 only in reviewed 8-byte slot, new word
0x80110000037b3c3c; chain high bits preserved. Packed kernel equals input, checksum passes.
Image SHA-256 `f2aa6930ee68b12b5a9e8632beb1308aff656b97e7a87f506d00e5914d1d6e94`.
Logging remains enabled; hardware untested. Phone absent from adb last check, await recovery
then pull log before RAM boot. This candidate does not prove wide-VA path fully qualified.

**i154 hardware panicked; panic text missing from durable log.** Recovery pull initially
failed during adb transfer, retried successfully to
`/tmp/opencode/recovery-log-i154-retry/untagged-4df79761`: fresh boot ID, uptime58s,
30 rounds/zero write errors/GPT1/1. Last msgbuf line t74s reports launchd wakeup limit;
no panic string, delivered fault or initproc exit reason recorded. Do NOT attribute panic
to wakeup warning without evidence, or claim previous libobjc fault resolved. User reports
visible panic; need exact panic text/photo (including ESR/PC/FAR if present) before next fix.
Periodic logger is working but does not guarantee final panic retention; no panic-time UFS writes.
Phone remains in recovery; no redundant reboot pending panic evidence.

**i154 built and RAM boot submitted (wide user VA).** CI42882786046/d109a0a success,
native SHA-256 `2f093d4f5584e83c5903513218ffb9e89fb894b237bb4d668656ec6221572bd6`;
fresh map /tmp/opencode/recovery/map-wide-user.json and emit wide-user.macho.
Scratch assets from earlier builds absent; rebuilt using repository bind/data scripts, extra
reviewed bindings in /tmp/opencode/wide-user-build/bind.json, forced a3ea770->gate_open override.
Old logstub cave ab12400 now occupied; moved logstub to ab15688, PMGR probe to ab15a00,
tcfix to ab16800 and shift constants ab16900/908, all zero-checked. tcfix relocated with
byte checks: SITE a4cf0c4/JOIN a4cf110/PAD a4cf5e8. Old panic-overlay and sync freeze omitted;
recovery logging is the diagnosis path. Packed shipped154.macho equals input and ten repaired
slots match native symbols; TCR routine writes low T0SZ=17, user VA mask 0x7fffffffffff,
static1 present, sheng-log=1/data-persist=0/diag=0/gpu-trace=0/autoreset=0.
Image SHA-256 `fe2e53843030d211bace4a916311f0e924568fec8489d5f3e300710380e8b3a5`.
Pulled existing i153 log before boot (same boot ID, no new run), adb reboot bootloader then
fastboot boot i154 on 42fe0af2: Sending/Booting OKAY. Hardware OS result pending; return to
recovery and pull a fresh boot ID. No flashing.

**i153 log retrieved (2026-11-11), remaining blocker unchanged:**
`/tmp/opencode/recovery-log-i153/untagged-4db57417`: uptime234s/118 rounds/zero errors,
GPT1/1. +322ca28 no longer present and no repeated gate-close panic; gate fixes passed this
run. At t127s DELIVER exc1/c0=1/c1=781e94aabf0, PC182a07140, x20=1e94aac28,
same libobjc words; SYNCSTOP caller-offset37a9d0c confirms deliberate fatal-exit freeze.
No visible panic therefore does not mean successful boot. Phone left in recovery, no redundant
boot of i153. Wide-user CI 42877072247 never acquired a hosted runner, no steps/logs; annotations
report macOS ARM64 capacity constraints. Started NEW workflow_dispatch 42882786046 at d109a0a,
not a rerun. Await compatible kernel build before another image.

**i152 gate panic explained / i153 fix:** user reports gate already closed, holder equals
current thread. Binding the missing close exposed another wrong pairing: APFS slot 81848f0,
original a3ea770, was bound to native lck_rw_gate_try_close. Original a3ea770 forwards x1
and then uses release CAS current_thread -> zero, wake-waiter/turnstile completion; this is
gate_open, NOT try_close (acquire zero -> thread). Original/native gate-init match exactly.
Corrected slot to `_lck_rw_gate_open` with fix-apfs-gate-open.py, preserving chain metadata;
kept verified close binding and all ownership checks. i153 packed kernel matches input,
SHA256SUMS OK, image SHA-256
`b2eaa8bde1235ee5272ce355a7ac112ebf135ffe5c0d790fc916c947dc55d6cc`.
Logging remains on, hardware untested. Durable matcher correction must map a3ea770 to open,
not try_close, across other owners too; investigate before claiming all gate paths repaired.

**i151 persistent logger hardware-confirmed; i152 RAM boot sent.** Recovery pull to
`/tmp/opencode/recovery-log-i151/untagged-60200767` reports fresh boot_id 1612711783,
uptime264s/133 rounds/zero errors, GPT1/1, export_bytes0/export_generation0. Current INS32,
DELIVER and SYNCSTOP diagnostics establish this is the new iOS27 run, not stale iPadOS log.
Exact fault: c1/x19/x0=241e91eebf0, PC18274b140, x20=1e91eec28, caller-offset37a9d0c;
same libobjc ISA-mask issue. User authorizes recurring recovery-pull -> adb reboot bootloader
-> RAM fastboot boot cycles. Pulled before reboot; i152 transfer and boot both OKAY on 42fe0af2.
Wait for recovery next, pull to a new directory and check +322ca28 removal / new failures.

**i152 APFS spam binding fix, logging retained:** revisited original target a3ea654:
gate in x1, tag/ref verification, CAS from 0 to current thread, interlock slow path and
panic when holder nonnull identify lck_rw_gate_close (not try/open/steal). Native export
a3adba4 forwards x1 to gate_close at +12, whose behavior independently matches source.
New bind-apfs-gate-close.py validates unmapped slot 81848d8 and native wrapper before changing
only its low-30 target bits, retaining chain metadata. i152 shipped slot
`0x80110000033a9ba4` -> `_lck_rw_gate_close`; kernel diff against i151 is exactly this
8-byte slot. Persistent logging args unchanged from authorized i151. SHA256SUMS passes,
image SHA-256 `75a32fe9bfa1dccd15e3430762a63d973d0e66a0d42304d7fcab99a77acdb0b7`.
Hardware untested. This fixes the binding behind +322ca28; it does not suppress other
unresolved calls or prove boot correctness. Future rematches should carry original target
`0xfffffff00a3ea654` -> `_lck_rw_gate_close` through reviewed bindings.

**i151 logging enabled with explicit user authorization (2026-11-11).** Uploaded pull-log.py
matches repository copy byte-for-byte. Read-only recovery pull succeeded from serial 42fe0af2:
sda34/unixnu start 90660608, size 33554432 4K blocks, log LBA 124206848..124215039.
Existing log is stale Darwin23.6 (229 rounds, uptime462, errors0), NOT current iOS27 evidence.
Next image i151 is kernel-identical to i150, payload arguments changed to sheng-log=1;
sheng-data-persist=0, sheng-diag=0, sheng-gpu-trace=0, sheng-autoreset=0. Existing
source-storage archive already contains BootLog and starts it after UFS start. Intended writes:
bounded diagnostic log in unixnu tail only, no OS filesystem/Android userdata writes. Export
and auto-reset intentionally disabled. Actual logger startup and writes require hardware test.
Packed config and byte-identical kernel verified; SHA256SUMS OK. Image SHA-256
`ac4d75662b1f09419e7f44e3829e2e8468d68c24c1bee089642d0d6b5230ceb2`.
Runtime read-back must verify a fresh boot ID and iOS27 kernel identity, not stale header.
Windows adb works via /tmp/opencode/log-adb-bin/adb wrapper (pull destination wslpath conversion).
Use pull-log.py --serial 42fe0af2 --out <new-directory> in recovery after the test.
User authorizes persistent diagnostic logging, not broader persistence or fastboot flashing.

**i150 exact identity / wide-user-VA source fix:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-cde9bcb1-9257-45b8-a72f-59664ac835a5.png`.
Correct previous transcription: fault instruction is f9400268 (`ldr x8,[x19]`), not f9400260.
The full 12 words match SystemOS raw offset 0x20e6e130, cache .01 offset 0x1b130;
fault is unslid VA 0x18041b140, libobjc.A.dylib realizeClassWithoutSwift+0x30.
`ipsw dyld a2s` confirms symbol. LR low bits match libobjc _finishInitializing region,
but do not assume same slide without saved-image mapping evidence.
Bad x19/x0 0x501e0172bf0 versus x20 0x1e0172c28; libobjc _finishInitializing uses
ISA mask 0x7fffffffff8 (disassembly at 18041e484). Bad class preserves PAC-range bits
0x50000000000 above native 39-bit VA. Sheng explicitly solves this by T0SZ_USER=17
(47-bit user VA) and coordinated user L0/TCR early-entry changes, preserving TTBR1.
Ported nine related source files only as native-arm64e-user-va.patch; patch application and
check-user-va.py pass. No PAC stripping/bypass or userspace modification. Runtime validation pending.
Next compatible CI build, inspect user pmap/TCR code and size constraints, rematch and RAM boot.

**i149 confirms fatal exit caller / i150 unredacted instructions:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-5f935940-1865-47b0-ba65-e70ba569bef4.png`
shows DELIVER exc1/code0=1, then SYNCSTOP caller-offset 37a9d0c. This equals the
return address of proc_prepareexit's call at a7add08, confirming the exit path. CODE has
one `<ptr>`-redacted 64-bit instruction pair; surrounding apparent words do not yet match
the available binaries, so do not infer corruption from tentative transcription.
Recovered IPSW OS/SystemOS raw images locally under .scratch/ios27/unixnu-work/ipsw/24A446;
mounted read-only with apfs-fuse under /tmp/opencode/recovery/{os-mount,systemos-mount}.
Extracted disk dyld and cache /usr/lib/dyld for comparison. i150 prints 12 instruction words
individually as INS32, retaining continuation and sync freeze. Source variant
/tmp/opencode/recovery/deliver-context32.py. Packed kernel matches input and checksum passes;
image SHA-256 `49b3c50fceaf0643e2e2933a6d28ebccfe5d7c6d68e25953c2399cc54b00ad4f`.
Hardware untested, fastboot empty. Next capture INS32/REGS/SYNCSTOP from RAM boot i150.

**i148 hardware / i149 context probe:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-067803e9-a762-4a7a-b5e4-341591da8085.png`
shows DELIVER exc 1 c0 1 c1 approximately c1e747ebf0 then SYNCSTOP caller <ptr>.
Do not identify sync caller from that: PAC-signed LR is apparently logger-redacted. New
deliver-sync-probe.py strips PAC and prints LR minus collection base. i149 adds instruction
context copyin (48 bytes around PC), x19/x20/x0/x1/SP logging in separate verified-empty cave
at ab12fa0, formats ab13200/ab13280, with extra helper preserving x19/LR. It continues triage,
then freezes at sync. Source variant `/tmp/opencode/recovery/deliver-context.py`.
Packed i149 kernel matches patched input; SHA256SUMS passes, SHA-256
`bfc769394c4628b07fa8d70dc9bea0a89283df4df44d8a5f683bf1aeb9542d39`.
Hardware untested, fastboot empty. Capture CODE, REGS, SYNCSTOP caller-offset next.
Known direct sync callers in i148: proc_prepareexit call a7add08, reboot_kernel a7c308c/
a7c31f0, kdbg_dump_trace_to_file a77e508; compare return address, not call address.

**i147 repeats first delivered fault; i148 tests recoverability.** Photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-7d91b2c1-90a0-439c-9aa4-cda1aedbf4f6.png`
again prints f9400260 (`ldr x0,[x19]`), Rn 0x13, invalid x19, copyerr 0.
Do not assume this exception is fatal: previous diagnostics freeze before userspace handlers.
Temporary /tmp build assets disappeared after reboot. Recovered shipped i147 kernel/payload
from staging (not rebuilt against an older native). New deliver-sync-probe.py is scoped to
i147 byte checks: log delivered exceptions and replay triage's original instruction/continuation;
restore unix_syscall/mach_syscall entry instructions to remove spam; hook sync to log caller LR
then freeze. A sync caller must be resolved before calling it a fatal exit path.
i148 SHA-256 `226bf3aaa26a057c401c12dc686fb05d8cf4d7291166eb95d5ec9ea74ef09914`.
Packed kernel extraction equals patched input; checksum passes; triage continuation and restored
syscall entries verified. Hardware untested, fastboot empty. Next boot i148; capture DELIVER and
SYNCSTOP, or any progress. Do not pursue speculative pointer changes before recoverability check.

**i146 hardware / i147 operand probe:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-773378b2-c639-4953-94b4-cce641caabed.png`
shows additional PID 1 activity (vm.malloc_ranges), then USERFAULT PC approximately 101767140,
FAR approximately 4c1e820abf0, ESR 92000004, code0=1/code1=FAR. Different PC alone does not
prove fixup success; capture remains first delivered exception, not necessarily fatal.
i147 uses the existing FAULTOP instruction/operand freeze on i146's kernel and all eight
bindings. Build `/tmp/opencode/build-fixup-faultop.sh 147 /tmp/opencode/bind-i138.json ...`.
Image SHA-256 `9e90fe7471c0e05b207b5ba8fcd0f7b41d485b860de41ad0e5c4fe8946005d0c`.
Packed extraction verified checksum, exception hook, FAULTOP log-then-freeze and eight slots.
First build failed due /tmp full; removed only our reproducible i134-i146 FAT extractions and
rebuilt cleanly. Fastboot empty; next RAM boot i147 and capture FAULTOP.

**i146 built from successful CI 42754674647 (9b61209), 16K dyld fixup candidate.** Native
SHA-256 `f62424a9c53841f36b1e0d04bfc152aa369674877e58ddabecc182072c6ed652` matches report.
Fresh matcher `/tmp/opencode/map-fixup-pages.json`, emit `/tmp/opencode/fixup-pages.macho`,
separate build input `/tmp/opencode/ios27-fixup-pages-build/k.macho`; build
`/tmp/opencode/build-fixup-pages.sh 146 /tmp/opencode/bind-i138.json <absolute-data.json>`
with mandatory gates, SYSTRACE and USERFAULT freeze. Relocated tcfix by verified native
arm_vm_prot_init movement: SITE a4cccfc, JOIN a4ccd48, PAD a4cd220, retaining byte checks.
Packed image SHA-256 `d34e5d92b98a9e09e71ed1b31f3a6690ced40773bd4cdd7ba8b1cdeb4de308b2`.
Extracted i146.macho: all eight repaired slots match symbols, pager's 16K validation
instruction verified at a487574, static1 present, exception_triage freeze hook verified.
SHA256SUMS passes. Not hardware-tested; next RAM boot i146 and capture USERFAULT if it stops.

**i145 hardware and 16K dyld pager source candidate:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-7b105129-773c-40cc-b6e8-dc98395606fc.png`
prints insn f9400260 = `ldr x0,[x19]`, Rn 0x13, Rt 0, copyerr 0, with invalid x19
(photo value approximately 641e8daebf0). Do not assume this first delivered fault is fatal.
Source inspection found missing reference pager support: pageIndex uses PAGE_SIZE rather
than seg->page_size; page-in fixes only 4K instead of assembling the image's 16K fixup page.
Ported sheng vm_dyld_pager changes as native-4k-dyld-fixup-pages.patch, after zero-key patch;
omitted six console-debug blocks, retained signature validation, bounds and cleanup, added
supported page-size and segment/header agreement checks. Runnable check-dyld-fixup-pages.py
passes against prepared baseline (applies both patches to temp copies and checks geometry,
tainted-page refusal and cleanup). Runtime attribution remains a hypothesis; compatible CI
rebuild and RAM boot required. No commit/push for this change yet.

**i144 delivered exception / i145 diagnostic:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-d6702487-e52b-4282-a05e-fcdda26d32d1.png`
shows PC approximately 10258b140, FAR approximately 601e902cbf0, ESR 92000004,
code0=1, code1=FAR. ESR decodes EL0 data read, translation fault level 0; code0 is
KERN_INVALID_ADDRESS. First delivered exception is not yet proven the fatal SIGBUS.
i145 reads the PC instruction and extracts encoded Rn/Rt register indices and saved values;
prints FAULTOP pc/insn/rn/value/rt/value/copyerr then freezes. Interpretation of register
fields depends on decoded instruction (31 means SP or zero depending on instruction).
Build `/tmp/opencode/build-faultop.sh`, probe `/tmp/opencode/systrace-faultop.py`.
Image SHA-256 `a68bb9e328bbc7149ade4c6b7131d8796c6c34b1a1854eb1b3c162beae40c0fd`;
packed format and log-then-freeze verified at 0xfffffff00ab12d70; SHA256SUMS OK.
Fastboot serial 42fe0af2 present at last check; i145 not yet booted.

**i143 hardware: namespace 2 / subcode 0xa (SIGBUS).** User reports initproc failed to start,
description none. Different from previous PAC reason, not proof every PAC problem is fixed.
Built i144 diagnostic `/tmp/opencode/build-userfault.sh`, probe
`/tmp/opencode/systrace-userfault.py`, using i143 kernel/bindings. Hooks exception_triage+4
at 0xfffffff00a3936c0, after failed-fault handling; retrieves user saved state through
current_thread/get_user_regs and prints USERFAULT PC/LR/FAR/ESR/code0/code1, then freezes.
This intentionally catches the first delivered exception (not necessarily fatal).
Packed image SHA-256 `4916962936a894a9a71b6a28bbe7f54d050573734350ac7cab554af3b9dfae2e`;
checksum, hook branch and log-then-freeze disassembly verified in extracted i144.macho.
Hardware untested, fastboot empty. Next RAM boot i144; capture USERFAULT line before any panic.

**i143 zero-key-signing candidate built from successful CI 42751319161 (3c5996b).**
Native SHA-256 `8426bd809659e6d5653131e7e4b47cc2af3e840c621dcc07642c1dbfc5186fb8` matches
artifact report. Fresh map `/tmp/opencode/map-zero-key.json`, emit `/tmp/opencode/zero-key.macho`,
separate input `/tmp/opencode/ios27-zero-key-build/k.macho`; build script
`/tmp/opencode/build-zero-key.sh`, bindings `/tmp/opencode/bind-i138.json`, mandatory gates,
SYSTRACE + TRACE_USER_PAC + FREEZE_USER_PAC, HOLD_SYNC unset. tcfix byte-check initially rejected
the moved native site; relocated by verified arm_vm_prot_init shift -0x40 in
`/tmp/opencode/tcfix-zero-key.py` (SITE a4cc87c, JOIN a4cc8c8, PAD a4ccda0).
Image SHA-256 `3399d605214eb366c69994d8277ddbf2cd26f32ecfc6fb0793850706680b6f9f`;
packed SHA256SUMS passes. Extracted `/tmp/opencode/i143.macho`: all eight prior repaired
bindings match symbol addresses, static1 present. signPointer at a4893c4 calls
arm_user_jop_disabled at +0x48, branches on explicit disable, then loads jop ID at +0x50
and calls pmap_sign_user_ptr at +0x60 without testing that ID for zero.
Hardware untested; fastboot empty. RAM boot i143 next. PAC probe deliberately freezes on failure.

**i142 hardware / zero-key signing source candidate:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-8d779d13-6d40-4206-b28a-a3f3ad604331.png`
confirms `insn dac11a30` (`autda x16,x17`), ESR 72000802 and an apparently unsigned
userspace pointer in x16. New patch `native-zero-key-user-signing.patch` ports sheng's
four signing-condition changes: vm_dyld_pager signPointer and vm_shared_region v3/v5/part-v5
must sign even for software JOP identifier zero. Evidence: ml_default_jop_pid returns zero
without paravirtual PAC, while native-pac-keys.patch keeps hardware PAC keys active and
ml_enable_user_jop_key is a no-op key switch. Existing zero-ID shortcuts leave auth pointers
unsigned, consistent with the capture; not yet hardware-proven as this fault's cause.
Patch applies to prepared source; check-zero-key-signing.py passes (temporary source apply,
four gate checks and explicit disable-policy preservation). No signature or PAC enforcement
bypass. Needs compatible CI rebuild, re-match, packed verification and RAM boot.

**i141 freeze capture / i142:** photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-93a3b4bc-910b-47ad-8f8c-35bc6ac7e746.png`
reads USERPAC pc 1007b2b74 lr 1007b2b64 esr 72000802, x16 1ea8db270,
x17 9abf0001ea8db220 (photo values tentative). PC does not match an authentication
instruction in `/tmp/launchd-24A446` under tested 16K slide candidates; do not assume image
identity or a particular zero-key defect. i142 extends freeze probe with copyin(PC,4) and
prints `insn` before freezing. Packed checksum passed; extracted probe has copyin and logging
followed by `b .` at 0xfffffff00ab12d24. Image SHA-256
`384f796a894884e55157a18122557d640911850ffd2a126582552490d093c17f`.
Build script `/tmp/opencode/build-userpac-insn.sh`, probe `/tmp/opencode/systrace-userpac-insn.py`.
Hardware untested, fastboot empty. Next boot i142 and capture USERPAC instruction.

**i139 hardware: PAC exception.** User reports `initproc failed to start -- exit reason
namespace 28 subcode 0x105 description none`. Source definitions identify OS_REASON_PAC_EXCEPTION
and EXC_ARM_PAC_FAIL. No faulting PC/instruction supplied yet. Sheng reference patch removes
zero-key signing shortcuts in vm_dyld_pager.c and three vm_shared_region.c paths; amethyst still
has these shortcuts. This is a hypothesis, not a diagnosed root cause, and PAC stays enforced.
Built diagnostic i140 using `/tmp/opencode/build-userpac.sh` and
`/tmp/opencode/systrace-userpac.py` with TRACE_USER_PAC=1, HOLD_SYNC unset and i139 bindings.
Image SHA-256 `ddec7dc7d456921453cf6327fed63d4f0c132be7a48f4b771231a81fbeced2f3`.
Packed checksum passed; extracted kernel hook at `_handle_pac_fail+4` (0xfffffff00a4d29ec)
branches to 0xfffffff00ab12cc0, prints USERPAC pc/lr/esr/x16/x17, restores arguments and
replays displaced `stp x24,x23,[sp,#-0x40]!` before returning to handler+8. No HOLD spin.
Saved-state offsets from thread_status.h: header 8, PC 0x108, LR 0xf8, x16 0x88, x17 0x90.
No new fix claim; boot i140 and capture USERPAC before panic. Fastboot currently empty.

**i139 candidate: remove diagnostic sync hang and bind copyinstr.** `HOLD_SYNC=1` in i137
unconditionally replaces `_sync+4` with a probe ending in `b .`; any sync invocation hangs,
and `kern_exit.c` calls sync before reporting PID 1's exit reason. Thus HOLD can hide a real
exit/panic or hang a legitimate sync. It is not evidence of the underlying launchd exit reason.
Built i139 with `/tmp/opencode/bind-i138.json` and `SYSTRACE=1`, but HOLD_SYNC unset.
Image SHA-256 `33b9e8f46d9753a025df3931e2fa3bf546d0e405780e45b51f024d3a19ae59a6`.
Extracted `/tmp/opencode/i139.macho` verifies `_sync+4 == 0xa9bf7bfd` (original instruction),
HOLD format absent, all eight repaired slots bound to named native targets including `_copyinstr`,
and `_trust_cache_static1` present. SHA256SUMS passes. Hardware untested: fastboot empty.
RAM boot i139 next and capture any actual PID 1 exit/panic; do not describe it as a proven boot fix.

**i137 capture chronology caution:** user explicitly corrected the interpretation: the final
state is the capture containing launchd, not the screen previously described as the final HOLD
endpoint. Do not infer the final stop or PID 1 exit from attachment order. The capture text
`dyld cache mapped system-wide` and static count 2 remain observations, not proof of launchd
startup completion. A separate sandbox `+315c2b0` decodes to slot `0xfffffff008174148`, original
`0xfffffff00a5457bc`; original/native disassembly identifies `copyinstr` (four arguments, initializes
length output, size validation and user-string copy). i138 was built with this candidate binding
in `/tmp/opencode/bind-i138.json`, but has not been packed-image verified or boot-tested and is
not established as the fix for the user's final launchd state.

**i136 hardware / i137 candidate:** photos from RAM boot
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-8ccf8fdc-a4d4-4ab5-baf1-7506a490b263.png`
and `...-175ddad5-06fe-4513-b215-dfb86613ee43.png` show PID 1 opening shared-cache subfiles
through `.77.dyldlinkedit`, then syscall 536 (`shared_region_map_and_slide_2_np`) and
UNMAPPED `+31d5674`. This is further than the previous visible rejection, not proof all caches map.
Caller `0xfffffff00a1d9674` -> thunk `0xfffffff00a2bd204` -> slot `0xfffffff008184508`
-> original target `0xfffffff00a590f94`: identical 40-byte wrapper to native `_cluster_pagein`
at `0xfffffff00737969c`, with both +0x18 calls going to their respective `_cluster_pagein_ext`.
Added binding in `/tmp/opencode/bind-i137.json`; built i137 with unchanged diagnostics.
Packed image checksum OK: `5dd3cf5593d31ce861df2ff939c8840189c7b1955f0eef8537f7f896588b3a5c`.
Extracted kernel: slot targets shipped `_cluster_pagein` `0xfffffff00a4fd69c`; its branch to
`_cluster_pagein_ext` and previous six bindings verified. Hardware untested; fastboot empty.
Repeated `+322ca28` remains unresolved: APFS thunk slot `0xfffffff0081848d8` -> original
`0xfffffff00a3ea654`, gate-like code with second argument as gate. Do not guess which gate
API from proximity alone; original inlining differs from native implementation.

**i136 built after successful CI 42740253195 (a004877):** native SHA-256
`a1fb6a421a25fc5d9895dcaff32eb35018325ce5406711ba7e5bbdf89dfe119a`.
CI's stale matcher lacked i132 repairs; re-matched locally using current XnuPrelink into
`/tmp/opencode/map-second-static.json` (8712 functions, 1373 data), emitted
`/tmp/opencode/second-static.macho`, and built separately under
`/tmp/opencode/ios27-second-static-build` without replacing run5's i135 input.
Build command: `/tmp/opencode/build-second-static.sh 136 /tmp/opencode/bind-i135.json
<absolute-device-scripts>/data.json`, with mandatory gates and SYSTRACE/HOLD_SYNC.
Image `staging/amethyst-ios27-i136/Mu-amethyst-ios27-i136.img`, SHA-256
`03ddfac0968a732a33f1b964c14350c6fe42e96655b85ec0483885752541ffc3`; checksum passes.
Extracted packed `/tmp/opencode/i136.macho`: all six repaired slots resolve by symbol to
proc_ucred, IOTaskHasEntitlement, kernproc, current_persona_get, persona_get_uid,
ubc_upl_range_needed. Both static0 and static1 are present; loader at
`0xfffffff00a7e9c8c` +0x178 checks runtime byte +8 (allowSecondStaticTC), +0x184
selects static1 at `0xfffffff007c60e08`. Runtime result untested: fastboot devices empty.
Next RAM boot i136; require static count 2 and check whether dyld cache rejection clears.
No claim of dyld success yet. New emit has 112 unresolved closure refs / 1448 stubbed sites
before manual bind/data application; do not confuse this with i135's map statistics.

**Second-static-cache source fix (2026-11-10):** `amethyst-owner-release.patch` now adds
`second_static_trust_cache` to `SECURITY_RELEASE`. This enables both runtime permission and
the existing index-1 static loader. Original trust modules are unchanged; engineering trust
remains disabled. Contrary to an earlier retracted claim, `SETUP/config/doconf` explicitly parses
the `# NAME = [...]` configuration sets. The full selected patch series applies, and
`bash scripts/bringup-24A446/check-second-static.sh <patched-xnu-tree>` passes on the actual
prepared source. Local `unixnu build kernel` fails with exit 1 in `build-firehose.sh`:
`sys/ucred.h:164:20: error: '__ptrauth' qualifier must take between 1 and 3 arguments`;
`os/base.h:355` supplies four arguments including `"authenticates-null-values"`.
Log: `UniXNU/out/amethyst/ios-27.0.1-24A446/amethyst/logs/kernel-bootstrap.log`.
No new boot image: finish a build with the compatible toolchain/CI, re-match the changed native
kernel, preserve the reviewed i134/i135 bindings, verify the packaged second-static path, then
RAM boot. Expected checkpoint is two static modules; dyld success remains unverified.

**Trust provenance investigation:** extracted the three trust-cache IM4P files directly from the
iPhone15,4 24A446 IPSW selected by `profiles/os/ios-27.0.1-24A446/profile.toml` using
`unixnu.ipsw.Ipsw`. Their decoded payloads match all three shipped i135 bundle modules byte for
byte, in order: StaticTrustCache SHA-256 `bb208b91c62237c29fa7b80ff73a1705fc92a6f3c9fcb4338d3fea3b3027ebff`,
SystemTrustCache `532083863e00769f667f8c2a0dff2256a1a4f17ae818c1fbee46255fdde951b8`, AppTrustCache
`de18f4f39bdeb55573bf031eb1b67313ce0543b91ad70477cfdfd09b367bcf95`. The bundle extracted from
the actual i135 FAT payload matches `/tmp/tc-24A446.trustcache` SHA-256
`d39802f84695a76c88450bb02d8e496da1ebbab49f76d0e4dc009e0af09ebabb`.
Thus the bundle itself is not missing or substituted. XNU's `load_static_trust_cache` loads
module 0 as static; absent `CONFIG_SECOND_STATIC_TRUST_CACHE`, later modules are treated as
engineering and skipped if runtime forbids them. Shipped i135 disassembly at
`0xfffffff00a7e9e38` loads runtime byte +9 and the following `tbz` branches to the skip path;
there is no `_trust_cache_static1` symbol. Runtime photo reports static 1, engineering 0; this is
consistent with skipped System/App modules, but does not prove the dyld cache CDHash is in them
or that enabling a second static module is authorized or sufficient. Determine the actual
arm64e dyld-cache CDHash and its module membership and runtime allowSecondStaticTC policy before
proposing a fix. Never merge hashes or bypass trust policy to force acceptance.

**i135 hardware result (2026-11-10):** RAM boot on `42fe0af2`; photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-3032f5cf-40aa-4111-abfd-cb9d14e21672.png`.
No sandbox UNMAPPED lines are visible in the captured screen (not proof none occurred earlier).
AMFI reports `/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64e` is ad-hoc signed,
`unsuitable CT policy 0 for this platform/device, rejecting signature`, and `code signature validation failed`.
Dyld says code signature registration for the shared cache failed, then reports no dyld cache and
missing libSystem. HOLD: owner stage 6 status 0 auth 1 union 1; static trust 1, engineering 0,
loadable 0. Do not bypass AMFI/CoreTrust: investigate actual iOS 24A446 cache signature and the
provenance/content and runtime loading of all three modules in `/tmp/tc-24A446.trustcache`.
Its SHA-256 is `d39802f84695a76c88450bb02d8e496da1ebbab49f76d0e4dc009e0af09ebabb`.
Sheng board requests StaticTrustCache, Cryptex1,SystemTrustCache, and Cryptex1,AppTrustCache;
determine which iPhone modules and cache CDHash are actually present before altering anything.

**i135 candidate (2026-11-10):** i134 RAM boot photo
`/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-badafc7f-9998-4de0-a4b4-7a30ba7be42a.png`
again shows stage 6, status 0, auth 1, union 1, and missing libSystem/no dyld cache.
The clearer photo resolves sandbox return offsets `+3144384` and `+31484e8`, respectively
slots `0xfffffff0081741b8` and `0xfffffff0081743e0`. Original fixups target
`0xfffffff00a8dd0b8` and `0xfffffff00a8dda44`: the former calls current-persona-ID
and tail-calls mapped `_persona_lookup`, matching native `_current_persona_get` at
`0xfffffff00762f184`; the latter checks `PERSONA_MAGIC`, null/invalid returns -101,
and reads UID at offset 0x118, matching native `_persona_get_uid` at `0xfffffff00762f5b8`.
Built i135 with `/tmp/opencode/bind-i135.json`, i134 APFS binding and diagnostic probes.
Extracted shipped slots are `0x80110000037af184` and `0x80110000037af5b8`, pointing to
the respective shipped symbols; APFS binding and previous three bindings also verified.
Image SHA-256 `4a1893df0306c0232b66ad87a6ff5a12707deb5e1d594c41c282fbc7ef22048e`;
`SHA256SUMS` passed. Hardware untested; Windows fastboot showed no device. Do not infer
these fixes resolve dyld until booted.

**i134 APFS binding candidate (2026-11-10):** one apparent i133 photo caller is
`+31d5508` (photo is blurry); shipped i133 instruction at that return address calls the
APFS thunk whose slot `0xfffffff008184c90` originally targeted `0xfffffff00a9419c4`.
That original function is `bti c; b 0xfffffff00a4da6a4`; its branch target implements a
three-argument UPL range operation. XNU source `bsd/kern/ubc_subr.c` identifies
`ubc_upl_range_needed(upl,index,count)` as the corresponding wrapper around
`upl_range_needed`. Native `_ubc_upl_range_needed` is likewise `bti c; b _upl_range_needed`
(native `0xfffffff007681b40` -> `0xfffffff0072e98ec`). Built i134 with reviewed binding
`/tmp/opencode/bind-i134.json`, retaining i133 diagnostic probes. Extracted shipped i134
slot is `0x8011000003801b40` -> `0xfffffff00a805b40`, whose instructions branch to
`0xfffffff00a46d8ec` (`_upl_range_needed`). The three i132 bindings are unchanged and
`SHA256SUMS` passes. Image SHA-256 `0b1c2e5e86f5e908c074ecade708ed16a44f02b7d3193474e6cbbe4d5136d685`.
Not boot-tested: Windows fastboot reports no device. This is one candidate fix, not proof
that the other unresolved calls or dyld issue are fixed.

**i133 hardware result (2026-11-10):** Windows fastboot RAM boot succeeded on `42fe0af2`.
Photo `/home/bluekyeet/.t3/userdata/attachments/73a5300a-0136-44bc-9949-80b5f9f4151a-fbfeb8be-50fc-4016-98c5-3016b5c3fd87.png`
shows `HOLD owner stage 6 status 0 auth 1 union 1 | tc static 1 eng 0 loadable 0`;
the instrumented mount reports success and union authorization, not proof of dyld cache mapping.
Dyld reports `Library not loaded: /usr/lib/libSystem.B.dylib` and `no dyld cache`. The screen
also shows repeated `UNMAPPED` calls, including apparent caller offsets `+3144304`, `+31404e0`,
and `+31d5500` (photo transcription is tentative). Trace their original fixup sites and independently
verify native identities before rebinding; do not assume they caused the cache failure.

Diagnostic i133 was built with `SYSTRACE=1 HOLD_SYNC=1`, retaining i132's mandatory
`STACKSHIFT=1 PAGESHIFT=1 TRUST=/tmp/tc-24A446.trustcache EXTRA_ARGS="sheng-no-lc=1"`.
Image: `staging/amethyst-ios27-i133/Mu-amethyst-ios27-i133.img`, SHA-256
`f1cad56536cb15dbacbee9b2b4f2a4fac5b47fbe482eafed467dab77740d7051`.
Extracted shipped kernel SHA-256 `071f567ae9fee42a22bb265d041a38b60378952615737d434a4e45b6a28fc876`
matches the build kernel. Verified three i132 bindings and the syscall, Mach and sync hook branch
targets, plus the HOLD format string. `SHA256SUMS` passed. No runtime qualification: Windows
`fastboot.exe devices` returned no devices. HOLD intentionally spins at sync and may not be reached;
capture the preceding `OWNERCP`, `VOLDEV`, `PATH`, and `HOLD owner stage ...` lines if visible.

Inspect the shipped image and runtime evidence for the union mount and dyld cache: extract the
kernel from the packed container, confirm the request constant and manifest arguments, then obtain
console lines preceding the initproc failure or surface the existing mount/cache checkpoints.
Do not modify mount flags or UUIDs on the basis of the retracted diagnosis. Continue RAM-only testing.

### Analysis of the remaining 56 unresolved references (complete)

> **Scope note.** This section analyses the 56 unresolved refs of the **`kF` baseline native
> build**, not the shipped i131 image (which reports 321/23). The substantive conclusion — none can
> be fixed by a prelink map entry — was re-checked against the shipped image's own report.

Every one of the 22 distinct targets was checked against its consumer instructions and against
native-side structure. **None can be fixed by a prelink map entry.** Results:

- **6 targets are harmless** (`0x7c2d988`, `0xaf58c7c`, `0xaed4bc8`, `0xaf4dad0`, `0xaef7ce8`,
  `0xaf01438`, 17 refs): code only reads integer-width fields, or never forms the address.
  `0x7c2d988` is `_bootstrap_pagetables+0x988` = `PAGE_SHIFT_CONST`, already handled at code level by
  `pageshift.py`.
- **9 targets are one C++ static blob** (`…IOUserServer…dispatchArray`), not separate objects.
  Element order is compiler-determined; the native kernel has its own differently-shaped arrays.
- **The rest are structurally divergent**: `_dead_mount_store+0x80` (native code switched to an
  `OSAction` metaclass test), `_zone_array+0x9208` (904 `ldr x` sites; native slot unmaintained),
  `_mach_vm_subsystem+0x310` (zero, runtime-populated), and two `kalloc_type_view` tables that share
  a name but have different lengths and field roles.
- **No target dangles**: all 22 remain inside mapped original segments in the emitted image, so the
  failure mode is a stale-but-valid object, not a fault on access.
- **Every target is reached** by kexts loading the slot that holds it with `ldr x` (up to 358 sites
  for `0x7c2d988`). So none can be dismissed as "no consumer" — the distinction is whether the code
  then reads an integer (`ldr w`, safe) or treats it as an object (`ldr x` / `bl` argument).

Key negative results that stop future re-litigation:

- A shared symbol name does not imply a shared object: `_open_maps` exists in both kernels with
  different bodies; `kalloc_type_view_4542` exists in both with different table shapes.
- No constant address shift relates the images (measured over all 1318 paired `kalloc_type_view`
  symbols).
- The 276 stubbed *functions* are whole unpaired functions, not "interior blocks". The matcher
  pairs 9130 of 20907 original functions. Offset-preserving matching is unsound and must not be
  added to the pipeline.

What this means for the i111 wedge: if `0xfffffff00a148a74` still loops after i131 boots, the fix
is **not** more map entries. It needs the semantic-change route — extending the matcher for the
`OSAction`/`OSMetaClass` pattern, or a kernel-source change.

---

## Status as of i109 (2026-11-08, hardware-confirmed)

Three blockers cleared this session, each verified in the shipped image and on the phone
(`fastboot -s 42fe0af2 boot`, RAM-only):

1. **`0xa8d5ddc` kernel data abort (i88–i107) — FIXED in i108.** It was an unapplied format-8
   chained-fixup rebase: `stackshift.py` zeroed the `next` field of slot `0x7ef0db0`, truncating
   DarwinBoot's fixup-walk and orphaning `0x7ef0dc8`. Fix: preserve `next` (see the i107 panic-dump
   section below). `stackshift.py` changed.
2. **launchd killed — Launch Constraint Violation — FIXED in i109.** Boot-arg **`sheng-no-lc=1`**
   (core already ships `zzyc-owner-skip-launch-constraints.patch`, compiled in; `UniXNU/UniXNU-sheng`
   uses the same arg to reach SpringBoard). Added via `EXTRA_ARGS="sheng-no-lc=1"` on `build5.sh`.
3. **`_upl_page_present+0x8` data abort (i109) — FIXED in i111.** `PAGE_SHIFT_CONST` is 0 (the SPTM
   `arm_init_sptm.c` assignment is never run; `native-4k-user-abi.patch` only patched the non-SPTM
   `arm_init.c`). The pager reads it as the runtime page shift, so `(0x8000>>0)-1 = 0x7fff` overran
   the UPL page-list. Its storage `0xfffffff007c2d988` is clobbered at runtime, so `pageshift.py`
   redirects all 64 `PAGE_SHIFT_CONST` GOT slots to a scratch word = 12 (AMFI's slot stays on
   stackshift's scratch = 15). Build with `PAGESHIFT=1`. **On hardware this boots fully into
   userspace: Darwin Ignition Sequence v1.0.0 runs (ignition level 0x5).**
4. **NEW blocker (i111): unmatched prelink functions in Darwin Ignition.** `logstub.py` makes
   `_unixnu_fileset_unmapped` log-and-return-0, so the console fills with `UNMAPPED +<lr-BASE> …`
   (not fatal). Boot wedges: a caller at `0xfffffff00a148a74` (`+0x3144a74`) loops calling an
   unmapped original function (`x0=0x806c`) that returns 0. This is the predicted "1530 unmatched
   functions" measurement. Use `site.py`/`unbound.py` on LR `0xfffffff00a148a74` to get the GOT
   slot + original target + owning fileset entry, bind it (patch-got/BIND json) or extend the
   matcher. nm5 does NOT map these running addresses — symbolize against the original kernelcache.

Superseded detail (kept for addresses): the original "NEW blocker (i109)" write-up —
`_upl_page_present+0x8` data abort, `esr=0x96000007` (translation fault,
   level 3 — not PAC). Backtrace: `… handle_user_abort → vm_fault → vm_fault_internal →
   vm_fault_page → vnode_pager_data_request → vnode_pager_cluster_read → vnode_pagein →
   upl_page_present`. A userspace process page-faulted on a file-backed (APFS) page; `upl_page_present(upl=x0,idx=x1)`
   (`sbfiz x8,x1,#3,#32; ldr w8,[x0,x8]`) read `page_list[idx]` with `idx=0x7fff`, i.e. `[x0+0x3fff8]`;
   the UPL page-list at `x0=0xfffffff5be365490` runs to `+0x40000` but its last page is unmapped
   (`far=0xfffffff5be3a5488`, 8 bytes before the end). `vnode_pagein` iterates `0x8000` pages
   (x20=x21=0x8000) but the page-list allocation is a page short. **Hypothesis: 4K-vs-16K page-size
   UPL/page-list sizing bug** in the file pager (the port's own 4K-page territory). Likely a
   kernel-source fix + CI rebuild, not a post-link binary patch. Candidates to diff against:
   `UniXNU/patches/xnu/native-4k-user-abi.patch`, `native-4k-shared-region-slide.patch`,
   `zzx-pmap-sync-64bit-pa.patch`, and sheng's MWL `mwli_page_size == 4*PAGE_SIZE` relaxation.

Everything below predates this summary; the i107 panic-dump section has the detailed root-cause.

---

# Handoff: iOS 27 24A446 bring-up — AMFI blocker resolved, new blocker is kext loading

Read `README.md` in this directory first for background. **The AMFI/CoreEntitlements entitlement-dict
blocker that earlier revisions of this file were about is fixed** (see below). `/sbin/launchd` now
runs and starts loading kernel extensions; the current blocker is a crash during that, described at
the bottom of this file.

**Immediate next action (i108):** stop probing `x21`. The i107 hardware dump settles it: the
faulting `x21 = 0x0010000003f23480` is a **raw, un-flattened `DYLD_CHAINED_PTR_64_KERNEL_CACHE`
rebase entry**, not a corrupt or clobbered pointer. The fix is to **flatten the kernel collection's
`LC_DYLD_CHAINED_FIXUPS` rebases into canonical VAs before packing** (this is a fixed-base RAM boot
at `0xfffffff007004000`, no KASLR, so a rebase is just `base + (v & 0x3FFFFFFF)`). Read
**"i107 panic dump: the fault is an unapplied chained-fixup pointer"** below.

The one-line summary: `x21` decodes exactly as a format-8 kernel-cache rebase — `isAuth=0`,
`next=2`, `target=0x3f23480` — whose intended value is `base+0x3f23480 = 0xfffffff00af27480` (inside
the OSKext metadata region). At runtime the register held the **packed** form, which only happens
if that slot's fixup was never applied. i107 printed nothing because the probe guard tests
`tbnz x21,#47` and `tbnz x21,#63`, but this value sets only **bit 52** (the `next` field) — a false
negative, not a clean array. The planned "multi-point probe to catch a `blraa` callee clobbering
`x21`" is therefore moot: there is no clobber. Verified against `/tmp/i107.km` (the booted kernel):
it still carries `LC_DYLD_CHAINED_FIXUPS` over `__DATA_CONST`/`__DATA`/`__KLDDATA`/`__BOOTDATA`, and
`__DATA` is full of packed rebase entries of this shape.

## The AMFI blocker: what it was, and the fix

`AMFI: failed to create OSDictionary copy: 14` at exec was caused by one mis-set constant, not a
code-signing or entitlements-delivery problem. AppleMobileFileIntegrity's GOT slot for Apple's
`PAGE_SHIFT_CONST` global was still bound to Apple's own value. CoreEntitlements' DER iterator reads
that slot as a stack-depth sanity check: `sp & ((1 << shift) - 1) < 0x80`, assuming the stack is
aligned to `1 << shift`. That's true on Apple's 16 KiB-page kernel (`shift == 14`, with 32 KiB guard
pages below the stack). Our native kernel uses 4 KiB pages and starts its stack 8 KiB into a 32
KiB-aligned allocation, so checking against a 16 KiB mask gives false "out of stack" positives at
ordinary call depths — the exact, literal mechanism behind the `OSDictionary copy: 14` error.

**This bug, and its fix, were already found and documented upstream** — not derived from scratch here.
`UniXNU/XnuPrelink`'s `fixtures/fileset-24A446-symbols.json` has a `sites` entry describing it almost
word for word, including the fix: rebind the slot to the value `15` (the real allocation-alignment
shift) instead of Apple's `14`. That fixture is for a different device's kernelcache (iPad17,3, not
our iPhone15,4/d37ap one — different `kernelcache_sha256`), so its addresses don't transfer directly,
but the mechanism does. The equivalent code was located directly in our own kernelcache by searching
for the `sp & mask < 0x80` instruction pattern inside AMFI's text:

```
kc 0xfffffff008f7c838   mov  x9, sp
kc 0xfffffff008f7c83c   and  x8, x8, x9
kc 0xfffffff008f7c840   cmp  x8, #0x80
kc 0xfffffff008f7c844   b.lo <error path>
```

Its GOT slot is at kernelcache `0xfffffff007ef0db0` (traced back via the `adrp`/`ldr` pair at
`0xf7c820`/`0xf7c824`), bound in every one of our builds to Apple's own `PAGE_SHIFT_CONST` location.
`stackshift.py` rebinds it: writes the raw value `15` into an unused scratch word in the panic-cave
region, then repoints the GOT slot at that word (a plain non-auth rebase, same encoding used
elsewhere in this bring-up). No kernel source rebuild needed — this is a GOT-slot binary patch, same
category as everything else in `bind.json`/`got-override.json`.

**Confirmed fixed on hardware (i88):** booted with `STACKSHIFT=1` plus the `CEFAIL`/`CEIT` diagnostic
probes still active from `ceprobe.py`. Neither probe fired — the entitlement-dict build no longer
fails. The boot proceeded to a new, later, unrelated crash (below), which only happens once launchd
is genuinely running and processing further work. `STACKSHIFT=1` should be a permanent part of every
build from here; there is no reason to omit it.

**Lesson for next time:** before spending effort re-deriving a fix by reading disassembly or kernel
source from scratch, check `UniXNU/XnuPrelink`'s `fixtures/*-symbols.json` for the relevant kernelcache
build, and check whether another UniXNU device repo (e.g. `UniXNU/UniXNU-sheng`) has already booted
the same OS build successfully — their patches, prelink inputs and reports are often the fastest path
to a real fix, faster than re-deriving one independently.

## Current blocker: a link-field walk in OSKext bootstrap dereferences an invalid pointer (NOT safeMetaCast, NOT PAC)

**Superseded diagnosis -- read this section first.** The old text of this section claimed the crash
was `safeMetaCast` faulting on `x0 == 0xdc008005` and, later, a PAC/discriminator failure. Both are
retracted; the evidence for each is recorded below so neither is re-derived.

What a real panic dump (image i103, EL1 sync-exception-vector probe) actually shows:

```
esr: 0x96000004          EC 0x25 = Data Abort from same EL, DFSC 0x04 = translation fault, level 0
far: 0x0010000003f23490  the unmapped address actually accessed
pc:  0xfffffff00a8d5ddc  the faulting instruction
x21: 0x0010000003f23480  the base pointer -- invalid
```

The instruction at `pc` is, in context (`0xfffffff00a8d5ddc` is inside OSMetaClass's
instance-array walk, a private/unsymbolised helper just below `OSMetaClass::applyToInstances`
at `0xa8d630c`):

```
0xa8d5d74:  ldr  x21, [x8, x26, lsl #3]   ; x21 = pointer_array[x26]
0xa8d5d78:  ldr  x0,  [x27, #0x2a0]
0xa8d5d7c:  ldr  x1,  [x21, #0x18]
   ...      blraa                          ; call #1
0xa8d5da4:  cbz  x0, 0xa8d5dd8             ; call #1 returned NULL in the i106 dump
...
0xa8d5dd8:  mov  w8, #0
0xa8d5ddc:  ldr  x21, [x21, #0x10]         <-- pc; far == x21 + 0x10 exactly
0xa8d5de0:  add  w8, w8, #1
0xa8d5de4:  cbnz x21, 0xa8d5ddc            ; walk the +0x10 link chain while non-null
```

(An earlier revision of this block listed the array load at `0xa8d5d78`; that was off by one
instruction. Verified against the extracted bytes.)

So the crash is a **linked-list walk over `+0x10` links**, and the base `x21` is an element of a
pointer array: `x8 = [x23+0x298]`, array base `= [x8+0x18]`, `x21 = [base + x26*8]` with
`x23 = 0xfffffff00af67000`. The array element itself is the bad value. `far - x21 == 0x10` matches
the instruction, so the dump is self-consistent and can be trusted.

**The indirect call just before it returned NULL.** In the i106 dump `x0 == 0` at `0xa8d5ddc`, and
`x22 == 0`, which means the `cbz x0` at `0xa8d5da4` was taken -- so the `blraa` at `0xa8d5da0`
returned 0. That is consistent with `x21` being an invalid element: the call was made through a
pointer derived from the bad element and yielded nothing. Whether the NULL return is a *cause* or a
*consequence* of the bad `x21` is what i107 is meant to separate.

Note there IS an `autda`/`blraa` PAC pair earlier in the same function (`0xa8d5d90`/`0xa8d5da4`, and
again at `0xa8d5dbc`/`0xa8d5dc8`), and its return value is walked with `ldr x0,[x0,#0x10]`. But that
loop exits before `pc`; the faulting walk is the second one, over `x21`. Do not describe the crash as
a PAC failure -- the PAC calls execute and return.

`x21`'s shape is the clue: it is the ONLY pointer in the dump whose top 16 bits are not `0xffff`
(`x7/x11/x19/x20/x23/x24/x25/x27` are all `0xffff`; `x21` is `0x0010`). Its low 32 bits
(`0x03f23480`) look plausible; the high half does not. That is the signature of an array slot holding
something that was never a kernel pointer -- NOT of an incorrectly signed vtable.

`x21` matches neither of the magic values this function tests (it compares `w21` against `0xdc004009`
at `0xa8d5e1c` and does a `w21 + 0x23ffbff6 < 2` range test at `0xa8d5e08`), so it is stray data
rather than a tag/sentinel. Note `0xdc004009` is in the same `0xdc00xxxx` family as the discredited
`0xdc008005`: whatever the earlier session read, the `0xdc00` prefix is a value THIS code compares
against, and should never have been interpreted as a pointer.

Note `far` is neither `0xfffffff0_03f23480` nor `0xffff000003f23480`, so it is NOT a plain 48-bit
sign-extension of the low bits either. Do not assume the exact transformation; the open question is
why the array slot at `[x8+0x18] + x26*8` holds a non-pointer, which is what the next probe must
answer.

### Retractions (do not re-derive these)

- `x0 == 0xdc008005` at `safeMetaCast`: **unverified**. It came from an earlier session with no
  recorded provenance (no register dump, no log). Every value since re-read from a photo of the
  screen (`...627361`, `...fifrrrff`) turned out to be OCR noise. The thing was measured properly in
  image i102 with a probe that renders `x0` at `safeMetaCast+0x1c` and it was
  **`0xfffffff73ef4c858`** -- a valid kernel pointer. Drop `0xdc008005` entirely.
- "The global `sAllKextLoadIdentifiers` is corrupted": **wrong**. Probes at component-kext loop
  entry and at the call site showed the same healthy stack-shaped value `0xfffffff4e47ff200` at both
  points (image i99). The global is not the problem.
- "The fault is a failed PAC check (`autda`/`blraa`)": **not supported**. The real `pc` is a plain
  `ldr` of a link field ~0x1fcc bytes away from `safeMetaCast`'s PAC sequence; the PAC calls that do
  appear in the faulting function execute and return normally before `pc` is reached.
- "`0xdc008005` is a garbage object pointer": **discredited in a second way**. The faulting function
  itself compares `w21` against `0xdc004009` and does a `w21 + 0x23ffbff6 < 2` range check, i.e.
  `0xdc00xxxx` is a value-domain this code handles deliberately, not an address.

### Probe history that produced the above (all in this directory)

`pacprobe.py` renders `x0` at `safeMetaCast+0x1c` and hangs; `excprobe.py` hooks the EL1 sync
exception vector (`_ExceptionVectorsBase + 0x200`, entry 4) and renders
`ESR/ELR/FAR/SPSR/x0/x1/sp`, which is what produced the dump quoted above. Note `excprobe.py` hangs
on ANY EL1 sync exception, so it changes boot behaviour and is not a neutral observer.

Two instrumentation lessons, both of which cost a hardware boot:

- A `str xN, [xBUFFER]` of a 4-character literal writes 8 bytes and puts a NUL right after the
  text, truncating the C string passed to `IOLog` (hit twice: i100, i101). Store with `str wN`.
- An emptiness assert placed AFTER the writes it guards always fails (hit once, `excprobe.py`).

A third, which cost three builds and produced a fake diagnosis (i104/i105/i106):

- **A probe script that shadows its own filename is a trap.** `arrayprobe.py` ends with
  `open(k,'wb').write(d)`, where `k` is the kernel path from `sys.argv[1]`. A later patch loop
  was written `for k, bit in enumerate((47, 63))`, rebinding `k` to an int. The write raised
  `TypeError`, and Python's traceback printer then tried to render the local `d` -- a 79 MB
  `bytearray` -- into the error message, so the process printed the entire kernel image to
  stdout and exited 120. Reading that as "the script emitted garbage, `enc()` must be broken"
  sent me chasing a non-existent assembler bug. **Read stderr separately from stdout**
  (`>/f.out 2>/f.err`) and look for the `Traceback` header before believing the output.

`tbnz`/`tbz` genuinely cannot go through `enc()` (llvm-mc emits a `fixup_aarch64_pcrel_branch14`
relocation, not a byte pattern), but that was a real, separate bug. Its encoding, verified by
assembling `tbnz x21,#47,.+8` and reading the word back (`0xb7780055`), is: **b5 is bit 31, above
the 6-bit opcode**, `0b110111` at bits 30..25, `op`=1 at bit 24, b40 at 23..19, imm14 at 18..5,
Rt at 4..0. Putting b5 into a base constant that already held the opcode yields
`tbnz xzr,#0x20` -- silently wrong, and it disassembles without complaint.

A fourth: **the rejoin must be `b`, never `bl`.** `arrayprobe.py` at one point wrote
`put(rejoin_va, bl(rejoin_va, RESUME))`. That reaches the right address but leaves `x30`/`lr`
pointing into the cave, so every healthy iteration silently corrupts the caller's return address.
i104/i105 did it correctly with `(bl(...) & ~0xfc000000) | 0x14000000`; i106 regressed. Caught by
diffing the rejoin word between images (`0x17f72164` = B, correct; `0x97f72166` = BL, wrong) --
which is a cheap check worth doing on every probe that has a benign path. i106 also destroyed
`x8`/`x9` in its render loop, and `x9` is the array loop's count; the current `arrayprobe.py`
saves and restores `x8,x9,x0,x1` around the report path for that reason. **`probe-lint.py` now
enforces both** (`check_cave_code`): it rejects a rejoin written as `bl(...)` and rejects a render
path that scratches `w9` without an `stp`/`ldp` save-restore. Confirmed by re-injecting the i106
line into a copy: lint fails, and the real probe passes.

---

## i107 panic dump: the fault is an unapplied chained-fixup pointer (authoritative)

This supersedes the `x21`-clobber investigation. i107 booted on hardware, passed FIPS POST, ran
`launchd`, and took the same `Kernel data abort at 0xfffffff00a8d5ddc` — but **printed no `ARRY`
line**. The last session read that silence as "the array is clean" and planned a multi-point probe
to test whether a `blraa` callee clobbers `x21`. Both readings are wrong. The register dump itself,
checked against the booted kernel `/tmp/i107.km`, gives the answer.

**The faulting value is a raw chained-fixup rebase.** `x21 = 0x0010000003f23480`. The booted
kernel.macho stores `__DATA` pointers in `DYLD_CHAINED_PTR_64_KERNEL_CACHE` (ptr_format 8) form, so
decode `x21` as that:

```
isAuth (bit 63)      = 0        -> plain rebase, NOT auth, so this is not a PAC failure
next   (bits 62:51)  = 2
target (bits 29:0)   = 0x3f23480
```

Intended pointer = `base + target = 0xfffffff007004000 + 0x3f23480 = 0xfffffff00af27480`, which is
inside the OSKext metadata region (`x23=x27=x28=0xfffffff00af67000`, `x25=0xfffffff00af67270`,
`x24=0xfffffff00ae8d000`). The loop dereferenced the **packed** entry at `[x21+0x10]`
(`far == x21+0x10` exactly) — an unmapped address — and aborted. A correctly-booted XNU would have
`0xfffffff00af27480` in that register; the packed form can only be there if the fixup for that slot
was never applied.

**Confirmed in the binary (not inferred).** `/tmp/i107.km` (the exact kernel pulled from the shipped
container, 79,937,536 bytes) still carries `LC_DYLD_CHAINED_FIXUPS` (1224 bytes), with format-8
chains over segments `__DATA_CONST`, `__DATA`, `__KLDDATA`, `__BOOTDATA`. `__DATA` (fileoff
`0x3e74000`, va `0xfffffff00ae78000`) is full of qwords of exactly this shape. So the collection is
shipped expecting a loader pass to flatten the rebases, and for the slot behind this array element
that pass did not run.

**Why the probe was blind.** The guard is `tbnz x21,#47` / `tbnz x21,#63`. In `0x0010000003f23480`
**only bit 52 is set** (it is the low bit of the `next` field). Neither guard bit is set, so the
guard falls through to the benign path and prints nothing. The silence is a **false negative**. Any
future guard for this class must instead test "is this a canonical kernel VA?" — report when
`(x21 >> 32) != 0xfffffff0`. That would have fired loudly on i107.

**Retractions.** (1) "i107 silence means the array is clean" — withdrawn; the guard cannot see an
unapplied rebase. (2) "the next step is a multi-point probe to see whether a `blraa` callee clobbers
`x21`" — withdrawn; `x21` is a stored fixup entry at the hook, the fault, and every point between;
there is no clobber and no PAC auth (`isAuth=0`). (3) The whole "link-field walk dereferences an
invalid pointer" framing in the section below is correct about the instruction but wrong about the
cause: the pointer is invalid because it was never flattened, not because a kext wrote garbage.

**The fix (i108) — SHIPPED AND CONFIRMED ON HARDWARE.** The investigation that followed pinned the
cause to a single orphaned slot: `0xfffffff007ef0dc8` was packed-raw because the chain that should
reach it was **truncated by `stackshift.py`**. In the pristine kernel the `__DATA_CONST` chain runs
`…0x7ef0db0 → 0x7ef0db8 → … → 0x7ef0dc8 → …` (`next=2` at each step). stackshift retargeted the GOT
slot `0x7ef0db0` by writing `new = 0x1000000 | offset`, which zeroes the `next` field, so DarwinBoot's
walk stops dead there and every fixup after it on that page (29 slots, incl. `0x7ef0dc8`) is never
applied. So no full flattener was needed — the one-line fix is to **preserve `next`** when retargeting
the slot, with the exact `(cur & ~0x3fffffff) | ((SCRATCH-BASE)&0x3fffffff)` idiom that
`patch-got.py`/`patch-data.py` already use (they were never buggy; only stackshift was). i108
verified in the shipped kernel: slot back to `next=2`, orphan re-threaded, walked `__DATA_CONST`
count restored to the pristine 119963 (i107 was 119934), AMFI scratch value 15 still applied. On
hardware the `0xa8d5ddc` abort is **gone** — the kernel boots through kext loading and IOKit.

(A general flattener was the first idea and is still viable, but unnecessary once the single
truncating writer was found. General lesson: any patch that retargets a chained-fixup slot MUST
preserve its `next` field, or it orphans every fixup after it on that page.)

---

## i106 panic dump: what it proves, and why i106 is a null result

Dump supplied from hardware (i106, `STACKSHIFT` + trust cache + `ARRAYPROBE`):

```
pc  0xfffffff00a8d5ddc   esr 0x96000004 (data abort, translation fault L0)
lr  0x3ac853b00a8d5da4 -> strip tag -> 0xa8d5da4
far 0x0010000003f23490          x21 0x0010000003f23480   (far == x21 + 0x10)
x0  0            x8  1            x9  0x64        x10 0x64       x22 0
x23 0xfffffff00af67000            x25 0xfffffff00af67270 (= x23+0x270)
x26 0                x27 0xfffffff00af67000           x28 0xfffffff00af67000
```

`0xa8d5ddc` is `ldr x21,[x21,#0x10]` (`0xf9400ab5`; note the imm12 of 2 is **scaled by 8**, giving
+0x10 -- an easy decode trap). Since `far == x21+0x10` exactly, `x21` was already
`0x0010000003f23480` when the instruction executed; it is not a kernel address (kernel VAs here
are `0xfffffff0..`/`0xfffffff8..`).

The function is a **tag-dispatch loop**, entered at `0xa8d5c38`:

```
0xa8d5c38  ldr  w9,[x8,#8]           ; x9 = a descriptor field
0xa8d5c3c  cbz  w9, <exit>
0xa8d5c48  mov  x21,x9               ; x21 = that 32-bit VALUE, not a pointer
0xa8d5c54  cmp  w21,#0xdc004008      ; <-- DISPATCH TOP; also the re-entry point
   ... tag branches to 0xa8d319c / 0xa8d3808 / 0xa8d7df4 / ...
0xa8d5d38  ldr  x8,[x23,#0x298]      ; the array walk (also a re-entry point)
0xa8d5d44  mov  x26,#0
0xa8d5d58  b    0xa8d5d70            ; enter loop body, skipping the bound check
0xa8d5d74  ldr  x21,[x8,x26,lsl#3]   ; x21 = array[x26]
0xa8d5d7c  ldr  x1,[x21,#0x18]       ; <-- MY HOOK (bl to cave)
0xa8d5d80  ldr  x16,[x0]
   ...     autda / ldr x8 / blraa     ; call #1
0xa8d5da4  cbz  x0, 0xa8d5dd8        ; x0==0 -> jump STRAIGHT to the fault loop
0xa8d5da8  mov  x22,x0
0xa8d5dac  ... chain walk ...        ; call #2, ldr x0,[x0,#0x10]
0xa8d5dd4  cbnz x0, 0xa8d5dac        ; loop while next link != 0
0xa8d5dd8  mov  w8,#0
0xa8d5ddc  ldr  x21,[x21,#0x10]      ; <-- FAULT
0xa8d5de4  cbnz x21, 0xa8d5ddc       ; walk the +0x10 chain, counting depth in w8
```

**`cbz`/`cbnz` decoding trap (cost a whole session's worth of wrong conclusions).** The imm19 field
is at bits 23..5 of the word, and it is **sign-extended**. Extracting it with the wrong shift, or
failing to sign-extend, produces plausible-looking but wrong targets -- and because
`0xa8d5da4+0x680 = 0xa8d6424` is a real in-image address, the wrong answer does not obviously look
wrong. Every branch target in this file must be confirmed with
`llvm-objdump -d --no-show-raw-insn` on the extracted bytes, exactly as the `tbnz` encoder had to
be. The verified targets here are: `0xa8d5da4 cbz x0 -> 0xa8d5dd8`,
`0xa8d5dd4 cbnz x0 -> 0xa8d5dac`, `0xa8d5de4 cbnz x21 -> 0xa8d5ddc`,
`0xa8d5dcc b.eq -> 0xa8d9d4c`.

**The hook DID run on the faulting pass.** Both ways of reaching `0xa8d5dd8` -- the short
`cbz x0` at `0xa8d5da4` and the long chain walk through `0xa8d5dac`/`0xa8d5dd4` -- are downstream
of `0xa8d5d7c`. Nothing between `0xa8d5d7c` and `0xa8d5ddc` writes `x21` (the chain walk touches
only `x0`, `x16`, `x17`, `x8`; `cbnz`/`b.eq`/`cmp` write no GPR). So `x21` at the fault is the
same value the hook saw.

`x22 == 0` **is** informative, just not about a skipped prefix: it means the `cbz x0` at
`0xa8d5da4` was taken, i.e. **the indirect call at `0xa8d5da0` returned 0**. That is why control went
straight to `0xa8d5dd8` and never executed `mov x22,x0` at `0xa8d5da8`. So the faulting pass is the
short path: hook -> `0xa8d5d7c` -> call #1 -> returns NULL -> `0xa8d5dd8` -> fault. The
`0xa8d5da8`..`0xa8d5dd4` chain walk was not executed at all on this pass.

**`x10`/`x11` are not evidence either.** Both are caller-saved and both are clobbered by the two
`blraa` calls on this path, so their values say nothing about whether the cave ran. `x12 = RENDER`
and `x13 = "ARRY"` *are* cave constants (in the unmodified kernel both addresses are zero), so the
cave was entered at least once.

**Conclusion: i106's silence is explained by i106's own defect, not by a skipped path.** The
benign rejoin was written as `bl` instead of `b`, so on every healthy iteration `x30`/`lr` was
overwritten with a cave address. That is a live corruption of the function's return address, and it
makes the whole boot untrustworthy -- including whether the report path could ever complete. i106
is a **null result** and must not be reused.

The earlier claim in this section -- that the fault was reached by re-entering the dispatch loop at
`0xa8d5c54` without reloading `x21` -- is **withdrawn**. It rested on the mis-decoded `cbz`/`cbnz`
targets above. Likewise the earlier "the probe measures the wrong path" title was wrong.

---

## Older analysis retained below for the addresses only; its fault narrative is superseded

With `STACKSHIFT=1`, boot reaches very early kext bootstrap (before launchd, in fact —
`KLDBootstrap::readStartupExtensions` is one of the first things that runs) and crashes in
`OSMetaClassBase::safeMetaCast`, dereferencing an object pointer that's garbage
(`0xdc008005` -- not null, not a valid kernel address, which are always `0xfffffff0xxxxxxxx`).

Call chain (addresses are `k.macho`, the kernel we build and boot — see the two-binary warning below
the chain):
```
KLDBootstrap::readStartupExtensions            0xfffffff00ab0d2c0-ish (from IOCatalogue block invoke)
OSKext::loadKextWithIdentifier(OSString*,...)  0xfffffff00a8e9b1c
OSKext::recordIdentifierRequest(OSString*)     0xfffffff00a8e9fec  +0x30  <- virtual call through [x0,#272]
OSMetaClassBase::safeMetaCast                  0xfffffff00a8d4e10  +0x1c  <- fault (x0 == 0xdc008005)
```

The faulting operation is a **virtual call, not a `safeMetaCast` call site**: `recordIdentifierRequest`
does `ldr x0,[x22,#1312]` (the global), null-checks it, then `ldr x16,[x0]` / `autda` / `ldr x8,[x16,#272]`
/ `blraa`. With `x0 == 0xdc008005` the `ldr x16,[x0]` itself faults. The global's own null guard
(`cbz x8, <finish>` at `0xa8ea010`) is present and correct, so by the time we get there the slot holds a
**non-null garbage** value, not null.

**The identifier being loaded, read directly off the device: `com.apple.kpi.bsd`** (a `kextnameprobe.py`
+ `badcastfreeze.py` pair stashed it at the real `loadKextWithIdentifier` entry and rendered it as
plain hex-digit text at the crash site — see "How this was found" below; getting a readable string
out of a frozen panic screen took three iterations because `IOLog`'s `%llx`/`%s` handling is
unreliable here, also documented below).

**Root cause, from reading `libkern/c++/OSKext.cpp` in the reference XNU tree (caveat: this is the
17.7/`xnu-10063` tree, not 24A446's `xnu-12377` — re-check against the real one if it's available,
same caveat as everywhere else in this bring-up):** `recordIdentifierRequest` reads a static global,
`sAllKextLoadIdentifiers` (an `OSSharedPtr<OSSet>`), set exactly once, in `OSKext::initialize()`
(`iokit/Kernel/IOStartIOKit.cpp`, `StartIOKit` → `OSKext::initialize()`), via
`sAllKextLoadIdentifiers = OSSet::withCapacity(kOSKextTypicalLoadCount);`. If that assignment had
simply never run, the pointer would still read as null, `recordIdentifierRequest`'s own
`if (!sAllKextLoadIdentifiers || ...) goto finish;` guard would catch it safely, and there would be
no crash. A **non-null but invalid** value (`0xdc008005`) means something did write to that global's
storage — just not a valid `OSSet*` — which points at either: `OSKext::initialize()` running with
something already broken underneath it (e.g. the allocator it calls through), or a different global
aliasing/overwriting this one's storage (the kind of prelink/binding mistake this bring-up has found
several times already, though this is plain native `com.apple.kernel` code, not a retained-kext GOT
slot, so it wouldn't be the *same* class of bug as the earlier ones).

**Read the addresses in this file section with care.** Everything above this line was traced against
the wrong binary. There are two completely different kernels in this bring-up and they do not share a
layout:

| | Apple's `kernelcache.decompressed` (24A446, from the IPSW) | **`k.macho`** (built in `.scratch/ios27-run5`, and the thing `build5.sh` actually patches) |
| --- | --- | --- |
| `OSKext::recordIdentifierRequest` | `0xfffffff00aa787ec` | **`0xfffffff00a8e9fec`** |
| `OSKext::initialize` | `0xfffffff00aa7440c` | **`0xfffffff00a8e0480`** |
| `OSMetaClassBase::safeMetaCast` | `0xfffffff00aa4ebc0` | **`0xfffffff00a8d4e10`** |
| `sAllKextLoadIdentifiers` | `0xfffffff00af99ed0` | **`0xfffffff00af67520`** |
| `__TEXT_EXEC` size | `0x2a44000` | `0x2930000` |

The probes patch `k.macho` (`patch-got.py`/`patch-data.py` produce it from the kc, but every probe
edits `k.macho` afterwards), so **all probe `SITE`/`GLOBAL`/`CAVE` addresses must be `k.macho`
addresses**. The per-probe `assert W(SITE)==<expected instruction>` guards are what catch this: all
six probes' asserts match `k.macho` and all six fail against the kc. If an assert ever fires, check
which binary you decoded from before changing the constant — the constant is probably right and the
disassembly you did it from was of the other kernel.

Cross-check that settles it, without needing to trust either address: in `k.macho`,
`OSKext::recordIdentifierRequest` (`0xa8e9fec`) is `adrp x22, <page>` + `ldr x8, [x22, #1312]` →
`0xfffffff00af67520`, and inside `OSKext::initialize` (`0xa8e0480`) the instruction at
`0xfffffff00a8e0664` is `str x19, [x22, #1312]`, storing the `OSSet` that `OSSet::withCapacity(150)`
just returned (`mov x19, x0` at `0xa8e0638`). Same slot, written once, read by the crashing function —
that is `sAllKextLoadIdentifiers`, in the binary we build.

**Both binaries are internally consistent; neither address is "wrong".** A register-tracking scan
(`adrp` target propagated until that register is clobbered, then an `ldr`/`str` with the matching
offset) gives, for `k.macho` on `0xaf67520`, exactly six references, all credible:

```
0xfffffff00a8e0640  ldr x0  OSKext::initialize +0x1c0        (the null check before re-creating)
0xfffffff00a8e0664  str x19 OSKext::initialize +0x1e4        (the single assignment)
0xfffffff00a8ea00c  ldr x8  OSKext::recordIdentifierRequest +0x20   (the guard -- the crash's source)
0xfffffff00a8ea030  ldr x0  OSKext::recordIdentifierRequest +0x44
0xfffffff00a8ea060  ldr x0  OSKext::recordIdentifierRequest +0x74
0xfffffff00a8f2c94  ldr x27 OSKext::handleRequest +0x1250
```

and for the kc on `0xaf99ed0`, exactly four, the same functions at the kc's own addresses
(`OSKext::initialize +0x4e4`, `recordIdentifierRequest +0x24`, etc.); each address has zero
references in the other binary. So 0xaf67520 is `k.macho`'s slot and 0xaf99ed0 is the kc's slot, and
a probe must use the one for the binary it edits. The bare-address scans done earlier in this
bring-up (looking for a raw `<Q` pointer, or for any `adrp`+`offset` pair) were unreliable in both
directions: they cannot see `adrp`-plus-register-offset addressing, and without register tracking they
report unrelated instructions that happen to share a page.

**`OSKext::initialize()` does get called in our build, so the global is not simply never set.** In
`k.macho` the only caller is `_iokit_post_constructor_init +0x28` (`0xfffffff00a93b450`), which calls
`OSKext::initialize` (`0xa8e0480`) unconditionally as part of IOKit's constructor init. That rules out
the "the assignment never ran, so the slot is uninitialised" theory outright — and it means the
`0xdc008005` in the crash is a *later* corrupting write (or a bogus pointer being stored), which is
the more interesting branch of the fork this section has been narrowing.

**Not yet investigated:**
- Whether `OSKext::initialize()` (called from `StartIOKit`) actually runs to completion before
  `loadKernelComponentKexts` does, in our build specifically — add a probe right after
  `OSKext::initialize()` returns that reads `sAllKextLoadIdentifiers`'s value directly and logs it
  (same hex-render-to-text technique as `badcastfreeze.py`). If it's already garbage there, the bug
  is in `initialize()` or something it calls; if it's still correctly null/fine there and only
  becomes `0xdc008005` later, something *else* overwrites it in between, which is a very different
  (and probably more interesting) bug.
- Whether `UniXNU/UniXNU-sheng` (which did boot this exact OS build to SpringBoard) hit the same
  thing — their build succeeded, so whatever's different about their `OSKext::initialize()` path is
  worth a direct comparison. Nothing specific was found in their patches on a first pass (`git grep`
  for `OSKext::initialize`/`sAllKextLoadIdentifiers`/`sKernelComponentNames` across `upstream-unixnu`,
  `UniXNU-sheng`, and `XnuPrelink` came up empty), but that doesn't rule out a difference in ordinary
  (unpatched) native kernel behavior between their build and ours.

### How this was found: getting a readable string out of a frozen panic screen

Three real dead ends on the way here, each worth knowing about before trying again:
1. **`%llx`/`%s` through `IOLog` is unreliable for this kind of value.** Pointer-shaped arguments
   printed as the literal string `<ptr>` (confirmed on-device, not a blurry photo). Masking to 32 bits
   first didn't help (rules out a magnitude heuristic). A 4-argument attempt came back completely
   scrambled — one slot showed a *different* argument's real value, i.e. redaction here can break
   argument correspondence itself, not just blank one value.
2. **Calling `_kprintf` instead of `_IOLog`** did stop the redaction for what little could be read,
   but caused a reproducible crash much earlier in boot (confirmed on two identical boots). Root
   cause not found; not worth re-attempting without a specific new reason to.
3. **Writing data into the `__TEXT_EXEC` panic-cave region at runtime faults.** That region is r-x
   only even though it's where every *build-time*-baked probe (format strings, patched instructions)
   lives safely — the difference is build-time bytes vs. a runtime `str`. Needed an actual writable
   scratch location: the last 64–128 bytes of `__DATA` (zero-fill padding at the segment's page-aligned
   tail) works.
4. **Pre-filling that writable scratch buffer with literal text at *build* time hung the boot before
   even the first kernel log line** (`iBoot version:` / `iBoot Stage 2 version:`, far earlier than
   any previous failure in this bring-up). Something very early in boot doesn't tolerate pre-set
   content in that `__DATA` tail. Fix: leave the buffer entirely zero at build time (same as every
   other scratch location used successfully elsewhere) and have the probe's own runtime code copy the
   static text in too, from a small literal embedded in its own (read-only) code cave.

What worked, and is the pattern worth reusing for the next probe: `kextnameprobe.py` patches
`loadKextWithIdentifier`'s real entry, calls `OSString::getCStringNoCopy()` on the incoming
identifier, and silently copies 20 raw bytes into a writable `__DATA` scratch buffer on *every* kext
load attempt (cheap, no printing, too fast to ever photograph anyway). `badcastfreeze.py` patches the
exact instruction that's known to fault, *conditionally* — only when the pointer being dereferenced
has zero upper 32 bits (real kernel pointers never do), so it doesn't trip on the thousands of
legitimate uses of the same function elsewhere in the kernel — and on that path, hex-renders the
stashed identifier as plain ASCII text entirely at runtime (no `%`-conversions reach `IOLog` at all)
before hanging so the screen stays readable.

## Build state to resume from

- `bind.json` (456 entries), `data.json`, `got-override.json` (4 entries, includes the `0xa8a5f7c` →
  `_cs_system_require_lv` and `0xa8a62b0` → `_csblob_get_teamid` map-error corrections), `stackshift.py`
  (new, required) are all current in this directory.
- `build5.sh N BIND DATA` env vars: `STACKSHIFT=1` (new, required), `TRUST=<trust-cache bundle path>`
  (regenerate with `tcparse.py`/`adtaddtc.py` from a local 24A446 IPSW — not committed), `HANG=
  "0xfffffff00a1c1604 0xfffffff00a7a6b70"` (keeps the log on screen past two earlier panic sites;
  still harmless to include), `CEPROBE=1` optional now (the entitlement-dict failure it watches for
  no longer happens, but it's inert/harmless to leave on). No `CSBYPASS`, no `MXPROBE` (`_kprintf`
  caused an unrelated, reproducible early-boot regression — see README.md; don't retry it).
- RAM-boot only: `fastboot -s <serial> boot <image>`. Never flash, erase, `set_active`, or write
  Android userdata.
- `GOT_OVERRIDE=<abs path to got-override.json>` is **required** when building: `patch-got.py` errors
  out without it, and the `grep` in `build5.sh`'s pipeline used to mask that failure (exit 0, no
  image produced). Always pass absolute paths for `BIND`/`DATA`/`GOT_OVERRIDE`; `build5.sh` `cd`s
  into `.scratch/ios27-run5` first.
- Cave map (all inside `__TEXT_EXEC`, `k.macho`). The **only** zero runs >= 0x300 in that segment are
  `0xab0d1e4..0xab10000` (0x2e1c), `0xab11688..0xab14000` (0x2978), and four small stubs near
  `0x4d4xxx` (<= 0xc95) used by `tcfix`/`stackshift`. The page at `0xab10000` is **not** free (3710
  nonzero bytes). Current allocation of run 1: `pacprobe` `0xab0d1e4..0xab0d4a4`, `arrayprobe`
  `0xab0d4c0..0xab0d800`, `excprobe` `0xab0d800..0xab10000`. Run 2: `0xab11688..0xab11700` free,
  then the panic-screen cave `0xab11700..0xab13ff0`, then `lkcentrystash` `0xab12160`,
  `logstub` `0xab12400`, `pmgrprobe` `0xab12b00`, `ceprobe` `0xab13400..0xab13800`, `tcfix`
  `0xab13800`, `stackshift` `0xab13900`, `kextnameprobe` `0xab13a00`.
- **Probe caves must be linted before every build.** `probe-lint.py <probe-dir> <probes…>
  --kernel k.macho` parses each probe's `CAVE`/`CAVE_SPAN`/scratch constants without executing it,
  reports overlaps between probes, checks scratch slots don't collide, and (with `--kernel`) proves
  each cave is all-zero in the actual image. `build5.sh` runs it automatically for whichever probes
  were requested. Two probes quietly overlapping caves cost a boot each time; this is now a
  build-time failure instead.
- `arrayprobe` emits 0x330 bytes and is the tightest fit: it must stay under the 0x340 gap between
  `pacprobe` and `excprobe`. Its hex renderer uses a 16-byte `'0'..'f'` table, which lives in
  `__TEXT_EXEC` beside the cave (see i105/i106 below), not `__DATA`, because three 16-digit fields
  at ~12 instructions per byte would not otherwise fit.
- The panic-screen cave is written by `patch-xnu-entry-probe-cave.py` **before** any probe runs, so
  `0xab11700..0xab13ff0` is permanently unusable by probes. `ceprobe`'s caves currently sit inside
  it; that is a **pre-existing latent bug**, reported as a hazard by `probe-lint.py`, and is
  harmless only because `CEPROBE=1` is not enabled in the current build. Do not enable `CEPROBE`
  together with the panic screen until ceprobe's caves are relocated.
- i99 image (trust cache + `STACKSHIFT` + `CEPROBE` + all three kext probes) verified offline: each
  site's `bl` decodes to its own cave, and every cave holds code. `staging/amethyst-ios27-i99/`.
- **i104** (`TRUST` + `STACKSHIFT` + `ARRAYPROBE`) is built and verified end-to-end: the kernel
  inside the packaged container is byte-identical to the build output, `0xa8d5d74` branches to
  `0xab0d4c0`, the cave holds code, and the `'0'..'f'` table is present. SHA-256
  `bb25af3ab8eaa02c63c1de2e17eea5d73252e5302c529adc69f944b118ea49f7`,
  `staging/amethyst-ios27-i104/Mu-amethyst-ios27-i104.img`. Expected output on screen is one line
  `ARRY <BASE><IDX><ELEM>` = three 16-hex-digit fields, then a hang (the probe hangs deliberately
  so the line stays readable; the kernel faults immediately after the hooked instruction anyway).
  **Superseded:** i104 hung on iteration 0 (IDX=0), which is benign -- see the i105/i106 note under
  "the site is a loop" below.
- **i105** proved the `__DATA` hex table theory wrong. It was byte-identical to its build output with
  `ARRY` correctly present, so the table was never the problem. The table was moved anyway into
  `__TEXT_EXEC` at `0xab0d7f0` (immediately after the `ARRY` literal, ending exactly at `0xab0d800`
  where `excprobe`'s cave begins), because that segment is proven mapped: the cave itself executes
  from it. Verified in the shipped image, not just the build output.
- **i106** was hooked at `0xa8d5d7c` and produced no output -- a **null result**, because its benign
  rejoin was written as `bl` instead of `b` and therefore corrupted `x30`/`lr` on every healthy
  iteration. SHA-256
  `8b70db54cc12af3c07476479339de8b1e6cabd6ed2e57283cbe87acb6064b13b`,
  `staging/amethyst-ios27-i106/Mu-amethyst-ios27-i106.img`. Not to be reused. The hook site itself
  is correct (it is upstream of the fault, and `x21` is not written between the hook and
  `0xa8d5ddc`), so the fixed `arrayprobe.py` keeps it. See the i106 panic-dump section above.
- **i107** is i106 with the two probe defects fixed, and is the image to boot. SHA-256
  `7d5ad3ac026b2a7ecf33d149a26a7dbde3692df0f434107d506b267fa0c0b385`,
  `staging/amethyst-ios27-i107/Mu-amethyst-ios27-i107.img`. Verified **against the kernel extracted
  from the shipped container**, not the build output: hook `0xa8d5d7c` is `bl` -> cave `0xab0d4c0`;
  rejoin is `b` -> `0xa8d5d80` (the i106 bug was `bl`, and there is no `bl`->RESUME word anywhere in
  the cave); `_IOLog` called at cave+0x310 with `b .` after it; `stp x8,x9`/`stp x0,x1` at
  cave+0x20/+0x24 paired with `ldp` at cave+0x318/+0x31c; replay `0xf9400ea1`; both `tbnz` guards
  land on the benign label at cave+0x318. `probe-lint.py` reported no overlaps, an all-zero cave,
  and a clean code check.

  Boot it with:

  ```
  fastboot -s 42fe0af2 boot /mnt/c/Users/Bluekyeet/Downloads/amethyst/Mu-amethyst-ios27-i107.img
  ```

  Expected: **nothing at all** if every `array[x26]` the loop visits is a well-formed pointer. On the
  first slot that is not, one line `ARRY <x21><index><base>` appears and the machine hangs with it
  on screen. Since `x21` at the hook is provably the same value that faults at `0xa8d5ddc`, a silent
  boot now genuinely rules the array out rather than being a dead probe.

## b18 — touch + side buttons (commit 1c07ae3, CI run 44876675607)
iPadOS 17 touch (Goodix Berlin/GENI SPI, loopback, DMA), SPMI side buttons and AmethystButtonsHid linked into the D94
legacy-display archive; sheng NativeHidInput bound to the D94 IOHIDDevice (scripts/hid-device-24A446-d94ap.json).
b18 args = b17 args + the V60 set read from Mu-amethyst-tap-v60.img:
`amethyst-touch-snapshot=2 amethyst-touch-dma-preflight=1 amethyst-touch-loopback=1 amethyst-touch-goodix=1
amethyst-touch-events=2 sheng-touch-hid=1 amethyst-buttons=1 log-input=1` (record* diagnostics left out).
Expect log rows "AmethystButtons: HID family load rc=0 … HID init=1 attached=1 running=1" and the [touch snapshot] section.
CI fixes on the way (0297742, then macOS-SDK headers, then 7th): IOHIDFamily headers need the macOS SDK's TargetConditionals.h
and Kernel.framework (IOKit/graphics); AmethystPanel/SIGP-mask weak imports had no definition in the kernel link -> nulled.
Green run 44879355026 (kernel 3f8c2367). prepare-b18.py: cave moved to 0xfffffff00b322000/…f00 (zero tail now b321e64..b324000),
adapters 220 -> 271 (+51 IOHIDDevice). b18 image f96240b2, booted 2026-11-21.
b17n (GPU off) result: still ~5 s frame cadence (vsync sent=0); SpringBoard killed by BLS CB_DM watchdog on
"LCD transitionToDisplayMode:Off" (auto-lock display-off transition never completes) — next display fix.

## b19 — Metal addon (same b18 kernel), booted 2026-11-21
b18 log: touch works in-kernel (AmethystHid rep=168 dn=4 btn=8 pub=1; Goodix frames + presses), but only 1 swap; backboardd
GPU open "foreign type 1" e00002e2 with addon files -1 (no Metal plugin). Vsync: D94 sender reads list 0x1ab8 (= fixture
6840, verified by disassembly) - nobody registers. Sheng (same iPadOS 27 build) renders via Metal addon.
b19 = b18 kernel + Payloads/kernel.macho.gpuaddon = d94-metal.addon (CI 44783163622, NGPUADD2) + ADT /chosen/memory-map
GpuAddon + trustcache 4th module (plugin 5278f48a…, turnip 0b5bc2e4…) + sheng-gpu-addon=1. Image 3476a17e. GPU archive
already publishes MetalPluginName=NativeAdrenoMetal and NativeSandboxGrantProcess=backboardd.
b19 HW (log untagged-6133a445b): addon installed (files 6 written 6, addon 3/2) but AMFI rejected
NativeAdrenoMetal "adhoc signed ... unsuitable CT policy 0": this RELEASE kernel loads only bundle modules 0/1 (static0 +
second static via amethyst-owner-release.patch); appended module 3 is engineering -> skipped. Touch: rep=3834 dn=54 sent to iOS.
b19b = b19 with plugin+turnip CDHashes merged (sorted, type 2) into bundle module 1 (130->132 entries; other modules unchanged),
tool scratchpad tc-merge1.py. Image 5055408b, booted 2026-11-21.
b19b HW (log untagged-444ef8e7): Metal plugin LOADS - gpu clients backboardd s0, SpringBoard s1, Setup s3, 228 submissions/1067
IBs; first swap 391 s (was ~1540 s). Presentation still CPU copy on the ~5 s beat (vsync arms=0). Plugin report "P2 ee00002d8".
b20: 2b31939 allows sheng-fb-gpu=1 (GPU hand-off presenter on resource service, sheng code; sheng-dpu-scanout still refused).
CI 44884895823 (kernel b5993d37), prepare-b20.py (cave b322000 unchanged, 271 adapters) -> fe2106cc. b20 = b19b payload +
new kernel + sheng-fb-gpu=1, image 43313e5a, booted 2026-11-21.
b20 HW (log untagged-4446f8ba): user "still slow". GPU hand-off WORKS (offer 0 ms, done 16-20 ms vs ~100 ms CPU copy) but
swaps still on the ~5 s beat. [scanout wait] 577/584 timeouts @500 ms = copier idle loop (NativeGOPPresenter::waitSwap), not
the cause. Touch reaches iOS: edge wrapper counted 18 contacts begun; the IOUserHIDEventDriver "null event" lines (~31) match
button reports (no digitizer data) - harmless. No crashes. Remaining: render server pacing (vsync arms=0).
b20b = b20 + offline-legacy-unit-scale=3 (sheng/V60 pass unit-scale; publishes /chosen display-scale), image c868721c.
b20b: DarwinBoot "returned bad buffer size" = CommandLine[1024] overflow (args 1032 chars). Keep args < 1023.
b20c = b20b minus pid1-exit-diag/pid1-exit-hold (997 chars), booted 22:04.
**5 s FRAME CAUSE (found online):** Scion-Project/scion-kernel docs/ios27/GPU-PATH.md: iOS 27 QuartzCore CA::EDRClient::client()
-> EDRCreateClientForDisplayWithTimeout to com.apple.libEDR.Notification; server never answers -> ~5 s timeout every frame
("one swap per 5.2 s"). sheng AppleARMBacklight.cpp: CoreBrightness starts that libEDR server only for a backlight service
conforming to AppleARMBacklight with backlight-control=true (int-236). D94 had no backlight service.
Fix commit: port AmethystPanel + AmethystBacklight (arm64e getMetaClass override answering AppleARMBacklight), CI 44888104945;
boot with amethyst-panel=3.
b21: CI 44888104945 OK (amethyst_panel_probe/amethyst_backlight_start linked). __TEXT_EXEC zero tail now only 0xadc ->
prepare-b21.py cave moved to the page padding before _LowResetVectorBase: renderer 0xfffffff00ac97400, trampoline ...ac97f40
(__KLD has room but is freed after boot - not for the panic renderer). Kernel 77d92513. b21 = b20c + new kernel +
amethyst-panel=3 (args 1014 chars), image 75c93d03.

**Nov22 b21 HW:** backboardd SIGSEGV at user ~102 s, then launchd reboot (devicerecoverytool). Symbolized against the
iPhone17,2 24A446 dyld cache (UUID 1892d244, `ipsw extract --dyld --remote` of the iPhone17,2 IPSW; the iPhone15,4 cache is
a757941a and does not match): BLControl handleCADisplay -> CBDisplayContaineriOS findBacklight/handleDisplayArrival ->
CBDisplayModuleiOS initWithBacklight: -> DisplayCreateWithCapabilitiesAndID+1096, `ldr s2,[x8]` with x8 = the nits table
(display+0x40) not yet built. Taken when -[CBBacklightNode getBacklightCalibration:] fails: with no capabilities dict it reads
`backlight-calibration-parameters` (dict lMaxProduct/lMidProduct/lMinProduct/lMaxPanel/lMidPanel/lMinPanel, intValue as 16.16
nits; milliAmps2NitsScaleFactor optional). copyMarketingTable then needs `backlight-marketing-table` (OSData of 16.16 nits, >= 3
entries) or the display is not created. So the conformance fix worked (CoreBrightness took the backlight).
**b22** (0570581, CI 44932203446, kernel fa7a7071): AmethystBacklight publishes both (2/100/500 nits, 17-point quadratic
table). HW: frames normal (5 s cadence gone), GPU: 1402 swaps accepted. Touch reached the driver (43 contacts, 1226 reports)
but nothing acted.
**b23** (2d12dd2, CI 44935294039, kernel d37fce30): the DriverKit HID dext publishes (1,6) after our one-time publish, so
backboardd drops digitizer events from an "unsupported service"; republish (0x0d,4) whenever it reads back otherwise (sheng
HidInputProbe.cpp). HW: touch works (pub=3: republished), 8571 swaps over 12 min. Setup page every boot (fresh Data).
**b24** (7cf1862, CI 44938482037, kernel ba6c3ef1): BootWatchdogPolicy.c's load_init_program call was under
XNU_BOOT_PROGRESS (unset for D94), so sheng-lite/sheng-disable-watchdogd never ran; native-d94-launch-policy.patch adds sheng's
#else call. BootWatchdogPolicy.c gains amethyst-skip-setup (default 1): purplebuddy/migration/managedconfiguration/springboard
prefs as mobile-owned XML plists before launchd. HW (user): lock screen -> swipe to home, touch, smooth frames; lag remains.
Power off from iOS panics: launchd unmounts Rave24A446.D94SystemCryptex, then faults fetching code from it (esr 0x82000007),
initproc exited. Log: [gpu unstick] kick=1177 longest=5090, [fence wait] 4.66M timeouts (sheng-gpu-irq=0 polling),
[cpu gov] boosts=0.
**b25** (96779b0, CI 44942054764, kernel 785e59ce): CpuFreqProbe/GpuPowerProbe touch boosts watch native_hid_input_events[6],
which nothing defined (the amethyst driver counted amethyst_hid_events); now both are counted. Args: +sheng-gpu-dcvs=1,
-sheng-gpu-irq=0 (driver default 1; =0 dated from iPadOS 17 V46). b25b = same kernel with sheng-gpu-irq=0 kept,
-sheng-gpu-trace=0 (default 0) for length. b25 booted 2026-11-22; result pending.

**b26-b29c** (2026-11-22): b29 (fcf94e4, CI 44964828618) = NativeAdreno a81c173 5 ms nudge + brightness-nits fix. HW: brightness
slider works; no freezes at GPU level 6 but laggy (fence waits 99 s / 527 s). Level 8 (b29b) and level 7 (b29c): frames mostly
< 4 ms, then a permanent GPU freeze (hangs 49/40, restarts 0) with touch alive. SoC id 636 (plain Volcano) - level 8 is in-bin.
Cause: the board never installed native_adreno_set_restart, so NativeAdrenoPublish's hangcheck only counted hangs.
**b30** (board 07fd4aa on branch a810-gpu-restart, f50c3d8/1fb3c9e, compile 44991058715, CI 44991539720, kernel a41145bc):
gpuRestart for the A810 (sheng's gmu-wrapper reset: SQE halt, GBIF drain incl. A8XX RBBM_GBIF_HALT 0x0a, RBBM_SW_RESET_CMD
0x73, hw_init, CP start, secure-mode exit). b30 = level 6, b30b = level 7. Log: [gpu recovery] restarts / native_gpu_restart.
The /tmp prepare-bNN.py chain was lost in a WSL /tmp wipe; its five post-link patches are now
scripts/bringup-24A446/prepare-d94-kernel.py (reproduces the shipped b29 kernel byte-for-byte from CI 44964828618).
