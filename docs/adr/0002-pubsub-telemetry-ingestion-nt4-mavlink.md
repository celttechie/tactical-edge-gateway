# 2. Multi-Protocol Autonomy Ingestion (MAVLink and ROS2/DDS)

Date: 2026-10-13 (Updated 2026-10-14)

## Status
Accepted (Amended to retire NetworkTables 4 in favor of MAVLink + ROS2/DDS)

## Context
Tactical unmanned platforms and autonomous systems operate across operational telemetry standards:
1. **MAVLink (Micro Air Vehicle Link):** The dominant binary protocol for uncrewed aerial systems (UAS), autopilots (PX4, ArduPilot), and ground control stations (QGroundControl). Typically transported over UDP (port 14550) or serial UART.
2. **ROS2 / DDS (Robot Operating System 2 / Data Distribution Service):** The open robotics middleware standard utilizing DDS publish-subscribe topics (`sensor_msgs/NavSatFix`, `nav_msgs/Odometry`) for ground vehicles, manipulators, and sensor suites.
3. **NetworkTables 4 (NT4):** Originally evaluated as a pub/sub protocol candidate. However, NT4 is specific to FIRST Robotics (RobotPy / WPILib), requires heavy native C++ wheels (`pyntcore`), and is not utilized in defense or operational tactical autonomy programs.

## Decision
We will implement a **First-Class Multi-Protocol Ingest Architecture** (`BaseTelemetryAdapter`):
1. **MAVLink Ingest Adapter (`MavlinkAdapter`):** Uses `pymavlink` to ingest asynchronous UDP/serial streams, parsing standard messages:
   - `HEARTBEAT` (system status, autopilot type, armed state)
   - `GLOBAL_POSITION_INT` (GPS latitude, longitude, altitude, velocity vectors)
   - `ATTITUDE` (roll, pitch, yaw)
   - `SYS_STATUS` (battery percentage, hardware sensor health)
2. **ROS2 / DDS Ingest Adapter (`ROS2Adapter`):** Provides a standardized subscriber for `nav_msgs/Odometry` and `sensor_msgs/NavSatFix` for tactical ground unmanned systems.
3. **Synthetic Telemetry Generator (`SyntheticAdapter`):** Deterministic flight/ground waypoint simulator for headless unit testing and continuous integration without physical radio/robot hardware.
4. **Retirement of NetworkTables 4:** NT4 is retired from the gateway scope to eliminate non-standard dependencies, minimize container attack surface, and maintain strict alignment with defense and DoD autonomy standards.

All adapters normalize incoming packets into a unified internal dataclass: `TelemetryFrame`.

## Consequences
- Immediate, out-of-the-box compatibility across air (UAV) and ground (UGV) autonomous systems.
- Demonstrates deep hands-on fluency in the core tactical autonomy protocols highlighted in Defense Unicorns' operational domain.
- Clean separation between edge wire protocols and downstream C2 translation.
- Lightweight container footprint and simplified air-gap delivery via Zarf.
