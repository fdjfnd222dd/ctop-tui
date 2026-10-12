"""Firewall status without root: which tool is in charge, whether it is on, and its rules when readable.

Read-only. Nothing here changes the firewall or needs sudo. "No firewall tool found" is
reported as active=None, never active=False: an nftables or iptables setup can exist
unseen, and only an explicit "off" in ufw's own settings counts as off.
"""
from __future__ import annotations

import os
import subprocess
from typing import Callable, Dict, List, Optional, Tuple

from ctop.collectors.base import Collector
from ctop.models import FirewallInfo

Runner = Callable[[List[str], float], Optional[str]]

CMD_TIMEOUT = 2.0
MAX_RULES = 100
ANY_NET = ("0.0.0.0/0", "::/0")
TUPLE_MARK = "### tuple ###"
POLICY_NAMES = {"DROP": "deny", "REJECT": "reject", "ACCEPT": "allow"}
ZONE_TARGETS = {"default": "reject", "REJECT": "reject", "DROP": "deny", "ACCEPT": "allow"}

UFW_CONF = "etc/ufw/ufw.conf"
UFW_DEFAULT = "etc/default/ufw"
UFW_RULES = "etc/ufw/user.rules"

# Notes are shown to the owner verbatim; they are the decision table in T10-firewall-status.md.
NOTE_1 = "Rule list needs root - run `sudo ufw status` in another terminal"
NOTE_2 = "ufw is switched on in its settings, but its service is not running - run `sudo ufw status` to check."
NOTE_3 = "firewalld is running. Run `firewall-cmd --list-all` in another terminal to see its rules."
NOTE_4 = "ufw is installed but switched off. Turn it on with `sudo ufw enable`."
NOTE_5 = "ufw is installed but its settings need root to read - run `sudo ufw status`."
NOTE_6 = ("nftables is loaded but its rules need root to read. Debian's default setup lets everything "
          "through, so this does not prove you are protected - run `sudo nft list ruleset` to check.")
NOTE_7 = ("No firewall tool (ufw or firewalld) was found. That is normal on a fresh Debian install. "
          "A firewall written directly with nftables or iptables could still exist - it cannot be seen "
          "without root. Run `sudo nft list ruleset` to check.")


def run_cmd(argv: List[str], timeout: float) -> Optional[str]:
    # LC_ALL=C keeps tool messages in English, the words the parsers look for.
    env = dict(os.environ, LC_ALL="C")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    # A non-zero exit still prints useful text (e.g. "running" or a stale state), so keep stdout.
    return proc.stdout


def _read(path: str) -> Optional[str]:
    # A directory where a file should be, or a root-only file, both raise OSError: "unreadable".
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _setting(text: str, key: str) -> str:
    # ufw.conf and /etc/default/ufw are shell-style KEY=value files; the last assignment wins.
    value = ""
    for line in text.splitlines():
        body = line.strip()
        if body.startswith("#") or "=" not in body:
            continue
        name, _, raw = body.partition("=")
        if name.strip() == key:
            value = raw.strip().strip("\"'")
    return value


def parse_ufw_conf(text: str) -> Optional[bool]:
    return {"yes": True, "no": False}.get(_setting(text, "ENABLED").lower())


def parse_default_input_policy(text: str) -> Optional[str]:
    return POLICY_NAMES.get(_setting(text, "DEFAULT_INPUT_POLICY"))


def parse_ufw_user_rules(text: str) -> Tuple[str, ...]:
    rules: List[str] = []
    for line in text.splitlines():
        body = line.strip()
        if not body.startswith(TUPLE_MARK):
            continue
        # Fields: action proto dport dst sport src [direction] [comment=<hex>]. The comment is
        # user text in hex; it is never decoded or shown.
        fields = body[len(TUPLE_MARK):].split()
        if len(fields) < 6:
            continue
        action, proto, dport, dst, _sport, src = fields[:6]
        direction = fields[6] if len(fields) > 6 else ""
        rule = f"{action} {proto} {dport}"
        if src not in ANY_NET:
            rule += " from " + src
        if dst not in ANY_NET:
            rule += " to " + dst
        if direction.startswith("in_"):
            rule += " on " + direction[len("in_"):]
        elif direction == "out" or direction.startswith("out_"):
            rule += " (outgoing)"
        rules.append(rule)
        if len(rules) == MAX_RULES:
            break
    return tuple(rules)


def parse_firewalld_list_all(text: str) -> Tuple[Optional[str], Tuple[str, ...]]:
    default: Optional[str] = None
    rules: List[str] = []
    for line in text.splitlines():
        body = line.strip()
        if body.startswith("target:"):
            words = body[len("target:"):].split()
            default = ZONE_TARGETS.get(words[0]) if words else None
        elif body.startswith("services:"):
            rules += ["service " + word for word in body[len("services:"):].split()]
        elif body.startswith("ports:"):
            rules += ["port " + word for word in body[len("ports:"):].split()]
    return default, tuple(rules)


def parse_is_active(text: Optional[str], units: Tuple[str, ...]) -> Dict[str, str]:
    # `systemctl is-active` prints one line per unit, in the order asked. A missing line
    # (short output, no systemctl at all) means we simply do not know that unit.
    lines = (text or "").splitlines()
    states: Dict[str, str] = {}
    for index, unit in enumerate(units):
        word = lines[index].strip() if index < len(lines) else ""
        states[unit] = word if word in ("active", "inactive", "failed") else "unknown"
    return states


def _unit_states(run: Runner) -> Dict[str, str]:
    states = parse_is_active(
        run(["systemctl", "is-active", "ufw", "firewalld", "nftables"], CMD_TIMEOUT),
        ("ufw", "firewalld", "nftables"))
    if all(state == "unknown" for state in states.values()):
        # No systemd here (Devuan, Alpine, containers): firewalld still answers "running" on its own.
        state_out = run(["firewall-cmd", "--state"], CMD_TIMEOUT) or ""
        if "running" in state_out.splitlines():
            states["firewalld"] = "active"
    return states


def read_firewall(root: str = "/", runner: Optional[Runner] = None) -> FirewallInfo:
    run = runner or run_cmd
    states = _unit_states(run)
    conf_path = os.path.join(root, UFW_CONF)
    ufw_on = parse_ufw_conf(_read(conf_path) or "")
    # Only "inactive" and "failed" mean the service is down; "unknown" (no systemd) still counts as up.
    ufw_down = states["ufw"] in ("inactive", "failed")

    if ufw_on is True and not ufw_down:  # row 1: ufw is on and running
        policy = parse_default_input_policy(_read(os.path.join(root, UFW_DEFAULT)) or "")
        rules_text = _read(os.path.join(root, UFW_RULES))
        if rules_text is None:
            return FirewallInfo("ufw", True, policy, False, (), NOTE_1)
        return FirewallInfo("ufw", True, policy, True, parse_ufw_user_rules(rules_text), "")

    if ufw_on is True:  # row 2: switched on in its settings, but the service is down
        return FirewallInfo("ufw", None, None, False, (), NOTE_2)

    if states["firewalld"] == "active":  # row 3: only asked when firewalld is the one reporting
        listed = run(["firewall-cmd", "--list-all"], CMD_TIMEOUT)
        if listed:
            default, rules = parse_firewalld_list_all(listed)
            return FirewallInfo("firewalld", True, default, True, rules, "")
        return FirewallInfo("firewalld", True, None, False, (), NOTE_3)

    if ufw_on is False:  # row 4: the only place active=False is reported
        return FirewallInfo("ufw", False, None, False, (), NOTE_4)

    if os.path.exists(conf_path):  # row 5: settings file exists but cannot be read or has no ENABLED line
        return FirewallInfo("ufw", None, None, False, (), NOTE_5)

    if states["nftables"] == "active":  # row 6: nftables loaded; its rules are root-only
        return FirewallInfo("nftables", None, None, False, (), NOTE_6)

    return FirewallInfo(None, None, None, False, (), NOTE_7)  # row 7: nothing found is not "no firewall"


class FirewallCollector(Collector):
    """Publishes FirewallInfo. Reads are root-free and cheap, so a slow interval is plenty."""

    name = "firewall"
    interval = 30.0

    def refresh(self) -> None:
        self.data = read_firewall()
