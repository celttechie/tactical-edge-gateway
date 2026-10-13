# 3. Cursor on Target (CoT) XML Translation & Schema Validation

Date: 2026-10-13

## Status
Accepted

## Context
Command and Control (C2) situational awareness tools across the Department of Defense (TAK Server, ATAK, WinTAK, WebTAK) consume geospatial situational awareness through **Cursor on Target (CoT)**, an XML schema standardized by MITRE. 

Robotics telemetry contains coordinate frames, raw sensor readings, and vehicle statuses that must be transformed into valid CoT `<event>` messages conforming to MIL-STD-2525 symbology standards (e.g., `a-f-G-E-V-U` for friendly ground unmanned vehicles).

## Decision
1. Implement a dedicated transformation engine (`CoTTransformer`) that maps normalized internal `TelemetryFrame` structures to standard CoT XML.
2. Structure output `<event>` tags with:
   - Unique UIDs tied deterministically to platform identifiers.
   - ISO-8601 UTC timestamps with accurate stale time calculations (preventing phantom tracks on loss of signal).
   - Point coordinates: Latitude, Longitude, Height Above Ellipsoid (HAE), Circular Error (CE), and Linear Error (LE).
   - Detail elements: `<contact callsign="..." />`, `<status battery="..." />`, and `<track course="..." speed="..." />`.
3. Enforce strict validation before queueing packets for network transmission.

## Consequences
- High-fidelity interoperability with any standard TAK deployment (FreeTAKServer or DoD TAK Server).
- Built-in protection against stale or malformed XML packets propagating across tactical networks.
