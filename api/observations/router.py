import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from api.db import get_db

try:
    from scapy.all import conf as _scapy_conf
    def _oui_lookup(mac):
        try:
            m = _scapy_conf.manufdb._get_manuf(mac)
            return m if m else None
        except Exception:
            return None
except ImportError:
    def _oui_lookup(mac):
        return None

# Shared BLE tracker classifier (same rules the Pi / mobile scanners use).
# Imported as a namespace module from the repo root (the API runs with the
# project dir as CWD). Degrade gracefully if it can't be loaded.
try:
    from scanners.ble_classify import classify_tracker
except Exception:  # pragma: no cover - defensive
    def classify_tracker(manufacturer_data, service_uuids, service_data):
        return None

# Bluetooth SIG company identifiers → manufacturer name. The scapy OUI table is
# WiFi-MAC-only and useless for BLE random addresses, so for BLE rows we resolve
# the manufacturer from the company ID in the advertisement instead. Partial
# list covering the vendors we care about; unknown CIDs leave manufacturer NULL.
_BT_SIG_CID = {
    0x004C: "Apple",
    0x0075: "Samsung",
    0x00E0: "Google",
    0x0006: "Microsoft",
    0x0087: "Garmin",
    0x009E: "Bose",
    0x00B7: "Fitbit",
    0x0118: "Tile",
    0x038F: "Xiaomi",
    0x0157: "Huami",
}

router = APIRouter(prefix="/api/observations", tags=["observations"])
logger = logging.getLogger(__name__)

DEADLOCK_MAX_RETRIES = 3
DEADLOCK_RETRY_DELAY = 0.5  # seconds
DEVICE_BATCH_SIZE = 50


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def _parse_ble_fields(obs):
    """
    Parse the raw BLE advertisement strings a scanner sends into the shapes
    classify_tracker() expects. The on-wire string formats match what
    wifi_scanner.py and mobile_ble_scanner.py produce.

    Returns (manufacturer_data, service_uuids, service_data):
      manufacturer_data: {company_id_int: bytes}  from "XXXX:<hex>,..."
      service_uuids:     [lowercase uuid str]      from "uuid,uuid,..."
      service_data:      {uuid_str: bytes}         from "uuid:<hex>,..."
    """
    mfr = {}
    for entry in (obs.get("manufacturer_data") or "").split(","):
        if ":" in entry:
            cid_hex, payload = entry.split(":", 1)
            try:
                mfr[int(cid_hex, 16)] = bytes.fromhex(payload)
            except ValueError:
                pass
    svcs = [u.strip().lower() for u in
            (obs.get("adv_services") or "").split(",") if u.strip()]
    svc_data = {}
    for entry in (obs.get("adv_service_data") or "").split(","):
        if ":" in entry:
            uuid, payload = entry.split(":", 1)
            try:
                svc_data[uuid.strip().lower()] = bytes.fromhex(payload)
            except ValueError:
                pass
    return mfr, svcs, svc_data


def _ble_manufacturer(mfr):
    """Resolve a manufacturer name from parsed BLE manufacturer data, or None."""
    for cid in mfr:
        name = _BT_SIG_CID.get(cid)
        if name:
            return name
    return None


def _build_device_values(batch, device_rows):
    """Build multi-row VALUES clause and params for device upsert."""
    parts = []
    params = {}
    for i, mac in enumerate(batch):
        d = device_rows[mac]
        parts.append(
            f"(:mac_{i}, :type_{i}, :oui_{i}, :mfr_{i}, :rand_{i},"
            f" :ht_{i}, :vht_{i}, :he_{i}, :first_{i}, :last_{i})"
        )
        params.update({
            f"mac_{i}": mac, f"type_{i}": d["type"], f"oui_{i}": d["oui"],
            f"mfr_{i}": d["manufacturer"],
            f"rand_{i}": d["is_randomized"],
            f"ht_{i}": d["ht"], f"vht_{i}": d["vht"], f"he_{i}": d["he"],
            f"first_{i}": d["first_seen"], f"last_{i}": d["last_seen"],
        })
    sql = text(
        "INSERT INTO devices"
        "  (mac, device_type, oui, manufacturer, is_randomized,"
        "   ht_capable, vht_capable, he_capable, first_seen, last_seen) "
        "VALUES " + ", ".join(parts) +
        " ON DUPLICATE KEY UPDATE"
        "  last_seen    = GREATEST(last_seen,   VALUES(last_seen)),"
        "  manufacturer = COALESCE(manufacturer, VALUES(manufacturer)),"
        "  ht_capable   = GREATEST(ht_capable,  VALUES(ht_capable)),"
        "  vht_capable  = GREATEST(vht_capable, VALUES(vht_capable)),"
        "  he_capable   = GREATEST(he_capable,  VALUES(he_capable))"
    )
    return sql, params


def _build_ssid_values(ssid_rows):
    """Build multi-row INSERT IGNORE for ssids."""
    parts = []
    params = {}
    for i, (mac, ssid, ts) in enumerate(ssid_rows):
        parts.append(f"(:mac_{i}, :ssid_{i}, :ts_{i})")
        params.update({f"mac_{i}": mac, f"ssid_{i}": ssid, f"ts_{i}": ts})
    sql = text(
        "INSERT IGNORE INTO ssids (mac, ssid, first_seen) VALUES "
        + ", ".join(parts)
    )
    return sql, params


def _build_obs_values(batch, scanner_host):
    """Build multi-row INSERT for observations.

    BLE rows carry the extra advertisement columns (manufacturer_data,
    adv_services, adv_service_data, tx_power, tracker_type); WiFi rows leave
    them NULL. Conversely channel / freq_mhz are NULL for BLE.
    """
    parts = []
    params = {}
    for i, obs in enumerate(batch):
        parts.append(
            f"(:mac_{i}, :iface_{i}, :host_{i}, :sig_{i},"
            f" :ch_{i}, :freq_{i}, :pc_{i},"
            f" :md_{i}, :as_{i}, :asd_{i}, :tx_{i}, :tt_{i}, :ts_{i})"
        )
        params.update({
            f"mac_{i}":  obs["mac"],
            f"iface_{i}": obs.get("interface", "esp32-wifi"),
            f"host_{i}":  scanner_host,
            f"sig_{i}":   obs.get("signal_dbm"),
            f"ch_{i}":    obs.get("channel"),
            f"freq_{i}":  obs.get("freq_mhz"),
            f"pc_{i}":    obs.get("probe_count", 1),
            f"md_{i}":    obs.get("manufacturer_data"),
            f"as_{i}":    obs.get("adv_services"),
            f"asd_{i}":   obs.get("adv_service_data"),
            f"tx_{i}":    obs.get("tx_power"),
            f"tt_{i}":    obs.get("tracker_type"),
            f"ts_{i}":    obs.get("recorded_at"),
        })
    sql = text(
        "INSERT INTO observations"
        "  (mac, interface, scanner_host, signal_dbm,"
        "   channel, freq_mhz, probe_count,"
        "   manufacturer_data, adv_services, adv_service_data,"
        "   tx_power, tracker_type, recorded_at) "
        "VALUES " + ", ".join(parts)
    )
    return sql, params


@router.post("/upload")
async def upload_observations(body: dict, db: AsyncSession = Depends(get_db)):
    """
    Receive a batch of observations from a remote scanner (e.g. ESP32) that
    cannot write to MySQL directly.

    Expected body:
    {
        "scanner_host": "esp32-static-1",
        "observations": [
            {
                "mac":         "aa:bb:cc:dd:ee:ff",
                "device_type": "AP",          // "AP" or "Client"
                "signal_dbm":  -65,
                "channel":     6,
                "freq_mhz":    2437,
                "ssid":        "MyNetwork",   // optional, "" if none
                "ht":          false,
                "vht":         false,
                "he":          false,
                "probe_count": 7,             // optional, raw packets in 10s window (default 1)
                "recorded_at": "2026-03-17T01:00:00"
            },
            ...
        ]
    }

    BLE rows set "device_type": "BLE" and carry the raw advertisement fields
    instead of channel/ssid:
    {
        "mac":               "aa:bb:cc:dd:ee:ff",
        "device_type":       "BLE",
        "signal_dbm":        -72,
        "is_randomized":     true,            // optional; else derived from address
        "tx_power":          -8,              // optional
        "manufacturer_data": "004C:1219...",  // "XXXX:<hex>[,...]"
        "adv_services":      "0000feaa-0000-1000-8000-00805f9b34fb",
        "adv_service_data":  "0000feaa-...:40ab12...",
        "interface":         "esp32-ble",
        "recorded_at":       "2026-03-17T01:00:00"
    }
    The server classifies tracker_type from these raw fields — clients do not
    send it on this path.
    """
    scanner_host = body.get("scanner_host", "unknown")
    observations = body.get("observations", [])
    health = body.get("health")

    # ── Insert scanner health if provided ────────────────────────────────────
    if health:
        try:
            await db.execute(text("""
                INSERT INTO scanner_health
                    (scanner_host, mac, free_heap, min_free_heap,
                     uptime_ms, temperature_c, recorded_at)
                VALUES
                    (:host, :mac, :free_heap, :min_free_heap,
                     :uptime_ms, :temperature_c, NOW())
            """), {
                "host":          scanner_host,
                "mac":           health.get("mac"),
                "free_heap":     health.get("free_heap"),
                "min_free_heap": health.get("min_free_heap"),
                "uptime_ms":     health.get("uptime_ms"),
                "temperature_c": health.get("temperature_c"),
            })
            await db.commit()
        except Exception as e:
            logger.error("Failed to insert scanner_health: %s", e)
            await db.rollback()

    if not observations:
        return {"inserted": 0}

    # ── Aggregate device metadata across the batch ─────────────────────────────
    device_rows = {}
    for obs in observations:
        mac = obs.get("mac", "").lower()
        if not mac or mac == "ff:ff:ff:ff:ff:ff":
            continue

        ts = obs.get("recorded_at")
        dtype = obs.get("device_type", "Client")
        is_ble = (dtype == "BLE")

        if mac not in device_rows:
            first_byte = int(mac.split(":")[0], 16)
            if is_ble:
                # BLE: randomized = bit 6 of the first address byte, but trust
                # an explicit client flag if it sent one. Manufacturer comes
                # from the SIG company ID, not the (meaningless) OUI.
                rand = obs.get("is_randomized")
                rand = int(bool(rand)) if rand is not None else int(bool(first_byte & 0x40))
                manuf = _ble_manufacturer(_parse_ble_fields(obs)[0])
            else:
                # WiFi: randomized = locally-administered bit; OUI table lookup.
                rand = int(bool(first_byte & 0x02))
                manuf = _oui_lookup(mac)
            device_rows[mac] = {
                "type":          dtype,
                "oui":           mac[:8].upper(),
                "manufacturer":  manuf,
                "is_randomized": rand,
                "ht":  0, "vht": 0, "he": 0,
                "ssids": set(),
                "first_seen": ts,
                "last_seen":  ts,
            }

        d = device_rows[mac]
        if ts and (not d["last_seen"] or ts > d["last_seen"]):
            d["last_seen"] = ts
        if obs.get("ht"):  d["ht"]  = 1
        if obs.get("vht"): d["vht"] = 1
        if obs.get("he"):  d["he"]  = 1
        ssid = obs.get("ssid", "")
        if ssid:
            d["ssids"].add(ssid)
        # A later advertisement for the same device may be the first to carry
        # manufacturer data — backfill if we don't have a name yet.
        if is_ble and not d["manufacturer"]:
            d["manufacturer"] = _ble_manufacturer(_parse_ble_fields(obs)[0])

    # ── Write to DB with deadlock retry ────────────────────────────────────────
    for attempt in range(1, DEADLOCK_MAX_RETRIES + 1):
        try:
            # Scanner heartbeat
            await db.execute(text("""
                INSERT INTO scanners (hostname, label, is_active, last_heartbeat)
                VALUES (:host, :label, FALSE, NOW())
                ON DUPLICATE KEY UPDATE last_heartbeat = NOW()
            """), {"host": scanner_host, "label": scanner_host})

            # Upsert devices in batches of 50 (sorted by MAC to avoid deadlocks)
            sorted_macs = sorted(device_rows)
            for batch in _chunks(sorted_macs, DEVICE_BATCH_SIZE):
                sql, params = _build_device_values(batch, device_rows)
                await db.execute(sql, params)

            # Batch SSID inserts
            ssid_rows = []
            for mac in sorted_macs:
                d = device_rows[mac]
                for ssid in d["ssids"]:
                    if ssid and len(ssid) <= 32 and ssid.isprintable() and "\ufffd" not in ssid and "\x00" not in ssid:
                        ssid_rows.append((mac, ssid, d["first_seen"]))
            for batch in _chunks(ssid_rows, DEVICE_BATCH_SIZE):
                sql, params = _build_ssid_values(batch)
                await db.execute(sql, params)

            # Batch observation inserts
            valid_obs = []
            for obs in observations:
                mac = obs.get("mac", "").lower()
                if mac not in device_rows:
                    continue
                row = {**obs, "mac": mac}
                # Classify BLE trackers server-side (single source of truth).
                if device_rows[mac]["type"] == "BLE":
                    mfr, svcs, svc_data = _parse_ble_fields(obs)
                    row["tracker_type"] = classify_tracker(mfr, svcs, svc_data)
                valid_obs.append(row)
            for batch in _chunks(valid_obs, DEVICE_BATCH_SIZE):
                sql, params = _build_obs_values(batch, scanner_host)
                await db.execute(sql, params)

            await db.commit()
            return {"inserted": len(valid_obs), "devices": len(device_rows)}

        except Exception as e:
            await db.rollback()
            if "1213" in str(e) and attempt < DEADLOCK_MAX_RETRIES:
                logger.warning("Deadlock on upload attempt %d/%d, retrying...",
                               attempt, DEADLOCK_MAX_RETRIES)
                await asyncio.sleep(DEADLOCK_RETRY_DELAY * attempt)
                continue
            raise
