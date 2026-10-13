# Milestone 2: ROS2 Ingestion, CoT Translation & TAK Server Egress

**Status:** Planned  
**Objective:** Add ROS2/DDS subscriber capability, transform autonomous telemetry into valid MIL-STD Cursor on Target (CoT) XML with appropriate air/ground symbology, and establish network transmission to TAK Server.

## Key Deliverables
- [ ] ROS2 / DDS Ingest Adapter (`ROS2Adapter`) subscribing to `nav_msgs/Odometry` and `sensor_msgs/NavSatFix`.
- [ ] `CoTTransformer` engine mapping geodetic positions and domain types:
  - UAV / Airborne Autonomy: Mapped to MIL-STD-2525 CoT type `a-f-A-M-F` (Friendly Airborne Drone / Military).
  - UGV / Ground Autonomy: Mapped to MIL-STD-2525 CoT type `a-f-G-E-V-U` (Friendly Ground Unmanned Vehicle).
  - Dynamic calculation of speed, course/heading, altitude (HAE), circular error (CE), and linear error (LE).
  - Detail elements: `<contact callsign="..." />`, `<status battery="..." />`, and `<track course="..." speed="..." />`.
- [ ] Network egress transports:
  - UDP unicast / multicast publisher (default TAK port 4242 / 6969).
  - TCP client with TLS mutual authentication support (port 8089).
- [ ] Schema validation suite verifying XML against official MITRE CoT XSD definitions.

## Acceptance Criteria
1. Generated CoT XML is consumed without errors by FreeTAKServer or standard TAK Server.
2. Airborne drone tracks and ground rover tracks appear with correct distinct MIL-STD icons on ATAK-CIV.
3. Stale timeout calculations correctly trigger indicator dimming when updates cease.
