# Amethyst touch evidence

## Verified source wiring, not live controller identification

Source: local Xiaomi kernel tree
`qcom/proprietary/devicetree/qcom/amethyst-sm7635-xiaomi-touch.dtsi`.

| Resource | Source value |
| --- | --- |
| Transport | QUPv3 wrapper0 SE0 SPI, CS0, maximum 15MHz |
| Engine | volcano-qupv3.dtsi: SPI at 0xa80000, size 0x4000 |
| Bus pins | GPIO0/1/2/3: MISO/MOSI/CLK/CS |
| Touch IRQ | GPIO70, DTS interrupts flags 0x2008 |
| Reset | GPIO87 |
| Rails | L13B 3.224V, L23B 1.8V |
| Possible controllers | Goodix GT9916R or FocalTech FT3683G |
| Coordinates | 122000 x 271200; controller-specific super-resolution scaling |

The same node contains both controller configurations. It does not identify
which controller is fitted to this phone. Do not guess based on panel size or
which driver modules compile. Goodix uses super-resolution factor100; FocalTech
lists factor16 with display coordinates122000x271200. Decode according to the
selected Linux driver, not arbitrary divisions.

AsteroidsTouchProbe.cpp supports FT3683G on the same SPI engine, but uses IRQ19
and reset20, NOT Amethyst's70/87. It also has board power/DMA/SMMU setup. Do not
enable it unchanged. Existing Amethyst UI image has no touch/HID probe enabled.

## Next read-only hardware evidence

While booted into normal Android (where touch works), capture
`adb -s 42fe0af2 shell cat /proc/bus/input/devices`. Controller-specific input
name and sysfs parent/driver binding are needed before SPI writes, resets,
firmware commands or enabling a provider. Recovery may not bind touch and can
therefore be inconclusive. Host adb currently has no attached device; Windows
adb was previously used successfully. No hardware-touch test or new image yet.

## Live Android evidence supplied by user

`/proc/bus/input/devices` reports actual multitouch input `goodix_ts`,
Bus0000/Vendorbeef/Productdead/Version28bb, handlers event7, properties2,
EV=b, ABS=26d800000000003, virtual input8. A separate `uinput-goodix`
device has keys only and is not the touchscreen. This identifies the active
Goodix driver family; it does not by itself read out the exact silicon ID.

Linux goodix_brl_spi.c implements address-based Goodix reads with opcode F1,
four big-endian address bytes and controller-dependent dummy bytes (see
BerlinD qualification below), then response data;
writes use F0 and four address bytes. This differs from the Asteroids FT3683G
register protocol. No SPI commands or pin/reset manipulation made here.

The driver publishes `chip_info` on platform device goodix_ts. Its read handler
uses the existing driver's read_version/get_ic_info operations and exposes ROM
PID/version, patch PID/version, sensor/config IDs. Next capture that and the
bound SPI driver from normal Android; do not use reg_rw, reset or send_cfg.

## Android controller identity and coordinate contract

User read `/sys/devices/platform/goodix_ts.0/chip_info`: ROM PID BERLIN,
ROM VID020004, patch PID GEST, patch VID00010104, sensorid255,
config_id66ebe19f/config_version25. SPI0.0 is bound to xiaomi,touch-spi at
a80000. Event7 exports ten slots, X0..121999, Y0..271199, direct input.

Xiaomi's driver has a TOUCH_THP_SUPPORT path and initializes enable_touch_raw=1
when compiled in. Its ordinary coordinate decoder reads 8-byte records with
16-bit LE x/y, insufficient to represent the exported ranges directly. Thus
Android input limits do not establish the ordinary SPI packet coordinate units.
goodix_htc_enable switches raw/coordinate modes using commands0x90/0x91;
send_cmd handles acknowledgements. Touch-data and command addresses come from
firmware IC-info, not fixed FocalTech registers. Need qualify normal-coordinate
scaling and actual THP mode before connecting HID. No device mode changes,
firmware uploads, resets or raw register writes performed.

## Normal coordinate decoder: source qualification

Found explicit scaling in goodix_ts_core.c:1562–1575: x/y decoded by
goodix_parse_finger are multiplied by super_resolution_factor immediately
before Linux input reporting. DTS supplies100. This establishes why ordinary
coordinate mode can use native-pixel 16-bit x/y while Android exports x100
ranges; raw/THP mode remains distinct and is not qualified by this observation.

Added GoodixBerlinPacket.h for normal finger-coordinate mode only: 8-byte
header, count low nibble, 8-byte points, ID high nibble, LE16 x/y/width,
LE16 byte-sum checksums, ten-contact limit, duplicate/type/bounds rejection.
Invalid/truncated input leaves the last valid output untouched; valid count0
means all contacts lifted. Unknown point types, gestures/requests and stylus
fail closed. Readable synthetic test check-goodix-packet.c covers valid edges,
checksum, truncation, duplicate ID, bad ID/type/range/count and empty frames.
cc -std=c11 -Wall -Wextra -Werror -fsanitize=address,undefined: build/run exit0.
No SPI transport, mode switching, HID publication or hardware claims yet.

## BerlinD SPI framing qualified from the selected compatible

goodix_get_ic_type selects BerlinD for compatible containing `9916` or `spi`.
The runtime node reports xiaomi,touch-spi. goodix_brl_spi probe selects the
ordinary read function for BerlinD; only BerlinA uses read_bra. Therefore
BerlinD read prefix is 8 bytes: F1, four BE address bytes, three FF bytes,
and response data at RX offset8. BerlinA uses offset9/four dummy bytes.
SPI mode0 is set by the Android driver. GoodixBerlinSpi.h now constructs
bounded BerlinD read frames; no hardware transaction is performed by it.
Synthetic framing/capacity/address-overflow tests pass with ASan+UBSan.
Packet decoder tests also rerun successfully. No touch image built.

## Bounded register-read adapter implemented (host only)

GoodixBerlinGeni.c reuses geni_spi_transfer for one full-duplex CS0 transaction;
no new GENI engine or board MMIO implementation. It refuses absent callbacks,
non-SPI identity, non-mode0/CS0 settings, zero poll bound, and unconfigured or
above-DTS-limit clocks. Board code still must own initialization/mappings and
serialize the supplied scratch buffers. Generic read helper caps payload1024,
checks address overflow and buffer overlap, and copies response bytes only on
transport success (timeout leaves output unchanged).

Framing/failure-preservation tests pass under ASan+UBSan; adapter builds with
host -Wall -Wextra -Werror and recorded startup.o.json ARM64 recipe (exit0).
This is not controller activation or identity-read hardware evidence. No DMA,
rails, reset, controller commands or HID publication performed. Before the
first hardware transfer: verify active rail resource identities (L13B/L23B,
not Asteroids L22B/L11B), stream ownership and live SE clock/FIFO state.

## Firmware identity reader (host only)

GoodixBerlinIdentity.h reads BerlinD version address0x10014, exactly28 bytes,
using the bounded SPI callback. Validates the original LE byte-sum checksum and
BERLIN ROM PID; extracts byte arrays for ROM/patch versions and sensor without
unaligned packed-struct reads. Refuses malformed/non-BERLIN input and preserves
previous output on transfer/checksum failure. No upload/reset or mode command.
Synthetic identity test matches the supplied Android identity layout and checks
timeout/corruption/wrong-controller/truncation. ASan+UBSan build/run exit0.
Actual read remains blocked on board transport ownership/clock/power setup;
this does not claim successful reads from the phone.

## Transport preflight checks and board resource qualification

Amethyst's volcano-regulators.dtsi maps L13B to RPMh resource ldob13 and L23B
to ldob23 (pm7550_l13/l23). Touch override sets3.224V/1.8V. volcano-qupv3.dtsi
lists wrapper stream0x563 and GPI stream0x576. These are source facts, not a live
ownership snapshot or permission to reconfigure an active shared SMMU context.

GENI adapter now refuses busy master status, disabled FIFO without DMA,
incomplete DMA callbacks, short/overflowing/overlapping DMA extents and non32-bit
FIFO geometry. Board still owns mapping reachability and serialization; a
status check is not a substitute for an exclusive controller lock.
check-goodix-geni.c uses a synthetic transport/MMIO model, exercises FIFO and
DMA preflight plus busy/missing/alias/overflow cases; ASan+UBSan exit0. No actual
MMIO, clocks, regulators, SMMU changes or device SPI commands performed.
