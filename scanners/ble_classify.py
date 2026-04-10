"""
Shared BLE tracker classification.

Identifies AirTags, SmartTags, Moto Tags, Tiles, and other BLE trackers
from advertisement data. Used by both wifi_scanner.py and mobile_ble_scanner.py.
"""

# Company IDs (little-endian key in bleak's manufacturer_data dict)
APPLE_CID   = 0x004C
SAMSUNG_CID = 0x0075

# Service UUID -> tracker label (16-bit UUIDs in full 128-bit form)
TRACKER_SVCS = {
    "0000feed-0000-1000-8000-00805f9b34fb": "Tile",
    "0000fd5a-0000-1000-8000-00805f9b34fb": "Samsung SmartTag",
    "0000fed8-0000-1000-8000-00805f9b34fb": "Google FMDN",
    "0000fe2c-0000-1000-8000-00805f9b34fb": "Google FastPair",
}

# Eddystone (0xFEAA) frame types
EDDYSTONE_UUID = "0000feaa-0000-1000-8000-00805f9b34fb"
EDDYSTONE_EID  = 0x40   # Ephemeral Identifier — used by Google FMDN (Moto Tag, etc.)
EDDYSTONE_UID  = 0x00
EDDYSTONE_URL  = 0x10
EDDYSTONE_TLM  = 0x20


def classify_tracker(manufacturer_data, service_uuids, service_data):
    """
    Return a short label if the advertisement matches a known tracker type,
    else None.

    Apple Find My (AirTag, etc.):
      manufacturer_data[0x004C][0] == 0x12

    Google FMDN (Moto Tag / Moto Tag 2 / Pixel Tag, etc.):
      Eddystone service UUID 0xFEAA + service data frame type 0x40 (EID).

    Tile: service UUID 0xFEED
    Samsung SmartTag: service UUID 0xFD5A or company 0x0075
    """
    # --- Apple ---
    apple = manufacturer_data.get(APPLE_CID) if manufacturer_data else None
    if apple and len(apple) >= 1:
        t = apple[0]
        if t == 0x12:
            return "Apple:FindMy"      # AirTag / Find My accessory
        if t == 0x02:
            return "Apple:iBeacon"
        if t == 0x10:
            return "Apple:NearbyInfo"  # iPhone / Mac proximity
        return "Apple"

    # --- Eddystone (0xFEAA) — check frame type in service data ---
    eddystone_payload = (service_data or {}).get(EDDYSTONE_UUID)
    if eddystone_payload and len(eddystone_payload) >= 1:
        frame = eddystone_payload[0]
        if frame == EDDYSTONE_EID:
            return "Google FMDN"       # Moto Tag, Moto Tag 2, Pixel Tag, etc.
        if frame == EDDYSTONE_UID:
            return "Eddystone-UID"
        if frame == EDDYSTONE_URL:
            return "Eddystone-URL"
        if frame == EDDYSTONE_TLM:
            return "Eddystone-TLM"
        return "Eddystone"

    # --- Other service UUID based ---
    for uuid in (service_uuids or []):
        label = TRACKER_SVCS.get(uuid.lower())
        if label:
            return label

    # --- Samsung manufacturer data fallback ---
    if manufacturer_data and SAMSUNG_CID in manufacturer_data:
        return "Samsung"

    return None
