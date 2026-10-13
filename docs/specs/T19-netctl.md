# T19 - netctl: the ONLY module that changes network settings

## 1 Purpose
`ctop/netctl.py` performs every network change ctop can make, through `nmcli` as the logged-in user. NetworkManager's polkit
rules allow this for the active desktop session, so no root and no privilege-escalation tool is ever involved. Like
`ctop/safety.py` for stopping programs, it is small, strict and validates everything before it reaches argv. The UI calls these
functions from a worker thread AFTER the user confirmed.

## 2 Files
Create `ctop/netctl.py`, `tests/test_netctl.py`. Read `docs/specs/NET-COMMON.md`.

## 3 Public API
```python
class Outcome(NamedTuple):
    ok: bool
    message: str          # one plain sentence for the status bar: "Connected to ExampleNet." / "Wrong or missing password for ExampleNet."

DNS_PRESETS = (("Automatic (from your router)", ()),
               ("Cloudflare  1.1.1.1", ("1.1.1.1", "1.0.0.1")),
               ("Quad9  9.9.9.9 - blocks known-bad sites", ("9.9.9.9", "149.112.112.112")),
               ("Google  8.8.8.8", ("8.8.8.8", "8.8.4.4")))

def valid_uuid(u) -> bool        # ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$
def valid_ssid(s) -> bool        # str, 1..32 UTF-8 bytes, no NUL, no newline
def valid_iface(n) -> bool       # ^[A-Za-z0-9_.-]{1,15}$ and not starting with "-"
def valid_password(p, security) -> Optional[str]   # None if OK, else a plain reason. Personal WPA/WPA2/WPA3: 8..63 printable ASCII (0x20-0x7e) or exactly 64 hex digits. Open network (security ""): no password needed, any value ignored -> None.
def parse_dns_list(text) -> Optional[tuple]  # "1.1.1.1, 9.9.9.9" or "2001:db8::1 1.1.1.1" -> tuple of normalised IP strings, max 3; any invalid -> None; blank -> ()

def connect_saved(uuid, name="", run=None) -> Outcome      # nmcli --wait 30 connection up uuid <uuid>
def disconnect(uuid, name="", run=None) -> Outcome         # nmcli connection down uuid <uuid>
def forget(uuid, name="", run=None) -> Outcome             # nmcli connection delete uuid <uuid>
def set_autoconnect(uuid, on, name="", run=None) -> Outcome  # nmcli connection modify uuid <uuid> connection.autoconnect yes|no
def set_priority(uuid, value, name="", run=None) -> Outcome  # clamp -999..999; connection.autoconnect-priority <n>
def get_dns(uuid, run=None) -> Optional[tuple]             # (servers tuple, ignore_auto bool); None on failure
def set_dns(uuid, servers, name="", run=None) -> Outcome
def connect_new(ssid, password, security, iface, run=None) -> Outcome
def rescan(iface=None, run=None) -> Outcome                # nmcli device wifi rescan [ifname <iface>]
def wifi_radio(on, run=None) -> Outcome                    # nmcli radio wifi on|off
def airplane(on, run=None) -> Outcome                      # on: nmcli radio all off ; off: nmcli radio all on
def reconnect(uuid, name="", run=None) -> Outcome          # "fix my Wi-Fi": connection down, then connection up, same uuid
def tailscale(up, run=None, which=shutil.which) -> Outcome # tailscale up | tailscale down; which("tailscale") None -> ok False "Tailscale is not installed"
```
`run=None` -> `subprocess.run`. Every call: `run(argv, capture_output=True, text=True, timeout=T, env=dict(os.environ, LC_ALL="C"))`
plus EITHER `stdin=subprocess.DEVNULL` OR `input=...` (never both - subprocess forbids it).
Timeouts: activation (`connection up`, `device wifi connect`) 40 s with `["nmcli", "--wait", "30", ...]` so nmcli gives up
first; everything else 10 s with plain `["nmcli", ...]`. Invalid input -> `Outcome(False, "...")` WITHOUT running anything.

## 4 Behaviour details
* **Result -> message.** exit 0 -> ok with a short success sentence naming the profile/SSID when known.
  Non-zero -> map stderr+stdout (case-insensitive):
  `secrets were required` / `no secrets` / `802-11-wireless-security.psk` / `property is invalid` with psk -> "Wrong or missing password";
  `not authorized` / `insufficient privileges` / `not allowed` -> "NetworkManager refused: this account may not change network settings (polkit)";
  `no network with ssid` -> "That network is out of range right now";
  `timeout` / `timed out`, or `subprocess.TimeoutExpired` raised -> "Timed out - the network did not answer";
  `FileNotFoundError` raised -> "nmcli is not installed (package: network-manager)";
  anything else -> "Failed: " + first non-empty stderr line with a leading `Error: ` removed, cut to 100 chars.
  Name the network in the message when a name/ssid is known: "Wrong or missing password for ExampleNet."
* **Never put a password in argv** (argv is readable in /proc by other users). `connect_new`:
  1. Open network (`security == ""`): `nmcli --wait 30 device wifi connect <ssid> ifname <iface>`.
  2. Secured personal network: create a profile WITHOUT the secret:
     `nmcli connection add type wifi con-name <ssid> ifname <iface> ssid <ssid> wifi-sec.key-mgmt <km>`
     where km = `sae` if the security words contain WPA3 and none of WPA/WPA2, else `wpa-psk`. Parse the new uuid from stdout
     (`Connection 'X' (3f0c1e2a-1111-2222-3333-444455556666) successfully added.` - take the uuid inside the LAST parentheses,
     validate it). Then `nmcli --wait 30 connection up uuid <uuid> passwd-file /dev/stdin` with
     `input="802-11-wireless-security.psk:" + password + "\n"`. NetworkManager stores the secret it receives.
     If activation fails -> `nmcli connection delete uuid <uuid>` (rollback, so a wrong password leaves no broken profile) and
     return the mapped failure. Password check (`valid_password`) happens before anything runs.
  3. security contains `802.1X` (enterprise) -> `Outcome(False, "Enterprise (802.1X) networks need your desktop's network settings")`, nothing run.
  ssid/con-name are separate argv elements, so spaces/colons are safe; still require `valid_ssid` and `valid_iface`.
* **set_dns**: servers `()` = automatic: `nmcli connection modify uuid U ipv4.dns "" ipv4.ignore-auto-dns no ipv6.dns "" ipv6.ignore-auto-dns no`.
  Otherwise split by family: `ipv4.dns <v4 comma-joined> ipv4.ignore-auto-dns yes`, and the same two ipv6 properties ONLY if
  v6 servers were given (no v6 given -> ipv6 untouched). Then apply: `nmcli --wait 30 connection up uuid U` (40 s).
  Validate every server with `ipaddress.ip_address`; 1..3 servers. Message: "DNS for <name> is now Cloudflare-style list ..." keep simple:
  "DNS for <name>: 1.1.1.1, 1.0.0.1." / "DNS for <name>: automatic."
* **get_dns**: `nmcli -g ipv4.dns,ipv4.ignore-auto-dns connection show uuid U` prints one value per line:
  `1.1.1.1,1.0.0.1\nyes\n` -> `(("1.1.1.1", "1.0.0.1"), True)`; `\nno\n` -> `((), False)`.
* `reconnect`: down then up; if down fails still try up; message "Reconnected to <name>." or the mapped up failure.

## 5 Safety
Only the argv shapes above may ever be built (test it). Never: a shell, any privilege-escalation tool, `nmcli general hostname`,
`nmcli networking off` (that cuts every interface, cable included). `airplane(True)` uses `radio all off`, which keeps Ethernet up.
Every argv value is a validated uuid/ssid/iface/ip or a fixed literal.

## 6 Tests (`tests/test_netctl.py`) - fake `run` recording argv/kwargs, returning SimpleNamespace(returncode, stdout, stderr)
* validators: good/bad uuids (`"-f"`, `"x;rm"`, 35 chars), ssid of 33 bytes rejected, `"Cafe: Free"` accepted, iface `-x` rejected.
* valid_password: 7 chars rejected, 8 accepted, 64 hex accepted, 64 non-hex rejected, non-ASCII rejected, open network OK.
* parse_dns_list: commas, spaces, ipv6, 4 entries -> None, "nope" -> None, "" -> ().
* connect_saved argv exact `["nmcli","--wait","30","connection","up","uuid",U]`, timeout 40.
* connect_new open: argv has `device wifi connect`, ssid as its own element, nothing password-like.
* connect_new secured success: two runs; first argv contains `wifi-sec.key-mgmt`, `wpa-psk`; second has `passwd-file`, `/dev/stdin`
  and kwarg `input == "802-11-wireless-security.psk:hunter22pw\n"`; the password appears in NO argv element; no `stdin` kwarg with input.
* WPA3-only -> `sae`; "WPA2 WPA3" -> `wpa-psk`.
* connect_new wrong password: up fails with stderr `Error: Connection activation failed: Secrets were required, but not provided` ->
  a third call deletes the new uuid; message contains "password".
* enterprise -> nothing run. Invalid uuid -> nothing run, ok False.
* error mapping: not authorized; FileNotFoundError raised by run; TimeoutExpired raised by run.
* set_dns auto argv; v4 only (no `ipv6.dns` in argv); mixed v4+v6; then a `connection up` call.
* get_dns parse both shapes.
* wifi_radio/airplane argv; no argv ever contains `networking`.
* reconnect: down fails, up still called.
* allow-list: call every public function with valid args; every argv[0] is `nmcli` or `tailscale`.
