"""Network changes for the Network page: the ONLY module that changes network settings.

Every change goes through `nmcli` (or `tailscale`) as the logged-in user. NetworkManager's
polkit rules allow this for the active desktop session, so no root and no privilege
escalation tool is ever involved. Every argv value is validated first, and no password
ever goes into argv (argv is readable in /proc by other users).
"""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import subprocess
from typing import Callable, NamedTuple, Optional, Tuple


class Outcome(NamedTuple):
    ok: bool
    message: str          # one plain sentence for the status bar


DNS_PRESETS = (("Automatic (from your router)", ()),
               ("Cloudflare  1.1.1.1", ("1.1.1.1", "1.0.0.1")),
               ("Quad9  9.9.9.9 - blocks known-bad sites", ("9.9.9.9", "149.112.112.112")),
               ("Google  8.8.8.8", ("8.8.8.8", "8.8.4.4")))

# Activation can wait for DHCP and the key exchange, so it gets a longer cap. nmcli's own
# --wait 30 is shorter than the 40 s Python cap, so nmcli gives up first and says why.
_ACT_TIMEOUT = 40
_QUICK_TIMEOUT = 10
_WAIT = ("--wait", "30")
_MAX_DNS = 3
_PRIO_MIN, _PRIO_MAX = -999, 999
_MAX_MSG = 100

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_IFACE_RE = re.compile(r"[A-Za-z0-9_.\-]{1,15}")
_PSK_RE = re.compile(r"[ -~]{8,63}")          # printable ASCII, WPA passphrase length
_HEX64_RE = re.compile(r"[0-9A-Fa-f]{64}")    # raw WPA key given as 64 hex digits
_PRINTABLE_RE = re.compile(r"[ -~]*")
_SEP_RE = re.compile(r"[,\s]+")
_LAST_PAREN_RE = re.compile(r"\(([^()]*)\)")

_PSK_MARKERS = ("secrets were required", "no secrets", "802-11-wireless-security.psk")
_POLKIT_MARKERS = ("not authorized", "insufficient privileges", "not allowed")
_TIMEOUT_MARKERS = ("timeout", "timed out")

_BAD_ID = "That network profile id is not valid."
_BAD_SSID = "That network name is not valid."
_BAD_IFACE = "That Wi-Fi interface name is not valid."
_ENTERPRISE = "Enterprise (802.1X) networks need your desktop's network settings"


# ---- validators -----------------------------------------------------------------

def valid_uuid(u) -> bool:
    return isinstance(u, str) and _UUID_RE.fullmatch(u) is not None


def valid_ssid(s) -> bool:
    if not isinstance(s, str) or "\0" in s or "\n" in s or "\r" in s:
        return False
    try:
        return 1 <= len(s.encode("utf-8")) <= 32
    except UnicodeEncodeError:       # a lone surrogate cannot become argv bytes
        return False


def valid_iface(n) -> bool:
    return isinstance(n, str) and not n.startswith("-") and _IFACE_RE.fullmatch(n) is not None


def valid_password(p, security) -> Optional[str]:
    """None if the password is acceptable, otherwise a plain reason. Open networks need none."""
    if not security:
        return None
    if not isinstance(p, str):
        return "Password must be text."
    if _PSK_RE.fullmatch(p) or _HEX64_RE.fullmatch(p):
        return None
    if not _PRINTABLE_RE.fullmatch(p):
        return "Password has characters that Wi-Fi does not allow."
    if len(p) < 8:
        return "Password must be at least 8 characters."
    return "Password must be 63 characters or fewer (or exactly 64 hex digits)."


def parse_dns_list(text) -> Optional[Tuple[str, ...]]:
    """'1.1.1.1, 9.9.9.9' -> normalised tuple. Any bad address or more than 3 -> None."""
    if not isinstance(text, str):
        return None
    tokens = [t for t in _SEP_RE.split(text.strip()) if t]
    if len(tokens) > _MAX_DNS:
        return None
    out = []
    for tok in tokens:
        try:
            out.append(str(ipaddress.ip_address(tok)))
        except ValueError:
            return None
    return tuple(out)


# ---- runner and message mapping ---------------------------------------------------

def _run(argv, timeout, run, text_input=None, tool="nmcli"):
    """Run one command. Returns (proc, None), or (None, Outcome) when it could not run at all."""
    runner = run or subprocess.run
    kwargs = {"capture_output": True, "text": True, "timeout": timeout,
              "env": dict(os.environ, LC_ALL="C")}
    # subprocess rejects stdin and input together, so only one of them is passed.
    if text_input is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = text_input
    try:
        return runner(argv, **kwargs), None
    except subprocess.TimeoutExpired:
        return None, Outcome(False, "Timed out - the network did not answer.")
    except FileNotFoundError:
        if tool == "tailscale":
            return None, Outcome(False, "Tailscale is not installed")
        return None, Outcome(False, "nmcli is not installed (package: network-manager)")
    except OSError:
        return None, Outcome(False, "Failed: could not start " + tool + ".")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _map_failure(proc, name: str) -> str:
    stderr = proc.stderr or ""
    stdout = proc.stdout or ""
    low = (stderr + "\n" + stdout).lower()
    who = " for " + name if name else ""
    if any(m in low for m in _PSK_MARKERS) or ("property is invalid" in low and "psk" in low):
        return "Wrong or missing password" + who + "."
    if any(m in low for m in _POLKIT_MARKERS):
        return "NetworkManager refused: this account may not change network settings (polkit)"
    if "no network with ssid" in low:
        return "That network is out of range right now."
    if any(m in low for m in _TIMEOUT_MARKERS):
        return "Timed out - the network did not answer."
    line = _first_line(stderr) or _first_line(stdout)
    if line.startswith("Error: "):
        line = line[len("Error: "):].strip()
    if not line:
        line = "nmcli exited with an error (code %s)" % proc.returncode
    return "Failed: " + line[:_MAX_MSG]


def _act(argv, timeout, run, ok_msg, name="", text_input=None, tool="nmcli") -> Outcome:
    proc, err = _run(argv, timeout, run, text_input, tool)
    if err is not None:
        return err
    if proc.returncode == 0:
        return Outcome(True, ok_msg)
    return Outcome(False, _map_failure(proc, name))


def _label(name: str) -> str:
    return name or "this network"


# ---- saved profiles ---------------------------------------------------------------

def connect_saved(uuid, name="", run=None) -> Outcome:
    if not valid_uuid(uuid):
        return Outcome(False, _BAD_ID)
    argv = ["nmcli", *_WAIT, "connection", "up", "uuid", uuid]
    ok = "Connected to " + name + "." if name else "Connected."
    return _act(argv, _ACT_TIMEOUT, run, ok, name)


def disconnect(uuid, name="", run=None) -> Outcome:
    if not valid_uuid(uuid):
        return Outcome(False, _BAD_ID)
    argv = ["nmcli", "connection", "down", "uuid", uuid]
    ok = "Disconnected from " + name + "." if name else "Disconnected."
    return _act(argv, _QUICK_TIMEOUT, run, ok, name)


def forget(uuid, name="", run=None) -> Outcome:
    if not valid_uuid(uuid):
        return Outcome(False, _BAD_ID)
    argv = ["nmcli", "connection", "delete", "uuid", uuid]
    ok = "Forgot " + name + "." if name else "Forgot the saved network."
    return _act(argv, _QUICK_TIMEOUT, run, ok, name)


def set_autoconnect(uuid, on, name="", run=None) -> Outcome:
    if not valid_uuid(uuid):
        return Outcome(False, _BAD_ID)
    value = "yes" if on else "no"
    argv = ["nmcli", "connection", "modify", "uuid", uuid, "connection.autoconnect", value]
    ok = "Auto-connect for %s is %s." % (_label(name), "on" if on else "off")
    return _act(argv, _QUICK_TIMEOUT, run, ok, name)


def set_priority(uuid, value, name="", run=None) -> Outcome:
    if not valid_uuid(uuid):
        return Outcome(False, _BAD_ID)
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):    # OverflowError: float("inf")
        return Outcome(False, "Priority must be a whole number.")
    n = max(_PRIO_MIN, min(_PRIO_MAX, n))  # NetworkManager accepts -999..999 only
    argv = ["nmcli", "connection", "modify", "uuid", uuid, "connection.autoconnect-priority", str(n)]
    ok = "Priority for %s set to %d." % (_label(name), n)
    return _act(argv, _QUICK_TIMEOUT, run, ok, name)


def get_dns(uuid, run=None) -> Optional[Tuple[Tuple[str, ...], bool]]:
    """(servers, ignore_auto) for a saved profile, or None when it cannot be read."""
    if not valid_uuid(uuid):
        return None
    argv = ["nmcli", "-g", "ipv4.dns,ipv4.ignore-auto-dns", "connection", "show", "uuid", uuid]
    proc, err = _run(argv, _QUICK_TIMEOUT, run)
    if err is not None or proc.returncode != 0:
        return None
    lines = (proc.stdout or "").splitlines()
    dns_line = lines[0] if lines else ""
    ignore = len(lines) > 1 and lines[1].strip().lower() == "yes"
    servers = []
    for part in dns_line.split(","):
        part = part.strip()
        try:
            servers.append(str(ipaddress.ip_address(part)))
        except ValueError:
            continue  # skip anything that is not an address rather than show junk
    return tuple(servers), ignore


def set_dns(uuid, servers, name="", run=None) -> Outcome:
    if not valid_uuid(uuid):
        return Outcome(False, _BAD_ID)
    try:
        items = list(servers)
    except TypeError:
        return Outcome(False, "DNS servers must be a list of addresses.")
    if len(items) > _MAX_DNS:
        return Outcome(False, "Use at most 3 DNS servers.")
    parsed = []
    for item in items:
        try:
            # ip_address() also accepts integers, which are not addresses here.
            if not isinstance(item, str):
                raise ValueError(item)
            parsed.append(ipaddress.ip_address(item))
        except ValueError:
            return Outcome(False, "That is not a valid DNS address.")
    v4 = [str(ip) for ip in parsed if ip.version == 4]
    v6 = [str(ip) for ip in parsed if ip.version == 6]

    base = ["nmcli", "connection", "modify", "uuid", uuid]
    if not parsed:
        # Empty list = automatic: clear both families and stop ignoring the router's DNS.
        props = ["ipv4.dns", "", "ipv4.ignore-auto-dns", "no",
                 "ipv6.dns", "", "ipv6.ignore-auto-dns", "no"]
        ok = "DNS for %s: automatic." % _label(name)
    else:
        # A family that was not given is left as it is, so a v4-only choice never touches IPv6.
        props = []
        if v4:
            props += ["ipv4.dns", ",".join(v4), "ipv4.ignore-auto-dns", "yes"]
        if v6:
            props += ["ipv6.dns", ",".join(v6), "ipv6.ignore-auto-dns", "yes"]
        ok = "DNS for %s: %s." % (_label(name), ", ".join(str(ip) for ip in parsed))

    out = _act(base + props, _QUICK_TIMEOUT, run, "", name)
    if not out.ok:
        return out
    # Changes to a profile only take effect on the next activation.
    return _act(["nmcli", *_WAIT, "connection", "up", "uuid", uuid], _ACT_TIMEOUT, run, ok, name)


# ---- new networks -----------------------------------------------------------------

def _key_mgmt(security: str) -> str:
    words = {w.upper() for w in security.split()}
    if "WPA3" in words and not (words & {"WPA", "WPA2"}):
        return "sae"
    return "wpa-psk"


def _new_uuid(stdout: str) -> Optional[str]:
    # nmcli prints: Connection 'X' (uuid) successfully added. The uuid is the last (...) group.
    found = _LAST_PAREN_RE.findall(stdout or "")
    if not found:
        return None
    candidate = found[-1].strip()
    return candidate if valid_uuid(candidate) else None


def connect_new(ssid, password, security, iface, run=None) -> Outcome:
    if not valid_ssid(ssid):
        return Outcome(False, _BAD_SSID)
    if not valid_iface(iface):
        return Outcome(False, _BAD_IFACE)
    if security is None:
        security = ""
    if not isinstance(security, str):
        return Outcome(False, "Unknown network security type.")
    if "802.1X" in security:
        return Outcome(False, _ENTERPRISE)

    if not security or security.split() == ["OWE"]:
        # Open network (OWE is "enhanced open", also no password): the password is never read or sent.
        if ssid.startswith("-"):
            # The SSID is a positional argument here, so nmcli could read it as an option.
            return Outcome(False, "Open networks whose name starts with '-' need your desktop's network settings")
        argv = ["nmcli", *_WAIT, "device", "wifi", "connect", ssid, "ifname", iface]
        return _act(argv, _ACT_TIMEOUT, run, "Connected to " + ssid + ".", ssid)

    reason = valid_password(password, security)
    if reason is not None:
        return Outcome(False, reason)

    # Step 1: profile WITHOUT the secret, so the password never appears in argv.
    add_argv = ["nmcli", "connection", "add", "type", "wifi",
                "con-name", ssid, "ifname", iface, "ssid", ssid,
                "wifi-sec.key-mgmt", _key_mgmt(security)]
    proc, err = _run(add_argv, _QUICK_TIMEOUT, run)
    if err is not None:
        return err
    if proc.returncode != 0:
        return Outcome(False, _map_failure(proc, ssid))
    uuid = _new_uuid(proc.stdout)
    if uuid is None:
        return Outcome(False, "Could not read the new profile id from NetworkManager.")

    # Step 2: hand the secret over on stdin. NetworkManager stores it in its own store.
    up_argv = ["nmcli", *_WAIT, "connection", "up", "uuid", uuid, "passwd-file", "/dev/stdin"]
    secret = "802-11-wireless-security.psk:" + password + "\n"
    up = _act(up_argv, _ACT_TIMEOUT, run, "Connected to " + ssid + ".", ssid, text_input=secret)
    if not up.ok:
        # Roll back so a wrong password does not leave a broken profile behind.
        _run(["nmcli", "connection", "delete", "uuid", uuid], _QUICK_TIMEOUT, run)
    return up


# ---- radios, scans, recovery ------------------------------------------------------

def rescan(iface=None, run=None) -> Outcome:
    argv = ["nmcli", "device", "wifi", "rescan"]
    if iface is not None:
        if not valid_iface(iface):
            return Outcome(False, _BAD_IFACE)
        argv += ["ifname", iface]
    return _act(argv, _QUICK_TIMEOUT, run, "Scanning for Wi-Fi networks.")


def wifi_radio(on, run=None) -> Outcome:
    argv = ["nmcli", "radio", "wifi", "on" if on else "off"]
    return _act(argv, _QUICK_TIMEOUT, run, "Wi-Fi is %s." % ("on" if on else "off"))


def airplane(on, run=None) -> Outcome:
    # "radio all" keeps Ethernet working; "networking off" would cut every interface.
    argv = ["nmcli", "radio", "all", "off" if on else "on"]
    return _act(argv, _QUICK_TIMEOUT, run, "Airplane mode is %s." % ("on" if on else "off"))


def reconnect(uuid, name="", run=None) -> Outcome:
    if not valid_uuid(uuid):
        return Outcome(False, _BAD_ID)
    # Down first, then up. A failed down is ignored: the profile may already be down.
    _run(["nmcli", "connection", "down", "uuid", uuid], _QUICK_TIMEOUT, run)
    ok = "Reconnected to " + name + "." if name else "Reconnected."
    return _act(["nmcli", *_WAIT, "connection", "up", "uuid", uuid], _ACT_TIMEOUT, run, ok, name)


def tailscale(up, run=None, which: Callable = shutil.which) -> Outcome:
    if not which("tailscale"):
        return Outcome(False, "Tailscale is not installed")
    argv = ["tailscale", "up" if up else "down"]
    ok = "Tailscale is %s." % ("up" if up else "down")
    return _act(argv, _QUICK_TIMEOUT, run, ok, "Tailscale", tool="tailscale")
