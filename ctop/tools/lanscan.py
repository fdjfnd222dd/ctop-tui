"""Device finder for the Network page: which devices are on my home network, found by one opt-in sweep.

The Security page only lists devices this computer already talked to. This tool is the one-key
version: it sends one tiny UDP packet to every address of the local subnet. Sending to an address
makes the kernel ask "who has this address?" (ARP), so every device that is switched on answers with
its MAC address, and the kernel stores that in its neighbour table. We then read that table.
No root, no raw sockets, no port scanning. Only private home-sized networks are accepted.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from typing import Optional, Tuple

import psutil

from ctop.collectors.lan import is_random_mac, read_devices
from ctop.models import FoundDevice, LanScanResult
from ctop.system import default_route
from ctop.tools import cancelled, emit

MAX_HOSTS = 254
SEND_GAP = 0.004                # seconds between packets: 254 packets take ~1 s, gentle on cheap routers
SETTLE = 2.0                    # seconds to let ARP replies arrive after the last packet
DISCARD_PORT = 9                # the "discard" service; nothing is expected to answer, the ARP lookup is the point
PROGRESS_EVERY = 16             # emit a progress update every this many addresses

# Home networks, plus the documentation ranges so tests and the demo can use them.
_ALLOWED = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


def scan_subnet(ip: str, netmask: str) -> Tuple[Optional[ipaddress.IPv4Network], Optional[str]]:
    """The subnet to sweep, or (None, reason). Networks bigger than a /24 are narrowed to the /24 around ip."""
    try:
        addr = ipaddress.IPv4Address(ip)
    except (ValueError, TypeError):
        return None, "Not an IPv4 address"
    try:
        net = ipaddress.IPv4Network("%s/%s" % (ip, netmask), strict=False)
    except (ValueError, TypeError):
        return None, "Could not read the netmask"
    if not any(addr in allowed for allowed in _ALLOWED):
        return None, "Only private home networks can be scanned"
    if net.prefixlen < 24:
        # A big office-style network would be thousands of packets; a home sweep stops at 254 hosts.
        net = ipaddress.IPv4Network("%s/24" % ip, strict=False)
    return net, None


def _read_own_mac(iface: str) -> Optional[str]:
    try:
        with open("/sys/class/net/%s/address" % iface) as f:
            return f.read().strip().upper() or None
    except (OSError, ValueError):
        return None


def _default_neigh(gateway: Optional[str]):
    return read_devices(gateway_ip=gateway)


def _ipv4_of(addrs: dict, iface: str):
    """(address, netmask) of the iface's IPv4 address, or (None, None)."""
    for entry in addrs.get(iface, []):
        if entry.family == socket.AF_INET:
            return entry.address, entry.netmask
    return None, None


def run_lanscan(on_update=None, cancel=None, route=None, addrs=None, send=None, neigh=None,
                sleep=None, own_mac=None) -> LanScanResult:
    """Sweep the local subnet and list the devices that answer. Never raises; problems go in `error`."""
    route = route or default_route
    addrs = addrs or psutil.net_if_addrs
    neigh = neigh or _default_neigh
    own_mac = own_mac or _read_own_mac
    if sleep is None:
        if cancel is not None:
            sleep = cancel.wait                          # wakes early when the user cancels
        else:
            sleep = time.sleep

    try:
        iface, gateway = route()
        own_ip, netmask = _ipv4_of(addrs() or {}, iface) if iface else (None, None)
    except Exception:                                    # a broken /proc or psutil must not crash the page
        result = LanScanResult(done=True, error="Could not read this computer's network settings")
        emit(on_update, result)
        return result
    if not iface:
        result = LanScanResult(done=True, error="Not connected to a network")
        emit(on_update, result)
        return result

    if own_ip is None:
        result = LanScanResult(done=True, error="No IPv4 address on %s" % iface)
        emit(on_update, result)
        return result

    net, reason = scan_subnet(own_ip, netmask or "")
    if net is None:
        result = LanScanResult(done=True, error=reason)
        emit(on_update, result)
        return result

    hosts = list(net.hosts())
    total = len(hosts)
    emit(on_update, LanScanResult(subnet=str(net), total=total, probed=0))

    sock = None
    if send is None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
        except OSError:
            result = LanScanResult(subnet=str(net), total=total, done=True,
                                   error="Could not open a network socket")
            emit(on_update, result)
            return result

        def send(ip: str) -> None:
            try:
                sock.sendto(b"", (ip, DISCARD_PORT))     # an empty packet is enough to trigger ARP
            except OSError:
                pass                                     # one unreachable address must not stop the sweep

    error = None
    probed = 0
    try:
        for host in hosts:
            if cancelled(cancel):
                error = "cancelled"
                break
            if str(host) != own_ip:                      # our own address needs no packet
                send(str(host))
                sleep(SEND_GAP)
            probed += 1
            if probed % PROGRESS_EVERY == 0:
                emit(on_update, LanScanResult(subnet=str(net), total=total, probed=probed))
    finally:
        if sock is not None:
            sock.close()

    if error is None:
        sleep(SETTLE)                                    # ARP replies arrive a little after the last packet
        if cancelled(cancel):
            error = "cancelled"

    # Whatever the table says now is the answer, even when the sweep was cut short.
    in_subnet = []
    try:
        for dev in neigh(gateway) or ():
            try:
                if ipaddress.IPv4Address(dev.ip) in net and dev.ip != own_ip:
                    in_subnet.append(FoundDevice(ip=dev.ip, mac=dev.mac, random_mac=dev.random_mac,
                                                 is_gateway=dev.is_gateway, is_me=False))
            except ValueError:
                continue
    except Exception:
        error = error or "Could not read the device table"

    own = own_mac(iface)
    in_subnet.append(FoundDevice(ip=own_ip, mac=own, random_mac=bool(own) and is_random_mac(own),
                                 is_gateway=False, is_me=True))
    in_subnet.sort(key=lambda d: (not d.is_gateway, not d.is_me, int(ipaddress.IPv4Address(d.ip))))

    result = LanScanResult(subnet=str(net), devices=tuple(in_subnet), probed=probed,
                           total=total, done=True, error=error)
    emit(on_update, result)
    return result
