# Wi-Fi on amethyst: WCN6750 (qca6750) bring-up plan

**WPSS boots on b43 and GLINK version agreement was observed on b49/b50/b51/b53.** PAS 6 auth_and_reset returned 0;
SMP2P 0x269 and GLINK RX FIFO 480 appeared 100 ms later. IPCRTR OPEN/ACK is the next checkpoint. Earlier: stage 1 ran on b36: the APPS-WPSS partition holds GLINK 478/479 (AP side) but no 480 and no SMP2P items,
so the WPSS is not running. Stage 2 written (`package/touch/AmethystWifiProbe.cpp`): it boots
the WPSS with PAS id 6 when the GPU addon carries `wpss.mdt` + `wpss.b01..b10` (`addon-trustcache.py addfiles`, stored zlib).
The UEFI PIL route (uefipil.cfg `FULL_WPSS`, SubsysID 6) is out while PILDxe/UFSDxe stay disabled in amethystPkg.

## Hardware (stock vendor_boot DT, Volcano msm-id 0x27c = soc_id 636 read on the phone, and dtbo overlay)

| Item | Value | Source |
| --- | --- | --- |
| Chip | WCN6750 (FastConnect 6700), firmware dir `qca6750/` | dtbo `qcom,wcn6750`, `bt_wcn6750` |
| WLAN firmware CPU | WPSS, `remoteproc-wpss@9bb00000`, `qcom,volcano-wpss-pas`, `firmware-name = "qca6750/wpss.mdt"` | dt0.dts:5401 |
| WPSS memory | `wpss_region@9bb00000` size 0x1900000 (no-map) | dt0.dts:281 |
| WLAN MSA | `wlan_msa_region@a6400000` size 0xc00000 (no-map), `qcom,wlan-msa-fixed-region` | dt0.dts:335 |
| WPSS power | cx/mx supplies (rpmh levels), `qcom,signal-aop` + `qcom,qmp` (AOSS QMP), clock xo | dt0.dts:5401 |
| WPSS interrupts | wdog SPI 0x243; fatal/handover/ready/stop-ack/shutdown-ack from SMP2P `slave-kernel` bits 0,2,1,3,7 | dt0.dts:5417 |
| SMP2P (WPSS) | smem items in 0x269 / out 0x268, local pid 0, remote pid 13, IPCC mailbox client 0x18 signal 2 | dt0.dts:5378 |
| GLINK edge | transport smem, remote pid 13, IPCC 0x18 signal 0, channel `IPCRTR` (QRTR), intents 0x800x5, 0x2000x3, 0x4400x2 | dt0.dts:5433 |
| WLAN SMP2P | out `wlan`, `wlan_soc_wake`, `wlan_ep_power_save`; in `wlan_soc_wake` (soc-wake-ack), wlan_1_in (force-fatal, early-crash) | dtbo |
| WLAN block | `reg = <0x17110040 msi_addr>, <0xc0000000 0x10000 smmu_iova_ipa>`; 32 SPIs 0x300..0x31f (GIC 800..831); `iommus <SMMU 0x1400 0x1>`; IOVA pool 0xb0000000 + 256 MiB | dtbo o1.dts:10462 |
| WLAN rails | vdd-cx-mx, vdd-1.8-xo, vdd-1.3-rfa, vdd-ipa-2p2 (regulator configs in dtbo) | dtbo |

Firmware (NON-HLOS.bin, FAT `::/image/qca6750/`, ~12.8 MB folder): `wpss.mdt` + `wpss.b00..b12` (~8.2 MB), board data
`bdwlan.*` and `bd_*16u*.elf` (~60 KB each), `Data.msc`, `wpss.qdb`. Vendor/device-specific: never commit; record in
`proprietary-files.txt` like the GPU firmware.

## Linux reference

**Stage 3 version-only probe:** `amethyst-wifi=3` runs the b43 boot path, then polls the WPSS GLINK edge for up to five seconds. Row 42 `s3 version rc=1 sent=1 replied=1 agreed=1` means v1 with no optional features; b49, b50, b51 and b53 recorded this result. `rc=0` is a timeout. Stage 2 remains the default.

**IPCRTR OPEN/ACK candidate (host only):** `amethyst-wifi=4` uses the same five-second budget for version negotiation and a single peer-initiated `IPCRTR` handshake. It retains one early OPEN and uses READ_NOTIF if channel TX is blocked. Row 42 `s4 IPCRTR rc=1 v=1/1/1 peer=<nonzero> ack=1 open=1 last=4` means the peer acknowledged the host's OPEN; `rc=0` means incomplete, and `rc=-1/-2/-3` means invalid ring, unsupported command, or invalid channel control. `last=65535` means no RX header was seen. This is not a live QRTR endpoint: no intents, discovery, WLFW requests or traffic. Unsupported frames stay queued. No target build or phone test.

This is Linux **ath11k** (`drivers/net/wireless/ath/ath11k/ahb.c`, hw rev `ATH11K_HW_WCN6750_HW10`) on SC7280-class SoCs
(`qcom,wcn6750-wifi`, ChromeOS herobrine), with WPSS booted by `qcom_q6v5_pas` (`qcom,sc7280-wpss-pas`, PAS id 6). Control path:
QMI WLFW over QRTR over GLINK-SMEM to WPSS; data path: ath11k copy engines + DP SRNG rings in host memory, interrupts as SPIs.
Not ath12k (NativeWifi/Ath12kWifi is PCIe+MHI WCN7850) and not ath10k (Trogdor WCN3990 is SNOC with the WLAN FW on MPSS).

## Reusable UniXNU / community code

1. **UniXNU/NativeWifi** (Ath12kWifi): Linux v7.0.14 cfg80211/mac80211 + ath12k on XNU through a Linux shim, `org.unixnu.wifi.control`,
   wpa_supplicant 2.12 driver, `WifiNet.cpp` en0 / Apple80211 integration. The pinned Linux tree also contains ath11k; the shim
   (skb, NAPI, workqueues, DMA, firmware loader, qmi_interface, qrtr) is the part to reuse. Board callbacks today assume PCIe/MHI.
2. **chiba233/Trogdor-ipadOS** `platform/coachz-prelink/pending/wifi` + `docs/plans/wifi.md`: hardware-proven SMEM partition reader,
   SMP2P in/out, GLINK-SMEM (FIFO, version/channel handshake, intents), QRTR (node, hello, service announce), WLFW QMI (CAP, MSA,
   BDF download, FW ready), then WMI/HTT up to scan, WPA2 join, DHCP and Safari, plus `CoachzWifiNet.cpp` (from sheng WifiNet.cpp).
   Their WLAN FW sat on MPSS (MBA self-auth because TF-A lacked PAS); ours is on WPSS and amethyst's TZ has PAS (the GPU zap shader
   already uses PAS init_image / mem_setup / auth_and_reset through `sheng_smc_call`, GpuPowerProbe.cpp zapStart).
3. **UniXNU/QcomBaseband**: QMI/QRTR protocol pieces (board supplies SMEM/SMP2P/GLINK).

## Firmware delivery (no loader change)

DarwinBoot already loads `Payloads/kernel.macho.gpuaddon` and records it in `/chosen/memory-map/GpuAddon`
(DarwinBoot.c:684, NativeRamBlock.cpp:435). It is an NGPUADD2 file container (`scripts/bringup-24A446/addon-trustcache.py` reads it).
Append `wifi/qca6750/wpss.mdt`, `wpss.bNN` and the board file to it; the kernel finds them in that physical range for PAS.

## Bring-up milestones (probe modes above, RAM-only; boot args are ~1015/1023 chars)

1. **SMEM/SMP2P inventory** (read-only): parse SMEM, list the WPSS partition, read SMP2P 0x269 inbound state. Port Trogdor's reader
   with Volcano addresses (SMEM base from the DT `qcom,smem` region).
2. **WPSS boot**: AOSS QMP "wpss" load state + cx/mx RPMh votes, `qcom_mdt_load` of wpss.mdt into wpss_region, PAS init_image(6) /
   mem_setup / auth_and_reset, wait for the SMP2P `ready`/`handover` bits. Success = WPSS creates its GLINK FIFO in SMEM.
3. **GLINK + QRTR**: Trogdor's GLINK-SMEM (IPCC client 0x18 signal 0 instead of the 7c's APCS doorbell) and QRTR; expect WPSS to
   announce WLFW (service 69).
4. **WLFW QMI**: ath11k `qmi.c` for WCN6750 (host cap, MSA from wlan_msa_region, BDF from bdwlan/bd_*.elf by board id, m3 not used
   on wcn6750, FW ready).
5. **ath11k data path**: either compile ath11k (ahb.c wcn6750 path) inside NativeWifi's Linux shim with amethyst board callbacks
   (registers, 32 SPIs, SMMU SID 0x1400 IOVA pool, DMA) — preferred, matches upstream's "do not reinvent" rule — or Trogdor-style
   hand port. Then scan, join, en0 via WifiNet.cpp, wifid.

Per the new upstream rule (https://unixnu.com/contributing/repository) the driver lives in its own repo, added to the board repo as
a `drivers/<Name>` submodule and tagged `unixnu`.
