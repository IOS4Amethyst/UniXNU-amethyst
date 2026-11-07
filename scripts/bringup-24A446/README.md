# iOS 27 24A446 hardware bring-up (test only)

Post-link patches applied to the prelinked kernel for amethyst test boots (`fastboot boot`, RAM only,
never flashed). They bind retained-kext GOT/data slots that XnuPrelink left on
`_unixnu_fileset_unmapped` to reviewed native symbols, plus a few targeted kernel-binary patches and
trust-cache plumbing needed to get this far. Every binding was checked against the native body (exact
sequence, thunk shape, exact struct-offset match, or position among already-mapped neighbours — not
similarity-score alone; the window-similarity ranker has produced at least one confident but wrong
match, see `bind.json` note below). Inputs are a local 24A446 kernelcache, the `ios27-run5` prelinked
kernel, and the iOS 27 IPSW's trust-cache files; none of these are in git.

## Current state: boot reaches AMFI's entitlement parser, then stops

Progress, in order:

1. Storage, APFS container, and the root volume (`Rave24A446.D370S`, UUID
   `E89DE0E4-B66C-425D-BCD1-90F637F43F1F`) mount; devfs mounts.
2. `/sbin/launchd` is found, decompressed, and parsed as a Mach-O.
3. The iOS 27 trust caches load and the trust-cache lookup succeeds (launchd's cdhash is found).
4. AMFI validates and extracts launchd's code signature, including its DER-encoded entitlements.
5. **Blocked here:** AMFI's own CoreEntitlements parser fails while iterating that DER entitlements
   data (`AMFI: failed to create OSDictionary copy: 14` at `OSEntitlements.cpp:863`), launchd is
   killed (`namespace 9 code 0x8`, `EXEC_EXIT_REASON_SECURITY_POLICY`).

This is **not** a missing-binding problem: every kernel function AMFI's entitlement-conversion path
calls is bound, the libkern constructors it uses (`OSDictionary`/`OSArray`/`OSString`/`OSSymbol`/
`OSData`/`OSNumber`/`OSBoolean`) match native code and vtable offsets exactly, no retained-kext
`metaClass` data pointer is left dangling, the `amfi_t` interface struct layout is correct (the
failure is a clean error return, not a crash), and our XNU's DER-entitlements extraction
(`ubc_subr.c`, `csb_der_entitlements_blob`) is unmodified from upstream and was read end-to-end — it
validates magic/bounds and stores a subrange of the validated superblob with no copy or truncation.
A probe (see `ceprobe.py` below) narrowed the failure to AMFI's own DER *iterator* returning error 2
on a 0x620-byte blob, before any dictionary-entry callback runs — i.e. real Apple code is rejecting
data that the extraction path handed over intact and unmodified.

**Two bypass attempts were tried and are confirmed dead ends — do not repeat them:**
forcing `_mac_vnode_check_signature` to always succeed, or disabling AMFI's exec-time checks, both
just move the failure: AMFI's other hook (`cred_label_update_execve`) then kills launchd anyway for
"dyld signature cannot be verified", because skipping the signature hook also skips setting the
platform-binary flags AMFI's other checks depend on. There is no shortcut around AMFI here; the DER
iteration failure is the real, single blocker.

**What's needed to go further** (not available in this environment): the actual DER entitlements blob
extracted from launchd's signature on the phone's root volume, byte-compared against what the kernel
hands AMFI, to rule out a 24A446-specific difference in blob framing; and/or the 24A446 XNU source
tree (this bring-up was done against the iPadOS 17.7.11 tree for reference, since no 24A446 tree is
present locally) to check whether the extraction code differs in that version. Also worth checking:
whether the equivalent boot path for iPadOS 17 / V60 (which does boot) hands AMFI a similarly-shaped
DER blob successfully — a structural diff of the two blobs would localize this without touching any
verification code.

## Files

- `bind.json`: kernelcache function address -> native symbol, applied to slots still on the stub
  (`patch-got.py`). Includes decmpfs (file decompression), `VNOP_GETXATTR`, csblob/csfg entitlement
  and code-signature accessors, `query_trust_cache` family, and `proc_task` / `developer_mode_state` /
  `proc_best_name` / `task_set_cs_auxiliary_info` (used by AMFI's exec-label hook).
- `data.json`: kernelcache data address -> native symbol (`patch-data.py`). Includes
  `OSString::metaClass` (0xfffffff007cc9668) and `decmpfs_ctx` (0xfffffff00af40768), both missing from
  the prelinker's data map.
- `got-override.json`: map errors, rebound even where the prelinker already bound the slot
  (`GOT_OVERRIDE=` env for `patch-got.py`): `thread_wakeup_prim` was mapped to an `OUTLINED` helper,
  `0xaabceac` is `IOSimpleLockFree`, `0xa8a62b0` is `csblob_get_teamid` (mapped as
  `csblob_get_auxiliary_info`), and `0xa8a5f7c` is `cs_system_require_lv` (mapped as
  `_filt_psetevent` — wrong family entirely; it's one of two adjacent constant-1 stubs in the
  kernelcache and the map picked the wrong neighbour). **Caution:** the window-similarity ranker
  (`align.py`) that found most of the other bindings in this file scored `proc_task` as a 1.0 match to
  `_proc_get_task_raw` at one point; that was wrong (different struct field, different semantics) and
  was only caught by comparing actual disassembly. Treat any future similarity-only match as a
  candidate to verify, not a binding to trust directly.
- `tcfix.py`: kernel binary patch. The RELEASE-build check in `arm_vm_prot_init` panics
  (`TrustCache region is in an unexpected place`) because it requires the trust-cache region to sit
  below `segLOWEST`, but DarwinBoot always places it above the kernel. This patches the check to do
  what the DEVELOPMENT build does instead: record `segTRUSTCACHE`/`segSizeTRUSTCACHE` and continue.
- `adtaddtc.py` / `tcparse.py`: add a `TrustCache` slot to `/chosen/memory-map` in the device tree, and
  unwrap/repack the IM4P trust-cache files into the sidecar bundle format DarwinBoot expects
  (`kernel.macho.trustcache`). The bundle itself (built from the iOS 27 IPSW's StaticTrustCache,
  SystemTrustCache, and AppTrustCache) is IPSW-derived and **not** committed; regenerate it from a
  local 24A446 IPSW with `ipsw extract --remote --pattern '.*\.trustcache$'` plus these two scripts.
- `mxprobe.py`: bring-up probe. Logs `MACX <policy name> idx <n> ret <value>` after each static MAC
  policy's `cred_label_update_execve` hook runs, to see which policy (AMFI vs. Sandbox) rejects an
  exec and with what return value.
- `ceprobe.py`: bring-up probe, specific to the current blocker. Freezes the screen (so the log stays
  readable) at AMFI's CE→OSDictionary converter's three failure exits (tagged `CEFAIL A/B/C`: null
  object from the walker, failed dictionary insert, failed array insert) and at the DER-entitlements
  iteration call feeding the walker (tagged `CEIT err <code> ctx <...>`). This is how the current
  blocker (iterator returns error 2) was found.
- `align.py` / `reach.py` / `unbound.py` / `dumpk.py`: support tools. `align.py` ranks native
  candidates for an unmapped kernelcache function by matching the ordered sequence of already-mapped
  neighbour functions (verify its output against real disassembly, see caution above). `reach.py`
  walks the call graph from a kernelcache address and reports which reachable calls are still on the
  unmapped stub. `unbound.py` lists all still-unmapped GOT slots for one retained kext, grouped and
  looked up against the function map. `dumpk.py` is a linear disassembler for either the kernelcache
  or the native reference kernel, used by all of the above.
- `logstub.py`: `_unixnu_fileset_unmapped` logs `UNMAPPED +<caller offset> x0 x1` and returns 0.
  `site.py` turns the caller offset into the stub, GOT slot and original target.
- `pmgrfix.py` / `pmgrprobe.py`: ApplePMGR binding slide fix and publication probe.
- `hang.py` / `vbvprobe.py`: freeze APFS error cleanup so the log stays on screen; superblock probe.
- `build5.sh N BIND DATA`: patch, add the panic screen, optionally add the trust cache
  (`TRUST=<bundle path>`) and probes (`MXPROBE=1`, `CEPROBE=1`), pack `staging/amethyst-ios27-iN`.
  Do **not** add a `CSBYPASS` step here — see the dead-end note above.

These bindings belong upstream in XnuPrelink's reviewed-name fixture; this directory records them
until then.
