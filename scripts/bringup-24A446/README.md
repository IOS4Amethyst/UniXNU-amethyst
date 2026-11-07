# iOS 27 24A446 hardware bring-up (test only)

Post-link patches applied to the prelinked kernel for amethyst test boots (`fastboot boot`, RAM only,
never flashed). They bind retained-kext GOT/data slots that XnuPrelink left on
`_unixnu_fileset_unmapped` to reviewed native symbols, plus a few targeted kernel-binary patches and
trust-cache plumbing needed to get this far. Every binding was checked against the native body (exact
sequence, thunk shape, exact struct-offset match, or position among already-mapped neighbours — not
similarity-score alone; the window-similarity ranker has produced at least one confident but wrong
match, see `bind.json` note below). Inputs are a local 24A446 kernelcache, the `ios27-run5` prelinked
kernel, and the iOS 27 IPSW's trust-cache files; none of these are in git.

## Current state

Resolved: the AMFI/CoreEntitlements entitlement-dict blocker described in earlier revisions of this
file is fixed (`stackshift.py`). `/sbin/launchd` now runs and starts loading kernel extensions. The
current blocker is a crash during a dynamic kext load, reached only after that fix. **See
`HANDOFF.md` for the full story and the current task** — this file covers the binding/patch
inventory, not day-to-day status.

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

  Both of those re-mappings were re-verified from disassembly, and the teamid one now has a clean
  proof rather than a judgement call. `0xa8a62b0` is `ldr x0,[x0,#184]; ret`, a one-field getter.
  `cs_blob.csb_der_entitlements_blob` is at offset 200 (read as `[x0,#200]` by
  `csblob_get_der_entitlements`), and the fields before it step back by 8:
  `192` = `csb_entitlements_blob`, **`184` = `csb_teamid`**, `176` = `csb_cd`. So `0xa8a62b0` returns
  the team-id pointer, and the map's `auxiliary_info` label is impossible: that field sits far later in
  the struct, past the flag bitfields. (That chain holds with `CONFIG_SUPPLEMENTAL_SIGNATURES`
  disabled, which is the layout the runtime offset 200 confirms; if it were enabled,
  `csb_supplement_teamid` would sit between them and 184 would still be `csb_teamid`, so the
  conclusion does not depend on the option.) `0xa8a5f7c` is `mov w0,#1; ret` — an unconditional-success
  stub, which matches `cs_system_require_lv` and cannot be a filter-tap event handler.

  The tell for both is the `how` column in `kernel.map.json`: `pac-checked-shape` and `pure-leaf`,
  i.e. shape-similarity guesses, not call-graph evidence. When a binding's `how` is anything other
  than `calls`, check the disassembly before trusting the name.
- `stackshift.py`: kernel binary patch, **required**. AppleMobileFileIntegrity's GOT slot for Apple's
  `PAGE_SHIFT_CONST` was bound to Apple's 16 KiB-page value (14); CoreEntitlements' DER iterator uses
  it as a stack-depth check (`sp & mask < 0x80`) that assumes that page size, which false-positives on
  our 4 KiB-page kernel's stack layout. This was the actual cause of `AMFI: failed to create
  OSDictionary copy: 14` at exec — see `HANDOFF.md`. Rebinds the slot to the value `15` instead. Found
  via `UniXNU/XnuPrelink`'s `fixtures/fileset-24A446-symbols.json`, not derived independently here.
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
