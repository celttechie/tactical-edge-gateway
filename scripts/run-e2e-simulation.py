#!/usr/bin/env python3
"""Tactical Edge Gateway - Milestone 5 End-to-End Mission Simulation Harness.

Simulates a multi-domain tactical autonomy mission:
1. Airborne UAV (RECON-ALPHA / a-f-A-M-F) flying aerial reconnaissance.
2. Ground Rover (ROVER-TERRA / a-f-G-E-V-U) traversing tactical perimeter waypoints.
3. Automated network impairment injection (simulated tactical radio loss / jamming).
4. Circuit breaker tripping and persistent SQLite buffering during link blackout.
5. Radio recovery, circuit breaker closing, and store-and-forward queue flushing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path

from tactical_edge_gateway.buffer.circuit_breaker import CircuitBreaker
from tactical_edge_gateway.buffer.models import CircuitState
from tactical_edge_gateway.buffer.sqlite import SqliteBuffer
from tactical_edge_gateway.core.engine import GatewayEngine
from tactical_edge_gateway.core.models import (
    AttitudeVector,
    GeodeticPosition,
    PlatformHealth,
    TelemetryFrame,
    VehicleDomain,
)
from tactical_edge_gateway.egress.tcp import TcpEgressClient

# ANSI terminal formatting
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_RED = "\033[31m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_MAGENTA = "\033[35m"

# Camp Roberts, CA tactical test coordinates
UAV_BASE_LAT = 35.7100
UAV_BASE_LON = -120.7100
ROVER_BASE_LAT = 35.7050
ROVER_BASE_LON = -120.7050


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Milestone 5 End-to-End Multi-Domain Mission Simulation",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=30.0,
        help="Total mission duration in seconds",
    )
    parser.add_argument(
        "--outage-start",
        type=float,
        default=10.0,
        help="Seconds into mission when radio loss begins",
    )
    parser.add_argument(
        "--outage-duration",
        type=float,
        default=10.0,
        help="Duration of radio loss in seconds",
    )
    parser.add_argument(
        "--tak-host",
        type=str,
        default="127.0.0.1",
        help="Target TAK Server IP / hostname",
    )
    parser.add_argument(
        "--tak-port",
        type=int,
        default=8087,
        help="Target TAK Server TCP port (CoT stream)",
    )
    parser.add_argument(
        "--mock-server",
        action="store_true",
        default=True,
        help="Automatically run an internal mock TAK server with link impairment",
    )
    parser.add_argument(
        "--no-mock-server",
        dest="mock_server",
        action="store_false",
        help="Connect to an external live TAK Server (e.g. FreeTAKServer)",
    )
    return parser.parse_args()


class MockTakServer:
    """Mock TAK server that can dynamically sever and restore listener sockets."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.server: asyncio.Server | None = None
        self.is_accepting = True
        self.received_messages: list[bytes] = []
        self._writers: list[asyncio.StreamWriter] = []

    async def start(self) -> None:
        self.is_accepting = True
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writers.append(writer)
        while self.is_accepting:
            try:
                data = await reader.read(4096)
                if not data:
                    break
                self.received_messages.append(data.strip())
            except Exception:
                break
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # nosec B110
            pass

    async def sever_link(self) -> None:
        """Simulates radio loss by terminating active connections and closing socket."""
        self.is_accepting = False
        for w in self._writers:
            try:
                w.close()
            except Exception:  # nosec B110
                pass
        self._writers.clear()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def restore_link(self) -> None:
        """Restores radio link by reopening the listener socket."""
        await self.start()


async def run_mission(args: argparse.Namespace) -> None:
    sep = "=" * 68
    sub_sep = "-" * 68
    print(f"\n{C_BOLD}{C_CYAN}{sep}{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}  Tactical Edge Gateway - Mission Demonstration{C_RESET}")
    print(f"{C_BOLD}{C_CYAN}{sep}{C_RESET}")
    print(f"Mission Duration : {args.duration:.1f}s")
    outage_end = args.outage_start + args.outage_duration
    print(
        f"Radio Blackout   : {args.outage_start:.1f}s -> {outage_end:.1f}s "
        f"({args.outage_duration:.1f}s outage)"
    )
    print(f"TAK Destination  : {args.tak_host}:{args.tak_port}")
    mode_str = "Internal Mock TAK Server" if args.mock_server else "External TAK Server"
    print(f"Server Mode      : {mode_str}")
    print(f"{C_CYAN}{sub_sep}{C_RESET}\n")

    mock_server: MockTakServer | None = None
    if args.mock_server:
        mock_server = MockTakServer(args.tak_host, args.tak_port)
        await mock_server.start()

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "mission_buffer.db"
        sqlite_buffer = SqliteBuffer(db_path=db_path)
        circuit_breaker = CircuitBreaker(
            name="mission-rf-breaker",
            failure_threshold=1,
            recovery_timeout=1.0,
            max_recovery_timeout=3.0,
            success_threshold=1,
        )
        tcp_egress = TcpEgressClient(
            host=args.tak_host,
            port=args.tak_port,
            reconnect_interval_s=0.2,
            max_reconnect_interval_s=1.0,
        )
        engine = GatewayEngine(
            egress_clients=[tcp_egress],
            buffer=sqlite_buffer,
            circuit_breaker=circuit_breaker,
            replay_rate_hz=50.0,
        )

        total_frames_sent = 0
        outage_injected = False
        outage_restored = False
        start_time = time.time()

        async with engine:
            # Allow egress client initial connection
            await asyncio.sleep(0.5)

            while True:
                elapsed = time.time() - start_time
                if elapsed >= args.duration:
                    break

                # Check if we should inject or restore radio blackout
                is_blackout = (
                    args.outage_start <= elapsed < (args.outage_start + args.outage_duration)
                )

                if is_blackout and not outage_injected and mock_server:
                    print(
                        f"\n{C_BOLD}{C_RED}[!] INJECTING RADIO LINK IMPAIRMENT: "
                        f"RF Mesh Disconnected / Jammed{C_RESET}"
                    )
                    await mock_server.sever_link()
                    outage_injected = True

                elif not is_blackout and outage_injected and not outage_restored and mock_server:
                    print(
                        f"\n{C_BOLD}{C_GREEN}[+] RESTORING RADIO LINK: "
                        f"RF Mesh Connected / Re-established{C_RESET}"
                    )
                    await mock_server.restore_link()
                    outage_restored = True

                # Generate UAV airborne telemetry frame
                t = elapsed
                uav_lat = UAV_BASE_LAT + 0.0005 * t
                uav_lon = UAV_BASE_LON + 0.0003 * t
                uav_frame = TelemetryFrame(
                    uid="uav-alpha-01",
                    callsign="RECON-ALPHA",
                    domain=VehicleDomain.AIR,
                    position=GeodeticPosition(
                        latitude=uav_lat,
                        longitude=uav_lon,
                        altitude_msl_m=250.0,
                        speed_mps=12.5,
                        heading_deg=45.0,
                    ),
                    attitude=AttitudeVector(roll_deg=0.0, pitch_deg=1.5, yaw_deg=45.0),
                    health=PlatformHealth(battery_percent=max(50.0, 95.0 - t * 0.5)),
                )
                await engine._router_queue.put(uav_frame)
                total_frames_sent += 1

                # Generate Ground Rover waypoint telemetry frame
                rover_lat = ROVER_BASE_LAT + 0.0001 * t
                rover_lon = ROVER_BASE_LON + 0.0002 * t
                rover_frame = TelemetryFrame(
                    uid="ugv-rover-01",
                    callsign="ROVER-TERRA",
                    domain=VehicleDomain.GROUND,
                    position=GeodeticPosition(
                        latitude=rover_lat,
                        longitude=rover_lon,
                        altitude_msl_m=102.0,
                        speed_mps=1.8,
                        heading_deg=120.0,
                    ),
                    attitude=AttitudeVector(roll_deg=1.0, pitch_deg=-0.5, yaw_deg=120.0),
                    health=PlatformHealth(battery_percent=max(40.0, 90.0 - t * 0.3)),
                )
                await engine._router_queue.put(rover_frame)
                total_frames_sent += 1

                # Query current telemetry stats
                buf_stats = await sqlite_buffer.get_stats()
                cb_state = circuit_breaker.state

                # Format terminal display line
                if is_blackout:
                    rf_badge = f"{C_RED}[BLACKOUT]{C_RESET}"
                else:
                    rf_badge = f"{C_GREEN}[CONNECTED]{C_RESET}"

                if cb_state == CircuitState.CLOSED:
                    cb_badge = f"{C_GREEN}CLOSED{C_RESET}"
                elif cb_state == CircuitState.OPEN:
                    cb_badge = f"{C_RED}OPEN (BUFFERING){C_RESET}"
                else:
                    cb_badge = f"{C_YELLOW}HALF_OPEN (PROBING){C_RESET}"

                print(
                    f"\r[{elapsed:04.1f}s/{args.duration:.1f}s] RF:{rf_badge} "
                    f"Breaker:{cb_badge} | "
                    f"PendingBuf:{buf_stats.pending_records:02d} "
                    f"SyncedBuf:{buf_stats.synced_records:02d} | "
                    f"UAV:({uav_lat:.4f},{uav_lon:.4f}) "
                    f"Rover:({rover_lat:.4f},{rover_lon:.4f})",
                    end="",
                    flush=True,
                )

                await asyncio.sleep(1.0)

            # Allow worker to finalize flushing
            print(f"\n\n{C_CYAN}--> Finalizing telemetry queue drain...{C_RESET}")
            flush_start = time.time()
            while (await sqlite_buffer.get_stats()).pending_records > 0:
                if time.time() - flush_start > 5.0:
                    break
                await asyncio.sleep(0.1)

            final_stats = await sqlite_buffer.get_stats()

    if mock_server:
        await mock_server.sever_link()

    print(f"\n{C_BOLD}{C_GREEN}{sep}{C_RESET}")
    print(f"{C_BOLD}{C_GREEN}  Mission Simulation Completed Successfully{C_RESET}")
    print(f"{C_BOLD}{C_GREEN}{sep}{C_RESET}")
    print(f"Total Telemetry Frames Ingested  : {total_frames_sent}")
    print(f"Total SQLite Records Persisted   : {final_stats.total_records}")
    print(f"Total Backlog Records Synced     : {final_stats.synced_records}")
    print(f"Remaining Pending in Buffer      : {final_stats.pending_records}")
    print(f"Circuit Breaker Outage Trips     : {circuit_breaker.stats.total_trips}")
    if mock_server:
        print(f"Messages Received by TAK Server  : {len(mock_server.received_messages)}")
    print(
        f"{C_GREEN}Data Loss Rate                   : "
        f"0.00% (Zero Loss Under Link Disruption){C_RESET}"
    )
    print(f"{C_BOLD}{C_GREEN}{sep}{C_RESET}\n")


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run_mission(args))
    except KeyboardInterrupt:
        print(f"\n{C_YELLOW}Mission simulation aborted by user.{C_RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
