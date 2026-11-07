# Handoff: finishing the iOS 27 AMFI entitlement blocker

Read `README.md` in this directory first for full context. This file is the task list — what to
actually go do next, in priority order, with the exact commands already checked for this handoff.

Current state (branch `ios27`, pushed to origin): `/sbin/launchd` reaches AMFI, decompresses, and its
code signature validates. AMFI's own DER-entitlements iterator then returns error 2 before any
dictionary-entry callback runs. `launchd` is killed (`namespace 9 code 0x8`). Two bypass attempts
(forcing the signature check to succeed) are confirmed dead ends — they just move the failure to
AMFI's other exec hook, which then kills launchd for `dyld signature cannot be verified`. Do not
repeat those; see README.md.

## Where the investigation stands

Three separate claims have been made and retracted about this blocker. Only the static evidence
survives, so read this section before proposing anything.

1. **Retracted: "the kernel was built from iPadOS 17's XNU."** CI resolves XNU from the OS profile;
   the running kernel is `xnu-12377.121.6` (`ac9718fb…`). See "Retracted lead" under Task 1.
2. **Retracted: "AMFI is handed a 0x620-byte blob, 7 short."** `ceprobe.py` stored its values on the
   stack and never loaded them into `x1`–`x7`, and `IOLog` reads its first seven conversions from
   those registers. The six-conversion format read leftover registers and never touched the stack
   slots. **Every number those probes printed was uninitialised garbage** — including 0x620 and the
   "walker-null type" value. The probe is fixed but has not been run on hardware.
3. **Retracted: "our code-signing path truncates the blob."** Disassembly of the shipped kernel shows
   the data hand-off is exact end to end (below). There is no measured truncation and no structural
   room for one.

What *is* established, from the shipped kernelcache rather than from a probe:

- `csblob_get_der_entitlements` (`0xa93fd50`) hands AMFI the **whole `CS_GenericBlob`**, 8-byte
  `fade7172` header included, with length `ntohl(blob->length)` = **`0x62f`**. That is what Apple's
  kernel does too.
- CoreEntitlements skips that header explicitly: the DER container constructor
  `0xfffffff008f73930` is called from `0xfffffff008f7779c` with `w0=6, w1=8`, and `w1` is the header
  length to skip.
- The framing closes exactly: `8` (generic header) + `4` (DER tag/long-form length) + `0x623`
  (content) = `0x62f` = the slot length. Nothing is missing.

**So the open question is no longer "is the blob the right length".** It is "what does
CoreEntitlements do with a correctly-delivered blob that makes it return 2".

## The single next action: one hardware run to get a real measurement

Everything static that can be settled has been settled. The next step needs real numbers, and that
means one RAM-only boot. This is the main agent's job — do not flash, erase or `set_active`.

The probe is fixed, validated offline (all four sites assert, each `bl` resolves to `_IOLog` at
`0xfffffff00a93a004`, formats land where the `adrp`/`add` pairs point), and **already synced** to the
copy the build script actually invokes (`$S/id/ceprobe.py`). Verify before building — if this
`diff` is not silent, the run will print garbage again:

```sh
diff /tmp/claude-1000/-home-bluekyeet-SmallProject/6bef58f5-6d21-489e-b4c5-0ae037cad119/scratchpad/id/ceprobe.py \
     UniXNU-amethyst-ios27/scripts/bringup-24A446/ceprobe.py
```

Build and boot (next free index is **i84**; i83 is the last one built):

```sh
cd /home/bluekyeet/SmallProject
TRUST=<bundle> GOT_OVERRIDE=over-i72.json CEPROBE=1 \
  HANG="0xfffffff00a1c1604 0xfffffff00a7a6b70" \
  <scratchpad>/id/build5.sh 84 <BIND> <DATA>
```

`build5.sh` writes `staging/amethyst-ios27-i84/`, containing
`Mu-amethyst-ios27-i84.img`, `BUILD.txt` and `SHA256SUMS` — that image is what gets booted. Then,
RAM-only, never flash, and give the user the UNC path:

```
fastboot boot -s 42fe0af2 ...\staging\amethyst-ios27-i84\Mu-amethyst-ios27-i84.img
```

Confirm the kernel actually in the image matches what the probe patched before booting — `build5.sh`
already does this internally (`mtype … ::/EFI/UniXNU/Payloads/kernel.macho | cmp - <kernel>`), so a
silent build means the probe is in. Never add `CSBYPASS=1`. The four cave addresses
(`0xab13400`–`0xab13700`) sit inside the panic cave and were verified empty (0 non-zero bytes) before
patching, and `hang.py` only writes the addresses explicitly passed to it, so the two do not collide.

### Reading the result — what has actually been confirmed on hardware, run by run

The probe prints one line per site. Format: `CEIT err %llx p8 %llx p16 %llx p24 %llx p32 %llx p40
%llx`, where the six values are the iterate return code and then the five words at `x21+8…x21+40` of
the CE pod.

**Confirmed real, on two independent builds (i84, i85), both landing on this exact line:**
`err == 1`, not `2`. The earlier `2` was the unfixed probe reading leftover registers — see the
retracted-lead note above. `1` is real: it survived both before and after a later change (see below)
that should have had no effect on it if it were also garbage.

**Still not read: `p8`/`p16`/`p24`/`p32`/`p40`.** Every attempt so far prints the literal text
`<ptr>` for all five, confirmed by direct photo (not a blurry-photo misread — the device prints
that string). Two things were tried and both are now closed:

- **Masking each value to its low 32 bits before the call** (on the theory that `IOLog`/`os_log`
  redacts values that look like canonical kernel addresses): did not change anything. Still `<ptr>`.
  Rules out a value-magnitude heuristic.
- **Calling `_kprintf` instead of `_IOLog`** (a different, lower-level console-print function with no
  `os_log`-style composition step, already enabled by this bring-up's own `-enable_kprintf_spam` boot
  arg): **DO NOT retry this.** It did stop the `<ptr>` redaction for what little could be read before
  the next problem, but it also caused a new, *reproducible* freeze much earlier in boot — confirmed
  on two independent boots of the identical built image, same freeze point both times, right after
  root volume mounts, well before launchd's own exec. AMFI's kext logs lines from very early boot
  (before root volume even mounts), so the CE iterate function this probe patches is plausibly
  reached for some other purpose earlier than launchd's exec too, and something about reaching it
  through `_kprintf` there behaves differently than through `_IOLog`. The cause was not fully
  diagnosed before reverting — it wasn't worth another round of guessing against live hardware.
  `ceprobe.py` is back to `_IOLog` + masking (the configuration proven to reach the real blocker
  reliably), and that revert is committed. If you want the `p8`–`p40` values, you need either a
  different way to defeat `IOLog`'s redaction that doesn't touch `_kprintf`, or to read them some
  other way entirely (e.g. have the cave write them to a fixed, unused memory address instead of
  printing them, and read that address back out via a different mechanism after the freeze — not yet
  attempted).

**What `err == 1` alone is enough to do:** check `kCEAPIMisuse`/`kCEInvalidArgument`/etc. in
`EXTERNAL_HEADERS/CoreEntitlements/Errors.h` against whatever small-integer code the *internal*
iterate helper (not the public `CEError_t`-returning API — this is a different, lower-level function,
and its return value is a plain integer, not a `CEError_t` pointer) uses for its own internal result
codes, which likely needs the actual CE source or at least its header's internal result enum, not
just the public `Errors.h`. That's additive to Task 2 below, not a replacement for it.

## Task 1 — read the actual 24A446 XNU source (not the 17.7.11 tree), highest priority

**Status: done for the parts that matter; see "Retracted lead" below before redoing anything.**

### Retracted lead — do not repeat

A later session claimed the iOS 27 kernel had been *built* from `xnu-10063.141.1` (17.7) because
`unixnu.lock` in the device repo pins `[source."xnu"] commit = "d8b80295…"`, and concluded the whole
blocker was an `OSEntitlementsInterface_t` field-shift between XNU revisions. **That was wrong.** The
lock is a local convenience record; CI resolves XNU from the OS profile. The CI log proves the kernel
on the phone was built from the right tree:

```
git -C .../out/feeds/xnu-3199e8bc fetch -q --depth 1 origin ac9718fb1af618d5ce8678d0dc6e8a58f252216f
git clone --quiet --shared --no-checkout .../out/feeds/xnu-3199e8bc .../tree/.cache/xnu-work
```

(`.scratch/ios27/run-41976267912.log`, from run 41976267912.) So the running kernel is 12377 and its
AMFI interface struct matches the shipped AMFI. Do **not** re-lock or rebuild XNU on the strength of
the lock file alone.

What *is* true and verified from source: `OSEntitlementsInterface_t` did gain an `adjustContext` field
between 10063 and 12377 (`OSENTITLEMENTS_INTERFACE_VERSION` 1u → 2u). That is a real difference and it
is why the 17.7 feed tree must not be used to reason about the 24A446 kernel — but it is not this bug.

### The original task, still valid
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

## Task 2 — DONE. Result: the on-disk blob is intact at 0x627 payload bytes

Extracted the real 24A446 `/sbin/launchd` from SystemOS and measured its DER slot:

```
launchd            639,376 bytes  sha256 c640246d38aaeb2d2372aff1e5aa0de59dec267f53c0dfc155f7837e717af68b
LC_CODE_SIGNATURE  dataoff 0x95700, datasize 0x6a90
SuperBlob          6 entries:
  0x0      CSSLOT_CODEDIRECTORY                   len 0x144e
  0x2      CSSLOT_REQUIREMENTS                    len 0x0c
  0x5      CSSLOT_ENTITLEMENTS          (fade7171) len 0x915   (2325)
  0x7      CSSLOT_DER_ENTITLEMENTS      (fade7172) len 0x62f   (1583)
  0x9      (fade8181)                             len 0x4f
  0x10000  CSSLOT_ALTERNATE_CODEDIRECTORIES[0]    len 0x08
```

The DER slot lays the payload out as magic + length BE (`0x62f`) followed by a standalone DER blob:

```
70 82 06 23 ...          tag 0x70 (SEQUENCE-ish), long-form length 0x623
payload total            4 + 0x623 = 0x627  (1575 bytes)  -- wraps exactly, well formed
last entitlement         "com.apple.security.script-restrictions"  value 01 01 ff
```

So the shipped blob is **0x627 payload bytes inside a 0x62f slot**, and it is structurally sound:
the outer length field matches the file byte-for-byte. There is no evidence of truncation on disk.

### Retraction — the 0x620 runtime measurement was an artefact

An earlier revision of this file claimed the probe "reports the runtime blob as 0x620", 7 bytes short
of the real payload, and built a root cause on top of it. **That was wrong and is retracted.**

`ceprobe.py` stored the values it wanted to print on the stack (`str x0,[sp,#0]`,
`stp x9,x10,[sp,#8]`, …) and never loaded them into `x1`–`x7`. `IOLog` is variadic: with the format
string in `x0`, the first seven conversions are read from `x1`–`x7` and only the eighth onwards come
from the stack. The `CEIT` format had six conversions, so all six read leftover caller-saved
registers and the stack slots were never read. Every printed number, including 0x620, was garbage.

Do not treat `0x62f - 0x0f == 0x620` or `0x627 - 7 == 0x620` as corroboration; both are numerical
coincidences with an uninitialised value, not derived facts.

`ceprobe.py` now loads the values into `x1`–`x6` in the format's order
(`err, p8, p16, p24, p32, p40`), so the next hardware run produces a real measurement.

**Conclusion so far: the blob on disk is well formed and there is no measured runtime truncation.**
The open question is what the runtime length actually is, and what the iterator's error 2 means.

### Task 2b — re-measure the runtime blob with the fixed probe

Run procedure, cave-address notes and the branch table for reading the log are in
**"The single next action"** at the top of this file. What follows here is only the call chain the
probe's patch sites sit in, all addresses verified by decoding the instruction word (not by trusting
`llvm-mc`'s printed `#±N`, which is relative to the input buffer):

```
converter       0xfffffff008f7dc58  bti c / pacibsp   (CE -> OSObject)
  iterate call  0xfffffff008f7dd4c  bl -> 0xfffffff008f7ca10
  result check  0xfffffff008f7dd50  cbnz w0, #92      <- CEIT patch site
iterate wrapper 0xfffffff008f7ca10  checks [x0,#16] == 0x9000_0000_0000_0017 or …_0010
  bl            0xfffffff008f7ca60  -> 0xfffffff008f7c7dc
worker entry    0xfffffff008f7c7dc  ldr x8,[x0,#16]; add x0,x0,#24; bl 0xfffffff008f777ec
DER primitive   0xfffffff008f777ec  (mid-function; error 2 path is at 0xfffffff008f777f8)
```

The worker reads the CE pod at `[x0+24]`; it never sees a bare length, so the length it parses comes
out of the pod's own DER header. `p24`/`p32` should describe a `0x62f`-byte object — see the next
section for why, and do not read `0x62f` there as a truncation.

### What the kernel actually hands AMFI (read out of the running image, not inferred)

This was established by disassembling the shipped kernelcache, so it does not depend on any probe.

`csblob_get_der_entitlements` is `0xfffffff00a93fd50` (`.scratch/ios27-run4` function map). Its
success path is:

```
93fddc ldr  x19, [x0, #200]      ; x19 = cs_blob->csb_der_entitlements_blob
93fe88 str  x19, [x22]           ; *out_start  = the generic blob (magic at +0)
93fe8c ldr  w8,  [x19, #4]       ; read the LENGTH field at offset 4
93fe90 rev  w8,  w8              ; ntohl
93fe94 str  x8,  [x21]           ; *out_length = generic blob's length  == 0x62f
```

Offset 200 is confirmed from `bsd/sys/ubc_internal.h`: `csb_der_entitlements_blob` follows
`csb_entitlements_blob`, and with 8-byte alignment it lands exactly on 0xc8 = 200. The field's own
comment there says it is the "**raw blob**".

So AMFI receives **the whole `CS_GenericBlob`, including its 8-byte `fade7172` + length header**, with
length `0x62f` — *not* a pointer to the DER payload and *not* `0x627`. This is what Apple's kernel does
too, so it is correct behaviour, not our bug. It also explains `0xfffffff008f7871c ldrb w26,[x19,#4]`
in the AMFI builder: offset 4 is the length field, so AMFI knows it holds a generic blob.

CE strips the header itself, and the mechanism is explicit in the binary. The DER container
constructor is `0xfffffff008f73930`, and its call site at `0xfffffff008f7779c` passes:

```
f7779c mov w0, #6        ; container kind
f777a0 mov w1, #8        ; <-- header length to skip
f777a4 mov x2, x24       ; pointer
f777a8 mov x3, x23
...
f777bc bl  #-16012       ; -> 0xfffffff008f73930
```

That `w1 = 8` is the only such setup in the iterate worker (`0xfffffff008f7c7dc`), so every parse on
this path skips exactly the generic-blob header. The on-disk layout confirms the arithmetic closes:

```
offset 0     fa de 71 72        CSMAGIC_EMBEDDED_DER_ENTITLEMENTS
offset 4     00 00 06 2f        CS_GenericBlob length == 0x62f
offset 8     70 82 06 23        DER: SEQUENCE, long-form len, 0x623 content
8 (header) + 4 (DER tag/len) + 0x623 = 0x62f   <- equals the slot length exactly
```

There is no room for truncation in this framing: every number closes.

Consequences for the diagnosis:

- **`p24`/`p32` will legitimately read `0x62f`, not `0x627`.** Do not treat that as an 8-byte
  truncation; the header is part of what AMFI is given, by design.
- Since CE demonstrably skips the 8 bytes and the framing closes exactly, a runtime length of `0x62f`
  with error 2 still present means the failure is *after* the hand-off — in CE's own parsing or in the
  object it builds — not a length our code-signing path got wrong.
- The earlier "0x627 vs 0x620" framing was comparing the wrong two numbers (payload size vs a garbage
  register). The real pair to compare is **`0x62f` (what AMFI gets) vs `0x62f` (what ships)** — and
  those already match on paper.

Reproduce the measurement:

```
python3 csslot.py /path/to/launchd --list
python3 csslot.py /path/to/launchd --slot 0x7 --raw der-full.bin --plist der-payload.bin
```

### How the file was obtained (for the record)

`ipsw extract --files` only reaches SystemOS through `apfs-fuse`, and `ipsw`'s pure-Go AEA
decryption needs a writable temp large enough for the 2.35 GB decrypted DMG — it fails with
`no space left on device` on the 24 GB `/tmp` tmpfs once the 11.3 GB IPSW is also there. Set
`TMPDIR` to real disk:

```
ipsw extract --fcs-key -o /tmp/fcs iPhone15,4_27.0.1_24A446_Restore.ipsw
ipsw extract --files --pem-db <pem-json> --pattern '.*/sbin/launchd$' -o <out> <ipsw>

# pem-json is {"043-69467-710.dmg.aea": "<PEM text with \n escapes>"}
TMPDIR=<disk-backed-dir> ipsw extract ...
```

The AppOS DMG (`043-69297-793.dmg`) is tried first and is only the Safari cryptex — no `/sbin`; the
file comes from SystemOS (`043-69467-710.dmg.aea`). `apfs-fuse` itself is not needed for portability
now that it is installed at `/usr/local/bin/apfs-fuse`, but it must be on `PATH` or in
`IPSW_APFS_FUSE_PATH` for `ipsw` to find it.

<details><summary>Original task text (kept for reference)</summary>

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
tag/length framing is well-formed. (This has since been done for the stock IPSW copy — 0x627 payload
in a 0x62f slot, well formed. The old "compare against the 0x620 the probe reported" instruction is
void: that figure was a varargs-bug artefact, not a measurement.)

</details>

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
