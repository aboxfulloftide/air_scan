from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Integer, BigInteger, SmallInteger, String, Boolean, DateTime, Text,
    Enum, ForeignKey, JSON, DECIMAL, CHAR
)
from sqlalchemy.orm import Mapped, mapped_column
from api.db import Base


class Device(Base):
    __tablename__ = "devices"

    mac: Mapped[str] = mapped_column(String(17), primary_key=True)
    device_type: Mapped[str] = mapped_column(Enum("AP", "Client"), nullable=False)
    oui: Mapped[Optional[str]] = mapped_column(CHAR(8), nullable=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_randomized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ht_capable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vht_capable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    he_capable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Observation(Base):
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mac: Mapped[str] = mapped_column(String(17), ForeignKey("devices.mac"), nullable=False)
    interface: Mapped[str] = mapped_column(String(20), nullable=False)
    scanner_host: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_dbm: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    channel: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    freq_mhz: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    channel_flags: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SSID(Base):
    __tablename__ = "ssids"

    mac: Mapped[str] = mapped_column(String(17), ForeignKey("devices.mac"), primary_key=True)
    ssid: Mapped[str] = mapped_column(String(255), primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Scanner(Base):
    __tablename__ = "scanners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hostname: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    x_pos: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    y_pos: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    z_pos: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    floor: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MapConfig(Base):
    __tablename__ = "map_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    floor: Mapped[int] = mapped_column(SmallInteger, default=0)
    image_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    width_meters: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 2), nullable=True)
    height_meters: Mapped[Optional[float]] = mapped_column(DECIMAL(10, 2), nullable=True)
    gps_anchor_lat: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    gps_anchor_lon: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    gps_anchor_x: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    gps_anchor_y: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)


class MapZone(Base):
    __tablename__ = "map_zones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    map_id: Mapped[int] = mapped_column(Integer, ForeignKey("map_config.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    polygon_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    zone_type: Mapped[str] = mapped_column(Enum("secure", "common", "outdoor"), default="common")


class DevicePosition(Base):
    __tablename__ = "device_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mac: Mapped[str] = mapped_column(String(17), nullable=False)
    x_pos: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    y_pos: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 8), nullable=True)
    floor: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(DECIMAL(5, 2), nullable=True)
    method: Mapped[Optional[str]] = mapped_column(Enum("trilateration", "single_scanner", "gps", "manual"), nullable=True)
    scanner_count: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class KnownDevice(Base):
    __tablename__ = "known_devices"

    mac: Mapped[str] = mapped_column(String(17), primary_key=True)
    port_scan_host_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    owner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(Enum("known", "unknown", "guest", "rogue"), default="unknown")
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
