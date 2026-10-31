# Automation Toggle (Kill Switch)

One flag turns off **every** automatic behavior on the mobile Pi — WiFi/BLE
scanning, the hotspot, the status page, GPS boot-time clock sync, and the
wired sync upload to MySQL. Use it whenever the Pi is needed for something
unrelated and it must stay quiet.

Currently implemented and installed on `mobile-pi`, not yet committed to git
as of 2026-10-31 (see **Status** below).

---

## Usage

```bash
sudo air-scan disable            # turn everything automatic off
sudo air-scan disable "reason"   # optional note, recorded in the flag file
sudo air-scan enable             # turn it all back on
sudo air-scan toggle             # flip whichever way it is now
     air-scan status             # show flag state + every unit (no sudo needed)
     air-scan check              # exit 0 = automation allowed, 2 = disabled
```

`air-scan` is `scanners/air_scan_ctl.sh`, symlinked into `/usr/local/bin/air-scan`
so it works from any directory.

---

## How it's enforced

The flag is a single file: **`/etc/air_scan/disabled`**. Plain file → survives
reboots, needs no running daemon. Its contents are just a note of when/why/who:

```
air_scan automation disabled
set_at=2026-10-31T19:52:41Z
set_by=matheau
reason=testing unrelated project
```

Every automatic entry point calls `air-scan check` and backs off if it exits 2:

| Entry point | Gate |
|---|---|
| `mobile-scanner.service` | `ExecCondition=` — skipped at boot or manual start |
| `mobile-ble-scanner.service` | `ExecCondition=` |
| `mobile-status.service` | `ExecCondition=` |
| `gps-timesync.service` | `ExecCondition=` |
| `mobile-sync.service` (fires from the `99-mobile-sync.rules` udev rule on eth0-up, and from the net watcher) | `ExecCondition=` **plus** a check inside `mobile_sync.py` itself |
| `mobile_net_watcher.sh` | Checks the flag every poll (2s); idles instead of starting scanners/hotspot/sync while set |

**Why `check` exits 2, not 1:** `mobile-sync.service` sets
`SuccessExitStatus=0 1`, so an `ExecCondition` that exited 1 would have been
treated as *success* and run anyway — this was caught by testing, not
designed in up front. Exit 2 isn't in that success list, so it correctly
blocks the unit.

## What `disable` actually does

- Writes the flag file.
- Stops any of the gated units that are currently running (`mobile-scanner`,
  `mobile-ble-scanner`, `mobile-status`, `gps-timesync`, `mobile-sync`).
- Leaves `mobile-net-watcher` running — it keeps polling ethernet state but
  just idles instead of acting, so `enable` resumes it within ~2s with no
  service restart needed.
- Leaves the hotspot connection up if it's currently up (dropping it could
  cut your own SSH access to the Pi) — drop it manually with
  `nmcli con down Hotspot` if needed.

## What `enable` does

- Removes the flag file.
- Restarts `mobile-status` immediately (it's the always-on status page).
- Does **not** force-start the scanner, BLE scanner, or hotspot — those are
  the net watcher's job and it'll start them on its next poll if ethernet is
  actually unplugged. `gps-timesync` only runs at boot, so it won't re-run
  until next reboot.

## Deliberate escape hatches (still work with the flag set)

- `python3 scanners/mobile_sync.py --force` — manual sync ignoring the flag.
- `mobile-scanner-test.service` / `mobile-ble-scanner-test.service` — the
  manual, never-auto-start test units are untouched by the flag.

---

## Files involved

- `scanners/air_scan_ctl.sh` — the `air-scan` command itself (disable/enable/toggle/status/check)
- `scanners/mobile_net_watcher.sh` — polls the flag each cycle, idles when set
- `scanners/mobile_sync.py` — checks `disabled_by()` at the top of `__main__`
- `scanners/mobile-scanner.service`, `mobile-ble-scanner.service`,
  `mobile-status.service`, `mobile-sync.service`, `gps-timesync.service` —
  each has the `ExecCondition=.../air_scan_ctl.sh check` line
- `README.md` → "Kill Switch — Disabling All Automation" section has the
  same quick-reference table

## Status / to-do

- All of the above is live and tested on `mobile-pi` as of 2026-10-31.
- Changes are **sitting in the working tree, not committed yet** (alongside
  the pre-existing `mobile_sync.py` insert_sql dedup). Run `git status` in
  `air_scan/` and commit when ready — the files listed above are the full
  diff/new-file set.
- `/etc/systemd/system/*.service` on the Pi were manually `cp`'d from the repo
  copies during setup; if you edit a `.service` file in the repo again later,
  remember to `sudo cp` it to `/etc/systemd/system/` and
  `sudo systemctl daemon-reload` — editing the repo copy alone does not
  change the installed unit.
