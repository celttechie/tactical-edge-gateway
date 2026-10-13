# 2. Multi-Protocol Autonomy Ingestion (MAVLink, ROS2/DDS, and NetworkTables)

Date: 2026-10-13

## Status
Accepted (Supersedes single-protocol draft)

## Context
Tactical unmanned platforms and autonomous systems operate across three primary telemetry standards:
1. **MAVLink (Micro Air Vehicle Link):** The dominant binary protocol for uncrewed aerial systems (UAS), autopilots (PX4, ArduPilot), and ground control stations (QGroundControl). Typically transported over UDP (port 14550) or serial UART.
2. **ROS2 / DDS (Robot Operating System 2 / Data Distribution Service):** The open robotics middleware standard utilizing DDS publish-subscribe topics (`sensor_msgs/NavSatFix`, `nav_msgs/Odometry`) for ground vehicles, manipulators, and sensor suites.
3. **NetworkTables 4 (NT4):** Real-time pub/sub telemetry used in FIRST Robotics (RobotPy / WPILib) for high-frequency coprocessor vision and controller pose estimation over constrained Wi-Fi links.

Restricting the gateway to a single protocol or deferring MAVLink and ROS2 as future add-ons creates domain silos and limits immediate tactical utility.

## Decision
We will implement a **First-Class Multi-Protocol Ingest Architecture** (`BaseTelemetryAdapter`):
1. **MAVLink Ingest Adapter (`MavlinkAdapter`):** Uses `pymavlink` to ingest asynchronous UDP/serial streams, parsing standard messages:
   - `HEARTBEAT` (system status, autopilot type, armed state)
   - `GLOBAL_POSITION_INT` (GPS latitude, longitude, altitude, velocity vectors)
   - `ATTITUDE` (roll, pitch, yaw)
   - `SYS_STATUS` (battery percentage, hardware sensor health)
2. **ROS2 / DDS Ingest Adapter (`ROS2Adapter`):** Provides a standardized subscriber for `nav_msgs/Odometry` and `sensor_msgs/NavSatFix`, packageable as a lightweight container node.
3. **NetworkTables 4 Adapter (`NT4Adapter`):** Ingests robotics coprocessor poses and telemetry from RobotPy / WPILib (`/robot/pose`, `/robot/status`).
4. **Synthetic Telemetry Generator (`SyntheticAdapter`):** Deterministic flight/ground waypoint simulator for headless unit testing and continuous integration without physical radio/robot hardware.

All adapters normalize incoming packets into a unified internal dataclass: `TelemetryFrame`.

## Consequences
- Immediate, out-of-the-box compatibility across air (UAV) and ground (UGV) autonomous systems.
- Demonstrates deep hands-on fluency in the core tactical autonomy protocols highlighted in Defense Unicorns' operational domain.
- Clean separation between edge wire protocols and downstream C2 translation.
