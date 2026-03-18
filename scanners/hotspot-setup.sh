#!/usr/bin/env bash
# hotspot-setup.sh — One-time setup: turn wlan0 (onboard Pi WiFi) into a hotspot
#
# After running this:
#   - Pi broadcasts a WiFi network named "airscan" (or whatever you set below)
#   - Phone joins that network, gets an IP in 192.168.4.x
#   - Open http://192.168.4.1:8080 in phone browser for the status page
#
# IMPORTANT: wlan1 (USB WiFi) is left completely untouched — it stays in
# monitor mode for scanning as normal.
#
# Tested on: Raspberry Pi OS (Bookworm / Bullseye), Pi 3B+ / 4 / 5
#
# Run once as root:
#   sudo bash hotspot-setup.sh
# ---------------------------------------------------------------------------

set -e

AP_IFACE="wlan0"
AP_SSID="airscan"
AP_PASS="airscan123"   # min 8 chars; change this
AP_IP="192.168.4.1"
DHCP_RANGE="192.168.4.10,192.168.4.50,255.255.255.0,24h"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash hotspot-setup.sh"
  exit 1
fi

echo "==> Installing hostapd and dnsmasq..."
apt-get update -qq
apt-get install -y hostapd dnsmasq

echo "==> Stopping services while we configure..."
systemctl stop hostapd dnsmasq 2>/dev/null || true
systemctl unmask hostapd

# ---------------------------------------------------------------------------
# Static IP for wlan0
# ---------------------------------------------------------------------------
echo "==> Setting static IP ${AP_IP} on ${AP_IFACE}..."

DHCPCD=/etc/dhcpcd.conf
if ! grep -q "interface ${AP_IFACE}" "$DHCPCD" 2>/dev/null; then
  cat >> "$DHCPCD" <<EOF

# --- hotspot-setup.sh ---
interface ${AP_IFACE}
    static ip_address=${AP_IP}/24
    nohook wpa_supplicant
EOF
fi

# ---------------------------------------------------------------------------
# hostapd — AP configuration
# ---------------------------------------------------------------------------
echo "==> Writing /etc/hostapd/hostapd.conf..."
cat > /etc/hostapd/hostapd.conf <<EOF
interface=${AP_IFACE}
driver=nl80211
ssid=${AP_SSID}
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=1
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=${AP_PASS}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=CCMP
rsn_pairwise=CCMP
EOF

# Point the daemon to the config file
sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' \
    /etc/default/hostapd 2>/dev/null || true
echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >> /etc/default/hostapd

# ---------------------------------------------------------------------------
# dnsmasq — DHCP for clients on wlan0
# ---------------------------------------------------------------------------
echo "==> Writing /etc/dnsmasq.conf..."
# Back up original if this is the first run
[ -f /etc/dnsmasq.conf ] && [ ! -f /etc/dnsmasq.conf.orig ] && \
    cp /etc/dnsmasq.conf /etc/dnsmasq.conf.orig

cat > /etc/dnsmasq.conf <<EOF
# hotspot-setup.sh — DHCP only on the AP interface
interface=${AP_IFACE}
dhcp-range=${DHCP_RANGE}
domain=local
address=/airscan.local/${AP_IP}
EOF

# ---------------------------------------------------------------------------
# Enable and start
# ---------------------------------------------------------------------------
echo "==> Enabling services..."
systemctl enable hostapd dnsmasq
systemctl restart dhcpcd
sleep 2
systemctl start hostapd
systemctl start dnsmasq

# ---------------------------------------------------------------------------
# Install the status service while we're here
# ---------------------------------------------------------------------------
STATUS_SVC=/etc/systemd/system/mobile-status.service
if [ ! -f "$STATUS_SVC" ]; then
  echo "==> Installing mobile-status.service..."
  cp "$(dirname "$0")/mobile-status.service" "$STATUS_SVC"
  systemctl daemon-reload
  systemctl enable mobile-status
  systemctl start mobile-status
fi

echo ""
echo "============================================================"
echo " Hotspot ready."
echo ""
echo "  SSID     : ${AP_SSID}"
echo "  Password : ${AP_PASS}"
echo "  Status   : http://${AP_IP}:8080"
echo ""
echo " Join '${AP_SSID}' on your phone, then open the URL above."
echo "============================================================"
