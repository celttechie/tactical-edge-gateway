"""Unit tests for the interactive ATAK mission package builder and distribution CLI."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import common

# Ensure scripts directory is on sys.path
SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

spec = importlib.util.spec_from_file_location(
    "build_atak_package", SCRIPTS_DIR / "build-atak-package.py"
)
assert spec is not None and spec.loader is not None
pkg_builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pkg_builder)


class TestNetworkDetection:
    """Tests for host IP and network interface discovery."""

    def test_detect_host_ip_success(self) -> None:
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.getsockname.return_value = ("192.168.1.42", 54321)
            mock_sock_cls.return_value = mock_sock

            ip = pkg_builder.detect_host_ip()
            assert ip == "192.168.1.42"
            mock_sock.connect.assert_called_once_with(("8.8.8.8", 80))
            mock_sock.close.assert_called_once()

    def test_detect_host_ip_exception_fallback(self) -> None:
        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect.side_effect = OSError("Network unreachable")
            mock_sock_cls.return_value = mock_sock

            ip = pkg_builder.detect_host_ip()
            assert ip == "127.0.0.1"

    def test_discover_network_interfaces_with_ip_tool(self) -> None:
        mock_output = (
            "lo               UNKNOWN        127.0.0.1/8\n"
            "wlp9s0           UP             192.168.9.146/24\n"
            "enp8s0           DOWN           192.168.9.100/24\n"
            "tailscale0       UNKNOWN        100.107.173.30/32\n"
        )
        with patch("shutil.which", return_value="/bin/ip"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=["ip", "-br", "-4", "addr", "show"],
                    returncode=0,
                    stdout=mock_output,
                )
                ifaces = pkg_builder.discover_network_interfaces()

                assert ("lo", "127.0.0.1") in ifaces
                assert ("wlp9s0", "192.168.9.146") in ifaces
                assert ("tailscale0", "100.107.173.30") in ifaces
                # DOWN interface must be excluded
                assert not any(name == "enp8s0" for name, _ in ifaces)

    def test_discover_network_interfaces_fallback_when_ip_fails(self) -> None:
        with patch("shutil.which", return_value="/bin/ip"):
            with patch("subprocess.run", side_effect=subprocess.SubprocessError("Failed")):
                with patch.object(common, "detect_host_ip", return_value="10.0.0.5"):
                    ifaces = pkg_builder.discover_network_interfaces()
                    assert ("default", "10.0.0.5") in ifaces
                    assert ("lo", "127.0.0.1") in ifaces

    def test_discover_network_interfaces_fallback_when_no_ip_binary(self) -> None:
        with patch("shutil.which", return_value=None):
            with patch.object(common, "detect_host_ip", return_value="127.0.0.1"):
                ifaces = pkg_builder.discover_network_interfaces()
                assert ifaces == [("default", "127.0.0.1")]

    @pytest.mark.parametrize(
        "iface,expected",
        [
            ("wlp9s0", " (Wi-Fi)"),
            ("wlan0", " (Wi-Fi)"),
            ("wifi0", " (Wi-Fi)"),
            ("enp8s0", " (Ethernet)"),
            ("eth0", " (Ethernet)"),
            ("tailscale0", " (Tailscale VPN)"),
            ("lo", " (Loopback)"),
            ("docker0", " (Bridge/Container)"),
            ("br-1234", " (Bridge/Container)"),
            ("veth4321", " (Bridge/Container)"),
            ("unknown_dev", ""),
        ],
    )
    def test_format_interface_label(self, iface: str, expected: str) -> None:
        assert pkg_builder.format_interface_label(iface) == expected


class TestInteractiveSelection:
    """Tests for the interactive network interface selection prompt."""

    def test_select_default_on_empty_input(self) -> None:
        interfaces = [("wlp9s0", "192.168.9.146"), ("lo", "127.0.0.1")]
        prompt_fn = MagicMock(return_value="")
        chosen = pkg_builder.interactive_select_host(interfaces, prompt_fn=prompt_fn)
        assert chosen == "192.168.9.146"

    def test_select_valid_index(self) -> None:
        interfaces = [("wlp9s0", "192.168.9.146"), ("lo", "127.0.0.1")]
        prompt_fn = MagicMock(return_value="2")
        chosen = pkg_builder.interactive_select_host(interfaces, prompt_fn=prompt_fn)
        assert chosen == "127.0.0.1"

    def test_select_custom_ip_input(self) -> None:
        interfaces = [("wlp9s0", "192.168.9.146"), ("lo", "127.0.0.1")]
        # Choice 3 is custom, second prompt enters custom IP
        prompt_fn = MagicMock(side_effect=["3", "10.10.10.20"])
        chosen = pkg_builder.interactive_select_host(interfaces, prompt_fn=prompt_fn)
        assert chosen == "10.10.10.20"

    def test_select_reprompts_on_invalid_and_empty_custom(self) -> None:
        interfaces = [("wlp9s0", "192.168.9.146")]
        # Choice 99 (invalid) -> Choice 2 (custom, empty) -> Choice 2 ("custom.lan")
        prompt_fn = MagicMock(side_effect=["99", "abc", "2", "", "2", "custom.lan"])
        chosen = pkg_builder.interactive_select_host(interfaces, prompt_fn=prompt_fn)
        assert chosen == "custom.lan"


class TestPackageGeneration:
    """Tests for ATAK XML documents and zip package generation."""

    def test_generate_pref_xml_structure(self) -> None:
        xml_str = pkg_builder.generate_pref_xml(
            host="192.168.1.50",
            port=8087,
            description="Test Edge Stream",
        )
        root = ET.fromstring(xml_str)
        assert root.tag == "preferences"

        entries = {entry.attrib["key"]: entry.text for entry in root.findall(".//entry")}
        assert entries["connectString0"] == "192.168.1.50:8087:tcp"
        assert entries["description0"] == "Test Edge Stream"
        assert entries["enabled0"] == "true"
        assert entries["displayServerConnectionWidget"] == "true"

    def test_generate_manifest_xml_structure(self) -> None:
        xml_str = pkg_builder.generate_manifest_xml(pkg_name="Custom Package")
        root = ET.fromstring(xml_str)
        assert root.tag == "MissionPackageManifest"

        params = {
            param.attrib["name"]: param.attrib["value"] for param in root.findall(".//Parameter")
        }
        assert params["name"] == "Custom Package"
        assert params["uid"] == "teg-freetakserver-connection-pkg"

        content = root.find(".//Content")
        assert content is not None
        assert content.attrib["zipEntry"] == "teg-freetakserver.pref"

    def test_build_package_in_custom_directory(self, tmp_path: Path) -> None:
        pref_file, zip_file = pkg_builder.build_package(
            host="10.20.30.40",
            port=8087,
            output_dir=tmp_path,
        )

        assert pref_file.exists()
        assert zip_file.exists()
        assert pref_file.name == "teg-freetakserver.pref"
        assert zip_file.name == "teg-mission-package.zip"

        pref_content = pref_file.read_text(encoding="utf-8")
        assert "10.20.30.40:8087:tcp" in pref_content

        with zipfile.ZipFile(zip_file, "r") as zf:
            namelist = zf.namelist()
            assert "MANIFEST/manifest.xml" in namelist
            assert "teg-freetakserver.pref" in namelist

            pref_in_zip = zf.read("teg-freetakserver.pref").decode("utf-8")
            assert pref_in_zip == pref_content

            manifest_in_zip = zf.read("MANIFEST/manifest.xml").decode("utf-8")
            assert "teg-freetakserver.pref" in manifest_in_zip

    def test_build_package_default_directory(self) -> None:
        # Test default output directory calculation
        with (
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
            patch("zipfile.ZipFile"),
        ):
            pref_file, zip_file = pkg_builder.build_package(host="127.0.0.1", port=8087)
            assert "packages/atak/teg-freetakserver.pref" in str(pref_file).replace("\\", "/")
            assert "packages/atak/mission-package/teg-mission-package.zip" in str(zip_file).replace(
                "\\", "/"
            )


class TestServerAndCli:
    """Tests for HTTP server distribution and CLI execution."""

    def test_serve_package_invocation(self, tmp_path: Path) -> None:
        zip_file = tmp_path / "test-pkg.zip"
        zip_file.touch()

        mock_server = MagicMock()
        mock_server_cls = MagicMock()
        mock_server_cls.return_value.__enter__.return_value = mock_server

        pkg_builder.serve_package(
            zip_file=zip_file,
            host="192.168.1.10",
            http_port=8000,
            server_cls=mock_server_cls,
        )

        mock_server_cls.assert_called_once()
        mock_server.serve_forever.assert_called_once()

    def test_parse_args_defaults(self) -> None:
        with patch.object(pkg_builder, "detect_host_ip", return_value="192.168.1.100"):
            args = pkg_builder.parse_args([])
            assert args.host is None
            assert args.interactive is False
            assert args.port == 8087
            assert args.serve is False
            assert args.http_port == 8000
            assert args.output_dir is None

    def test_main_flow_with_host_flag(self, tmp_path: Path) -> None:
        with patch.object(pkg_builder, "build_package") as mock_build:
            mock_build.return_value = (tmp_path / "pref", tmp_path / "zip")
            pkg_builder.main(
                ["--host", "10.0.0.99", "--port", "8089", "--output-dir", str(tmp_path)]
            )
            mock_build.assert_called_once_with("10.0.0.99", 8089, tmp_path)

    def test_main_flow_with_interactive_flag(self, tmp_path: Path) -> None:
        with patch.object(
            pkg_builder, "discover_network_interfaces", return_value=[("wlan", "10.0.0.1")]
        ):
            with patch.object(
                pkg_builder, "interactive_select_host", return_value="10.0.0.1"
            ) as mock_select:
                with patch.object(pkg_builder, "build_package") as mock_build:
                    mock_build.return_value = (tmp_path / "pref", tmp_path / "zip")
                    pkg_builder.main(["-i"])
                    mock_select.assert_called_once()
                    mock_build.assert_called_once_with("10.0.0.1", 8087, None)

    def test_main_flow_with_serve_flag_and_keyboard_interrupt(self, tmp_path: Path) -> None:
        zip_file = tmp_path / "teg-mission-package.zip"
        zip_file.touch()

        with patch.object(pkg_builder, "detect_host_ip", return_value="192.168.1.20"):
            with patch.object(
                pkg_builder, "build_package", return_value=(tmp_path / "pref", zip_file)
            ):
                with patch.object(pkg_builder, "serve_package", side_effect=KeyboardInterrupt):
                    with pytest.raises(SystemExit) as exc_info:
                        pkg_builder.main(["--serve"])
                    assert exc_info.value.code == 0


class TestCommonUtilities:
    """Tests for scripts.common shared utilities and MockTakServer."""

    def test_get_repo_root(self) -> None:
        root = common.get_repo_root()
        assert (root / "Makefile").exists()

    def test_find_free_port(self) -> None:
        tcp_port = common.find_free_port(is_udp=False)
        assert isinstance(tcp_port, int)
        assert tcp_port > 0

        udp_port = common.find_free_port(is_udp=True)
        assert isinstance(udp_port, int)
        assert udp_port > 0

    @pytest.mark.asyncio
    async def test_mock_tak_server_lifecycle(self) -> None:
        port = common.find_free_port()
        server = common.MockTakServer("127.0.0.1", port)
        await server.start()

        # Connect client and send test message
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"<event uid='test-uid'/>\n")
        await writer.drain()
        await asyncio.sleep(0.05)
        writer.close()
        await writer.wait_closed()

        assert len(server.received_messages) == 1
        assert b"<event uid='test-uid'/>" in server.received_messages[0]

        # Sever link
        await server.sever_link()
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)

        # Restore link
        await server.restore_link()
        _reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        writer2.write(b"<event uid='test-uid-2'/>\n")
        await writer2.drain()
        await asyncio.sleep(0.05)
        writer2.close()
        await writer2.wait_closed()

        assert len(server.received_messages) == 2

        # Clean shutdown
        await server.stop()
