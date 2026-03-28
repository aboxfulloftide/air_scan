#!/usr/bin/env python3
"""
Boot-time GPS time sync.

- Always ensures NTP is enabled on entry.
- If gpsd provides a valid fix within the timeout, disables NTP and sets
  the system clock from GPS.
- If no fix is obtained, leaves NTP running.

Designed to run as a oneshot systemd service after gpsd starts.
"""

import json
import socket
import subprocess
import sys
import time

GPS_HOST    = "127.0.0.1"
GPS_PORT    = 2947
FIX_TIMEOUT = 1800   # seconds to wait for a GPS fix (up to 30 min for cold start)


def ntp(enable: bool):
    val = "true" if enable else "false"
    subprocess.run(["timedatectl", "set-ntp", val], check=True)
    print(f"[timesync] NTP {'enabled' if enable else 'disabled'}")


def get_gps_fix(timeout: float) -> str | None:
    """Return ISO8601 UTC string from gpsd TPV, or None if no fix."""
    try:
        s = socket.create_connection((GPS_HOST, GPS_PORT), timeout=5)
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        s.settimeout(5)
    except Exception as e:
        print(f"[timesync] gpsd connect failed: {e}")
        return None

    buf      = ""
    deadline = time.time() + timeout

    try:
        while time.time() < deadline:
            try:
                chunk = s.recv(4096).decode(errors="replace")
            except socket.timeout:
                continue
            except Exception:
                break
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                try:
                    obj = json.loads(line.strip())
                except Exception:
                    continue
                if obj.get("class") == "TPV" and obj.get("mode", 0) >= 2:
                    t = obj.get("time")
                    if t:
                        return t
    finally:
        try:
            s.close()
        except Exception:
            pass

    return None


def main():
    # Always start with NTP on — ensures time is tracked if GPS never arrives
    ntp(True)

    print(f"[timesync] Waiting up to {FIX_TIMEOUT}s for GPS fix…")
    gps_time = get_gps_fix(FIX_TIMEOUT)

    if not gps_time:
        print("[timesync] No GPS fix — leaving NTP active")
        sys.exit(0)

    print(f"[timesync] GPS fix: {gps_time}")

    # Parse and reformat for `date -s`
    try:
        ts     = gps_time.rstrip("Z").split(".")[0]   # "2026-03-18T02:00:00"
        tm     = time.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        utc_str = time.strftime("%Y-%m-%d %H:%M:%S", tm)
    except Exception as e:
        print(f"[timesync] Time parse error: {e} — leaving NTP active")
        sys.exit(1)

    # Disable NTP so date -s is accepted and sticks
    ntp(False)

    r = subprocess.run(["date", "-u", "-s", utc_str], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[timesync] date -s failed: {r.stderr.strip()} — re-enabling NTP")
        ntp(True)
        sys.exit(1)

    print(f"[timesync] Clock set to {utc_str} UTC from GPS")
    sys.exit(0)


if __name__ == "__main__":
    main()
