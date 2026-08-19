"""Linux secondary-IP ownership with duplicate detection and gratuitous ARP."""

from __future__ import annotations

import ipaddress
import json
import socket
import struct
import subprocess
import time


SIOCGIFADDR = 0x8915
SIOCGIFHWADDR = 0x8927
ETH_P_ARP = 0x0806
ETH_P_ALL = 0x0003


def _interface_request(interface: str) -> bytes:
    return struct.pack("256s", interface.encode("ascii")[:15])


def interface_mac(interface: str) -> bytes:
    import fcntl

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        result = fcntl.ioctl(control.fileno(), SIOCGIFHWADDR, _interface_request(interface))
    return result[18:24]


def interface_ipv4(interface: str) -> bytes:
    import fcntl

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        result = fcntl.ioctl(control.fileno(), SIOCGIFADDR, _interface_request(interface))
    return result[20:24]


def _arp_frame(destination: bytes, sender_mac: bytes, operation: int, sender_ip: bytes,
               target_mac: bytes, target_ip: bytes) -> bytes:
    ethernet = destination + sender_mac + struct.pack("!H", ETH_P_ARP)
    arp = struct.pack(
        "!HHBBH6s4s6s4s",
        1, 0x0800, 6, 4, operation, sender_mac, sender_ip, target_mac, target_ip,
    )
    return ethernet + arp


def find_address_owner(interface: str, address: str, timeout: float = 1.0) -> str | None:
    """Probe an unowned IPv4 address and return a foreign owner's MAC."""
    own_mac = interface_mac(interface)
    target_ip = ipaddress.ip_address(address).packed
    request = _arp_frame(
        b"\xff" * 6, own_mac, 1, b"\x00" * 4, b"\x00" * 6, target_ip,
    )
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL)) as raw:
        raw.bind((interface, 0))
        raw.settimeout(0.1)
        for _attempt in range(3):
            raw.send(request)
            window = min(deadline, time.monotonic() + timeout / 3)
            while time.monotonic() < window:
                try:
                    packet = raw.recv(2048)
                except TimeoutError:
                    continue
                if len(packet) < 42 or packet[12:14] != b"\x08\x06":
                    continue
                sender_mac = packet[22:28]
                sender_ip = packet[28:32]
                if sender_ip == target_ip and sender_mac != own_mac:
                    return ":".join(f"{octet:02x}" for octet in sender_mac)
    return None


def address_in_use(interface: str, address: str, timeout: float = 1.0) -> bool:
    return find_address_owner(interface, address, timeout) is not None


def send_gratuitous_arp(interface: str, address: str, count: int = 5) -> None:
    own_mac = interface_mac(interface)
    own_ip = ipaddress.ip_address(address).packed
    frame = _arp_frame(b"\xff" * 6, own_mac, 2, own_ip, b"\xff" * 6, own_ip)
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL)) as raw:
        raw.bind((interface, 0))
        for _attempt in range(count):
            raw.send(frame)
            time.sleep(0.05)


class VipManager:
    def __init__(self, address: str, interface: str, prefix_length: int):
        self.address = str(ipaddress.ip_address(address))
        self.interface = interface
        self.prefix_length = int(prefix_length)

    def _address_infos(self) -> list[dict[str, object]]:
        result = subprocess.run(
            ["ip", "-j", "address", "show", "dev", self.interface],
            check=True, capture_output=True, text=True, timeout=5,
        )
        rows = json.loads(result.stdout)
        return [
            info for row in rows for info in row.get("addr_info", [])
            if info.get("family") == "inet"
        ]

    def _addresses(self) -> set[str]:
        return {str(info["local"]) for info in self._address_infos()}

    def _is_secondary(self) -> bool:
        global_addresses = [
            info for info in self._address_infos() if info.get("scope") == "global"
        ]
        for index, info in enumerate(global_addresses):
            if info.get("local") == self.address:
                flags = info.get("flags") or []
                return bool(info.get("secondary")) or "secondary" in flags or index > 0
        return False

    def owns(self) -> bool:
        return self.address in self._addresses()

    def acquire(self) -> dict[str, object]:
        if self.owns():
            if not self._is_secondary():
                raise RuntimeError("Refusing to use the interface's primary address as a client VIP")
            send_gratuitous_arp(self.interface, self.address)
            return {"owned": True, "changed": False}
        if address_in_use(self.interface, self.address):
            raise RuntimeError("Client VIP is already owned by another device")
        subprocess.run(
            ["ip", "address", "add", f"{self.address}/{self.prefix_length}", "dev", self.interface],
            check=True, capture_output=True, text=True, timeout=5,
        )
        try:
            send_gratuitous_arp(self.interface, self.address)
            if not self.owns():
                raise RuntimeError("Client VIP was not present after acquisition")
        except Exception:
            subprocess.run(
                ["ip", "address", "del", f"{self.address}/{self.prefix_length}", "dev", self.interface],
                check=False, capture_output=True, text=True, timeout=5,
            )
            raise
        return {"owned": True, "changed": True}

    def release(self) -> dict[str, object]:
        if not self.owns():
            return {"owned": False, "changed": False}
        if not self._is_secondary():
            raise RuntimeError("Refusing to remove the interface's primary address")
        subprocess.run(
            ["ip", "address", "del", f"{self.address}/{self.prefix_length}", "dev", self.interface],
            check=True, capture_output=True, text=True, timeout=5,
        )
        return {"owned": False, "changed": True}
