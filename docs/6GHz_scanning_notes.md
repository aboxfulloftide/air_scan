# 6GHz Monitor Mode — Attempted Fixes & Outcome

## Context

Hardware: NetGear USB WiFi Adapter (USB ID `0846:9072`), MediaTek MT7925 chipset
Driver: `mt7925u` (kernel `6.12.62+rpt-rpi-v8`)
Firmware: `mediatek/mt7925` dated `2024-11-04`
OS: Raspberry Pi OS (Debian-based), aarch64

The adapter lists 6GHz frequencies (5955–7115 MHz) as available via `iw phy`:
```
* 5955.0 MHz [1] (12.0 dBm) (no IR)
* 5975.0 MHz [5] (12.0 dBm) (no IR)
...
```
All 6GHz channels carry two flags:
- `(no IR)` — No Initiating Radiation: adapter cannot transmit on these channels
- `PASSIVE-SCAN` in the regulatory domain — passive receive only

Attempting to switch to a 6GHz channel in monitor mode fails:
```
$ iw dev wlan1 set freq 5955
(extension) channel is disabled
command failed: Invalid argument (-22)
```

---

## Fix Attempt 1 — World Regulatory Domain via modprobe

**Goal:** Remove PASSIVE-SCAN restriction by switching to the world regulatory domain (`00`)

**Steps taken:**
```bash
# Set world domain at kernel module load time
echo 'options cfg80211 ieee80211_regdom=00' | sudo tee /etc/modprobe.d/cfg80211.conf

# Also added to wlan1-monitor.service ExecStart:
iw reg set 00
```

**Result:** FAILED — worse than before
- The world regulatory domain (`00`) does not include the 6GHz band at all
- `iw dev wlan1 set freq 5955` returned `Invalid argument (-22)`
- 2.4GHz and 5GHz continued to work normally

**Reverted:**
```bash
sudo rm /etc/modprobe.d/cfg80211.conf
sudo iw reg set US
# Restored wlan1-monitor.service to original (no iw reg set line)
```

---

## Fix Attempt 2 — US Regulatory Domain (baseline)

The US regulatory domain includes 6GHz:
```
(5925 - 7125 @ 320), (N/A, 12), (N/A), NO-OUTDOOR, PASSIVE-SCAN
```
`PASSIVE-SCAN` means receive-only, which is exactly what monitor mode requires.
However, the `mt7925u` driver still rejects the channel switch with `Operation not permitted (-1)`
even when run as root. This is a driver-level enforcement issue, not purely regulatory.

---

## Current Status — Driver Limitation

**Root cause:** The `mt7925u` driver on this kernel version does not support channel switching
to 6GHz frequencies in monitor mode. The `no IR` flag causes the kernel wireless stack to
block the switch regardless of regulatory domain. This is a known limitation of the MT7925
on Linux and may be resolved in a future kernel or driver update.

**Workaround implemented in `wifi_scanner.py`:**
At startup the scanner live-tests each band by attempting an actual frequency switch.
Any band that fails is logged and dropped from the schedule automatically:
```
[BAND] 2.4GHz OK (2412 MHz)
[BAND] 5GHz OK (5180 MHz)
[BAND] 6GHz not available (5955 MHz) — command failed: Invalid argument (-22)
```
No manual configuration needed — the scanner self-adjusts.

---

## If 6GHz Becomes Available in Future

When the driver gains proper 6GHz monitor mode support, the scanner will detect it
automatically at startup with no code changes required.

To verify manually:
```bash
sudo iw dev wlan1 set freq 5955 && echo "6GHz works" || echo "still blocked"
```

If it works, restart the scanner service and it will include 6GHz in its schedule.

---

## Things That Were NOT Tried

- Patching or rebuilding the mt7925u driver from source
- Using a different regulatory domain (e.g. `BO`, `GT`) that may have fewer 6GHz restrictions
- Using a different USB WiFi adapter with confirmed 6GHz monitor mode support on Linux
  (e.g. some Alfa adapters with mt7921au chipset have better 6GHz monitor support)

---

## Files Modified During Troubleshooting

| File | Change | Status |
|------|--------|--------|
| `/etc/modprobe.d/cfg80211.conf` | Created with `ieee80211_regdom=00` | **Reverted / deleted** |
| `/etc/systemd/system/wlan1-monitor.service` | Added `iw reg set 00` to ExecStart | **Reverted** |

Current state of both files is identical to pre-troubleshooting baseline.
