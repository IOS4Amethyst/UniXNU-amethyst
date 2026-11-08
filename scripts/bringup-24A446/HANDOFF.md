# Handoff: iOS 27 24A446 bring-up — now reaching userspace; blocker is the file pager

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
