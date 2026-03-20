-- Migration 011: Clean garbage SSIDs from the database
-- Removes SSIDs that are non-printable, too long, or contain binary garbage.
-- This fixes the root cause: scanners were extracting arbitrary Dot11Elt data
-- instead of only element ID 0 (the actual SSID field).

USE wireless;

-- Delete SSIDs longer than 32 chars (802.11 max SSID length)
DELETE FROM ssids WHERE CHAR_LENGTH(ssid) > 32;

-- Delete SSIDs with non-printable characters (control chars, high bytes)
DELETE FROM ssids WHERE ssid NOT REGEXP '^[[:print:]]+$';

-- Delete empty SSIDs
DELETE FROM ssids WHERE ssid = '' OR ssid IS NULL;
