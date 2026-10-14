# Milestone 2: ROS2 Ingestion, CoT Translation & TAK Server Egress

**Status:** In Progress
**Objective:** Add ROS2/DDS subscriber capability, transform autonomous telemetry into valid MIL-STD Cursor on Target (CoT) XML with appropriate air/ground symbology, and establish network transmission to TAK Server.

## Key Deliverables
- [x] `CoTTransformer` engine mapping geodetic positions and domain types:
  - [x] UAV / Airborne Autonomy: Mapped to MIL-STD-2525 CoT type `a-f-A-M-F` (Friendly Airborne Drone / Military).
  - [x] UGV / Ground Autonomy: Mapped to MIL-STD-2525 CoT type `a-f-G-E-V-U` (Friendly Ground Unmanned Vehicle).
  - [x] Dynamic calculation of speed, course/heading, altitude (HAE), circular error (CE), and linear error (LE).
  - [x] Detail elements: `<contact callsign="..." />`, `<status battery="..." />`, and `<track course="..." speed="..." />`.
- [x] Network egress transports:
  - [x] UDP unicast / multicast / broadcast publisher (`UdpEgressClient` - default TAK port 4242 / 6969).
  - [x] TCP streaming client with TLS support, queue backpressure, and exponential reconnect (`TcpEgressClient` - port 8087 / 8089).
- [x] Schema validation & integration suites:
  - [x] CoT schema validation, mandatory attributes, ISO-8601 millisecond UTC timestamps, and round-trip conversion.
  - [x] Live network loopback integration tests (UDP datagram transmission, persistent TCP streaming, and end-to-end MAVLink-to-CoT pipeline).
- [ ] ROS2 / DDS Ingest Adapter (`ROS2Adapter`) subscribing to `nav_msgs/Odometry` and `sensor_msgs/NavSatFix`.

## Acceptance Criteria
1. Generated CoT XML is consumed without errors by FreeTAKServer or standard TAK Server.
2. Airborne drone tracks and ground rover tracks appear with correct distinct MIL-STD icons on ATAK-CIV.
3. Stale timeout calculations correctly trigger indicator dimming when updates cease.
