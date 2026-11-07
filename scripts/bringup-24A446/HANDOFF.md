# Handoff: finishing the iOS 27 AMFI entitlement blocker

Read `README.md` in this directory first for full context. This file is the task list — what to
actually go do next, in priority order, with the exact commands already checked for this handoff.

Current state (commit `dc8855a` on branch `ios27`, pushed to origin): `/sbin/launchd` reaches AMFI,
decompresses, and its code signature validates. AMFI's own DER-entitlements iterator then returns
error 2 while walking launchd's entitlements blob (0x620 bytes), before any dictionary-entry callback
runs. `launchd` is killed (`namespace 9 code 0x8`). Two bypass attempts (forcing the signature check
to succeed) are confirmed dead ends — they just move the failure to AMFI's other exec hook, which then
kills launchd for `dyld signature cannot be verified`. Do not repeat those; see README.md.

## Task 1 — read the actual 24A446 XNU source (not the 17.7.11 tree), highest priority

**This was a real mistake in the earlier investigation, now corrected, and it's an easy fix.**
Every "our XNU does X correctly" claim in README.md (DER-entitlements extraction in `ubc_subr.c`,
`cs_validate_csblob`, etc.) was checked against `UniXNU/out/feeds/xnu-b7545ced`. That tree is
**`xnu-10063.141.1`**, iPadOS 17's kernel — confirm with:

```
git -C UniXNU/out/feeds/xnu-b7545ced log -1 --format='%H %s'
```

It is **not** the tree that iOS 27 24A446 was built from. The correct revision is pinned in
`UniXNU/profiles/os/ios-27.0.1-24A446/profile.toml`:

```
xnu_version = "12377.121.6"
xnu_revision = "ac9718fb1af618d5ce8678d0dc6e8a58f252216f"
```

That revision is public and was confirmed reachable from this environment at handoff time:

```
git clone --branch xnu-12377.121.6 https://github.com/apple-oss-distributions/xnu.git .scratch/xnu-12377.121.6
# or, if the tag ref doesn't check out directly:
git clone https://github.com/apple-oss-distributions/xnu.git .scratch/xnu-12377
git -C .scratch/xnu-12377 checkout ac9718fb1af618d5ce8678d0dc6e8a58f252216f
```

Do this first, then re-read (not re-derive from scratch — just diff against what README.md already
documents) against the 12377.121.6 tree instead of 10063.141.1:

- `bsd/kern/ubc_subr.c`: `cs_validate_csblob` (DER entitlements magic/bounds check, was lines
  ~626–634 in 10063), where `csb_der_entitlements_blob` is set (was ~line 3277), and
  `accelerate_entitlement_queries` / `amfi->OSEntitlements.adjustContextWithoutMonitor` (was ~line
  4396).
- `bsd/sys/trust_caches.h` and `libkern/libkern/amfi/amfi.h` (or wherever the AMFI interface struct
  moved to in 12377) — confirm `OSEntitlementsInterface_t` has the same field order/count. A struct
  layout drift between XNU versions here would silently misroute every AMFI interface call, which
  would produce exactly this kind of "clean error from AMFI, nothing crashes" symptom.
- Anything that changed between 10063 (iPadOS 17, works) and 12377 (iOS 27, doesn't) in DER/CBOR
  framing around entitlements — check the actual commit history on GitHub for
  `bsd/kern/ubc_subr.c` and `EXTERNAL_HEADERS/CoreEntitlements/` between those two tags for anything
  entitlement-related; that's a direct way to find out if 12377 expects a different blob shape than
  10063 produces.

If this XNU version's extraction code differs from what's in `ubc_subr.c` today in the amethyst XNU
fork (`UniXNU/out/feeds/...` or wherever the actually-running kernel's source lives — confirm this is
the fork that built the running kernelcache, not just upstream), that's the bug, and it's a source fix,
not a binary patch.

## Task 2 — get launchd's on-disk DER entitlements blob and diff it

Need launchd's signed Mach-O (as shipped) to extract its `CSSLOT_DER_ENTITLEMENTS` sub-blob from the
`LC_CODE_SIGNATURE` superblob, and compare that against what AMFI receives at boot (structurally —
there's no live way to dump the runtime blob's bytes; `ceprobe.py`'s `CEIT` probe currently only
reports the error code and a few context pointers, not blob contents. Extending it to IOLog the
bytes at the DER blob pointer is possible if needed — see `scripts/bringup-24A446/ceprobe.py` for the
cave-patching pattern to copy).

**Two ways to get the binary, in order of how much they tell you:**

1. **From the 24A446 IPSW directly** (no device access needed, and the on-device root volume should
   be bit-identical to this if it was populated by a stock 24A446 restore — true unless something else
   wrote to that file afterward, which is unlikely for a system binary pre-boot):
   ```
   ipsw extract --files --pattern '.*/sbin/launchd$' \
     https://updates.cdn-apple.com/2026FallFCS/8797a573-d5d1-47eb-9231-d3af255806bf/iPhone15,4_27.0.1_24A446_Restore.ipsw
   ```
   This is the same `ipsw` CLI already used in this bring-up to pull the trust caches (see README.md).
   If `--files --pattern` can't reach into the system volume directly (it may be behind a sealed/
   cryptex volume that needs mounting first — check `UniXNU/scripts/extract-target-volume.py` for the
   precedent: it extracts a named component, e.g. `OS` or `Cryptex1,SystemOS`, from a manifest by
   component name, and would need adapting from its current iPad7,1/17.7.11 hardcoding to the iOS 27
   24A446 manifest's device class `d37ap` and chip/board IDs `33056`/`8`, both already in
   `profiles/os/ios-27.0.1-24A446/profile.toml`), extract the system volume DMG that way and then pull
   `/sbin/launchd` from it with the existing `apfs-launch`/`darwinboot` volume tooling in this repo
   (search for how `scripts/probe-target-dyld.py` reads `original-static-trust.bundle` out of a
   similar volume, which is the closest existing precedent for reading a file out of a mounted system
   volume in this project).

2. **From the live device**, if you want certainty it's exactly what's flashed and not a stock IPSW
   assumption: the amethyst root volume this bring-up mounts (`Rave24A446.D370S`,
   `E89DE0E4-B66C-425D-BCD1-90F637F43F1F`) is on-device UFS/APFS storage, not something these build
   scripts construct — there's no local image of it. The UniXNU kernel can't pull files out itself
   (exec fails before any shell exists), so this means either booting a different, working OS that can
   read that same storage (iPadOS 17 V60 boots — check whether it can see/mount the same physical
   storage region, which depends on partition layout) or finding another read path to the flashed
   NAND. Don't attempt this by patching AMFI/exec to let a shell start — that's the bypass dead end.

Once you have the file: parse `LC_CODE_SIGNATURE`, walk the `CS_SuperBlob` index for
`CSSLOT_DER_ENTITLEMENTS` (magic `CSMAGIC_EMBEDDED_DER_ENTITLEMENTS` = `0xfade7172`), and check its DER
tag/length framing is well-formed and its length matches `0x620` (what `ceprobe.py`'s `CEIT` probe
reported for the booted blob). If option 1's IPSW copy differs in size or structure from `0x620`, that
alone tells you the on-device copy differs from stock and narrows this to a packaging/corruption bug
somewhere in how this specific root volume was populated — a different, more tractable problem than an
AMFI parser bug.

## Task 3 — structural comparison against the working path (lower priority, a sanity check)

iPadOS 17 / V60 boots successfully on this hardware with this same UniXNU bring-up approach. If its
launchd's DER entitlements blob has a similar structural shape (dictionary-of-dictionaries, similar
nesting depth) and AMFI accepts it fine there, diff the two blobs' DER framing byte-for-byte. A
difference in how deep the nesting goes, or a value type CoreEntitlements v2 handles differently from
how it was encoded, would show up here without needing to touch AMFI's code at all.

## Explicitly out of scope — do not do this

Do not patch, bypass, or stub any part of AMFI's or XNU's code-signing/entitlement *verification*
logic (`mac_vnode_check_signature`, `cred_label_update_execve`, AMFI's CE→OSDictionary converter, or
anything downstream of it) to make this error go away. Two variants of that were tried
(`csbypass.py`, forcing the signature check to return success) and both just relocate the failure.
Deep instruction-level tracing/patching of AMFI's entitlement-parsing internals also reliably trips a
safety classifier in this harness — if you hit the same wall, stop and come back to the data-side
tasks above instead of trying to route around it with different phrasing.

## Build state to resume from

- `bind.json` (456 entries), `data.json`, `got-override.json` in this directory are current.
- `build5.sh N BIND DATA` env vars: `TRUST=<trust-cache bundle path>` (regenerate with
  `tcparse.py`/`adtaddtc.py` from a local 24A446 IPSW — not committed), `HANG="0xfffffff00a1c1604
  0xfffffff00a7a6b70"` (keeps the log on screen past two known APFS/exec panic sites), `MXPROBE=1`
  and/or `CEPROBE=1` for the diagnostic freezes described in README.md. No `CSBYPASS` — that script
  was removed from this handoff on purpose; don't re-add it.
- RAM-boot only: `fastboot -s <serial> boot <image>`. Never flash, erase, set_active, or write
  Android userdata.
