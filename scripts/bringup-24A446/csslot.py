#!/usr/bin/env python3
"""Extract SuperBlob slots (DER entitlements, entitlements, CodeDirectory) from a Mach-O.

Reads LC_CODE_SIGNATURE from the Mach-O load commands, then parses the embedded
CS_SuperBlob index. Used to compare launchd's on-disk DER entitlements blob
against what the kernel hands AMFI at runtime.

Usage:
  csslot.py <macho> --list
  csslot.py <macho> --slot 0x5 [--raw out.bin] [--plist out.plist]

Slot magic values (csblob.h):
  0x0  CSSLOT_CODEDIRECTORY
  0x1  CSSLOT_INFOSLOT
  0x2  CSSLOT_REQUIREMENTS
  0x3  CSSLOT_RESOURCEDIR
  0x4  CSSLOT_APPLICATION
  0x5  CSSLOT_ENTITLEMENTS        (XML plist)
  0x7  CSSLOT_DER_ENTITLEMENTS    (DER, 0xfade7172)
  0x10000+ CSSLOT_ALTERNATE_CODEDIRECTORIES
"""
import argparse
import struct
import sys

SLOT_NAMES = {
    0x0: "CSSLOT_CODEDIRECTORY",
    0x1: "CSSLOT_INFOSLOT",
    0x2: "CSSLOT_REQUIREMENTS",
    0x3: "CSSLOT_RESOURCEDIR",
    0x4: "CSSLOT_APPLICATION",
    0x5: "CSSLOT_ENTITLEMENTS",
    0x6: "CSSLOT_RESERVED",
    0x7: "CSSLOT_DER_ENTITLEMENTS",
    0x1000: "CSSLOT_SIGNATURESLOT",
}

LC_CODE_SIGNATURE = 0x1D
LC_SEGMENT_64 = 0x19


def slot_name(t):
    if t in SLOT_NAMES:
        return SLOT_NAMES[t]
    if 0x10000 <= t <= 0x10005:
        return f"CSSLOT_ALTERNATE_CODEDIRECTORIES[{t - 0x10000}]"
    return f"unknown(0x{t:x})"


def find_code_signature(data):
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic == 0xFEEDFACF:
        off = 0
    elif magic == 0xCAFEBABE:
        # fat binary: take the first arch
        nfat = struct.unpack_from(">I", data, 4)[0]
        arch_off = struct.unpack_from(">I", data, 16)[0]
        off = arch_off
        print(f"fat binary with {nfat} arch(s); using first at 0x{arch_off:x}", file=sys.stderr)
    else:
        raise SystemExit(f"not a Mach-O (magic 0x{magic:08x})")

    ncmds = struct.unpack_from("<I", data, off + 16)[0]
    p = off + 32  # sizeof(mach_header_64)
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, p)
        if cmd == LC_CODE_SIGNATURE:
            dataoff, datasize = struct.unpack_from("<II", data, p + 8)
            return dataoff, datasize
        if cmdsize == 0:
            break
        p += cmdsize
    raise SystemExit("no LC_CODE_SIGNATURE")


def parse_superblob(data, base, size):
    magic, length, count = struct.unpack_from(">III", data, base)
    if magic != 0xFADE0CC0:
        raise SystemExit(f"bad SuperBlob magic 0x{magic:08x} (expected 0xfade0cc0)")
    entries = []
    for i in range(count):
        t, o = struct.unpack_from(">II", data, base + 12 + i * 8)
        blob = base + o
        bmagic, blen = struct.unpack_from(">II", data, blob)
        entries.append((t, o, blob, bmagic, blen))
    return length, entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("macho")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--slot", type=lambda s: int(s, 0))
    ap.add_argument("--raw")
    ap.add_argument("--plist")
    args = ap.parse_args()

    with open(args.macho, "rb") as f:
        data = f.read()

    dataoff, datasize = find_code_signature(data)
    print(f"LC_CODE_SIGNATURE dataoff=0x{dataoff:x} datasize=0x{datasize:x} "
          f"({datasize} bytes)", file=sys.stderr)

    length, entries = parse_superblob(data, dataoff, datasize)
    print(f"SuperBlob length={length} count={len(entries)}", file=sys.stderr)

    if args.list or args.slot is None:
        for t, o, blob, bmagic, blen in entries:
            print(f"  slot 0x{t:<7x} {slot_name(t):40s} off=0x{o:<7x} "
                  f"magic=0x{bmagic:08x} len=0x{blen:x} ({blen})")

    if args.slot is not None:
        match = [e for e in entries if e[0] == args.slot]
        if not match:
            raise SystemExit(f"slot 0x{args.slot:x} not present")
        t, o, blob, bmagic, blen = match[0]
        raw = data[blob:blob + blen]
        print(f"slot 0x{t:x} ({slot_name(t)}): magic=0x{bmagic:08x} "
              f"len=0x{blen:x} ({blen})", file=sys.stderr)
        if args.raw:
            with open(args.raw, "wb") as f:
                f.write(raw)
            print(f"wrote {args.raw} ({len(raw)} bytes)", file=sys.stderr)
        # DER entitlements: the payload is a separate DER blob (magic 0xfade7172)
        if bmagic == 0xFADE7172:
            plen = struct.unpack_from(">I", raw, 4)[0]
            payload = raw[8:plen]
            print(f"DER payload length=0x{plen - 8:x} ({plen - 8} bytes)", file=sys.stderr)
            if args.plist:
                with open(args.plist, "wb") as f:
                    f.write(payload)
                print(f"wrote {args.plist}", file=sys.stderr)
            else:
                sys.stdout.buffer.write(payload)
        elif bmagic == 0xFADE7171:
            plen = struct.unpack_from(">I", raw, 4)[0]
            payload = raw[8:plen]
            if args.plist:
                with open(args.plist, "wb") as f:
                    f.write(payload)
                print(f"wrote {args.plist}", file=sys.stderr)
            else:
                sys.stdout.buffer.write(payload)


if __name__ == "__main__":
    main()
