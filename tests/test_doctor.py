"""Network doctor: each step, the verdict and the cancel path. Every probe is injected: no network, no commands."""
from __future__ import annotations

import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ctop.models import NmState, SavedConnection, WifiData  # noqa: E402
from ctop.tools import doctor as dr  # noqa: E402

GOOD_NET = {"iface": "wlan0", "ip": "192.0.2.23", "gateway": "192.0.2.1"}
GOOD_WIFI = WifiData(iface="wlan0", ssid="ExampleNet", signal_pct=70, signal_dbm=-58, bitrate_mbps=144.0,
                     freq_mhz=5180, channel=36, security="WPA2", quality="Good")
NM_ON = NmState(available=True, wifi_enabled=True, state="connected", connectivity="full")


def wifi(quality="Good", dbm=-58):
    return WifiData(iface="wlan0", ssid="ExampleNet", signal_pct=40, signal_dbm=dbm, bitrate_mbps=54.0,
                    freq_mhz=2412, channel=1, security="WPA2", quality=quality)


def probes(router=3.0, internet=21.0, dns=18.0, portal="full", link=True, router_list=None):
    """Healthy network by default. router_list overrides the three router pings."""
    pings = list(router_list) if router_list is not None else [router, router, router]

    def ping(target):
        return pings.pop(0) if pings else None

    def resolve(name):
        return dns

    def tcp(host, port):
        return internet

    return {"ping": ping, "resolve": resolve, "tcp": tcp,
            "connectivity": lambda: portal, "link_up": lambda iface: link}


def diagnose(net=GOOD_NET, nm=NM_ON, wifi_data=GOOD_WIFI, on_update=None, **probe_opts):
    """Run the doctor; probe_opts (router=, internet=, dns=, portal=, link=, router_list=) feed the fake probes."""
    opts = probes(**probe_opts)
    return dr.run_doctor(net=net, nm=nm, wifi=wifi_data, on_update=on_update, **opts)


def step(diag, key):
    return next(s for s in diag.steps if s.key == key)


class Healthy(unittest.TestCase):
    def test_all_good(self):
        diag = diagnose()
        self.assertTrue(diag.done)
        self.assertEqual(diag.level, "ok")
        self.assertEqual([s.status for s in diag.steps], ["ok"] * 6)
        self.assertIn("router 3 ms", diag.verdict)
        self.assertIn("DNS 18 ms", diag.verdict)
        self.assertIn("internet 21 ms", diag.verdict)
        self.assertIsNone(diag.suggestion)

    def test_wifi_detail_names_network(self):
        diag = diagnose()
        self.assertEqual(step(diag, "link").detail, "Wi-Fi ExampleNet, signal Good (-58 dBm)")


class LinkAndAddress(unittest.TestCase):
    def test_wifi_off_skips_everything_else(self):
        diag = diagnose(net={}, nm=NmState(available=True, wifi_enabled=False), wifi_data=None)
        self.assertEqual(step(diag, "link").status, "fail")
        self.assertEqual(step(diag, "link").detail, "Wi-Fi is turned off")
        for key in ("address", "router", "dns", "internet", "portal"):
            self.assertEqual(step(diag, key).status, "skip")
        self.assertEqual(diag.suggestion, "wifi-on")
        self.assertEqual(diag.level, "fail")

    def test_no_default_route_with_saved_wifi_suggests_reconnect(self):
        saved = SavedConnection(name="ExampleNet", uuid="u1", kind="wifi", device=None, active=False,
                                state="", autoconnect=True, priority=0, last_used=None)
        nm = NmState(available=True, wifi_enabled=True, saved=(saved,))
        diag = diagnose(net={}, nm=nm, wifi_data=None)
        self.assertEqual(step(diag, "link").detail, "Not connected to any network")
        self.assertEqual(diag.suggestion, "reconnect")

    def test_no_ip_is_address_fail(self):
        diag = diagnose(net={"iface": "wlan0", "ip": None, "gateway": "192.0.2.1"})
        self.assertEqual(step(diag, "address").status, "fail")
        self.assertEqual(diag.suggestion, "reconnect")
        self.assertEqual(step(diag, "router").status, "skip")

    def test_self_assigned_169_is_address_fail(self):
        diag = diagnose(net={"iface": "wlan0", "ip": "169.254.10.4", "gateway": "192.0.2.1"})
        self.assertEqual(step(diag, "address").status, "fail")
        self.assertEqual(diag.suggestion, "reconnect")

    def test_link_down_is_fail(self):
        diag = diagnose(link=False)
        self.assertEqual(step(diag, "link").status, "fail")
        self.assertEqual(diag.suggestion, "reconnect")

    def test_weak_wifi_is_link_warn(self):
        diag = diagnose(wifi_data=wifi(quality="Weak", dbm=-78))
        link = step(diag, "link")
        self.assertEqual(link.status, "warn")
        self.assertIn("weak Wi-Fi signal (-78 dBm)", link.detail)
        self.assertEqual(diag.level, "warn")
        self.assertTrue(diag.verdict.startswith("Works, but: "))


class RouterAndInternet(unittest.TestCase):
    def test_router_silent_but_internet_ok_is_warn(self):
        diag = diagnose(router_list=[None, None, None])
        router = step(diag, "router")
        self.assertEqual(router.status, "warn")
        self.assertIn("ignores ping", router.detail)
        self.assertEqual(diag.level, "warn")
        self.assertIn("ignores ping", diag.verdict)

    def test_router_and_internet_down_internet_suggests_reconnect(self):
        diag = diagnose(router_list=[None, None, None], internet=None)
        self.assertEqual(step(diag, "router").status, "fail")
        internet = step(diag, "internet")
        self.assertEqual(internet.status, "fail")
        self.assertEqual(internet.detail, "No internet beyond your router")
        self.assertEqual(diag.suggestion, "reconnect")
        self.assertEqual(diag.level, "fail")

    def test_router_ok_internet_down_blames_provider(self):
        diag = diagnose(internet=None)
        internet = step(diag, "internet")
        self.assertEqual(internet.status, "fail")
        self.assertIn("provider", diag.verdict)
        self.assertIsNone(diag.suggestion)

    def test_router_lost_one_reply_is_warn(self):
        diag = diagnose(router_list=[3.0, None, 3.0])
        router = step(diag, "router")
        self.assertEqual(router.status, "warn")
        self.assertIn("lost 1 of 3", router.detail)

    def test_slow_router_is_warn(self):
        diag = diagnose(router=64.0)
        self.assertEqual(step(diag, "router").status, "warn")
        self.assertIn("64 ms", step(diag, "router").detail)

    def test_slow_internet_is_warn(self):
        diag = diagnose(internet=210.0)
        self.assertEqual(step(diag, "internet").status, "warn")
        self.assertIn("210 ms", step(diag, "internet").detail)

    def test_internet_falls_back_to_second_host(self):
        calls = []
        opts = probes()

        def tcp(host, port):
            calls.append(host)
            return None if host == "1.1.1.1" else 30.0

        opts["tcp"] = tcp
        diag = dr.run_doctor(net=GOOD_NET, nm=NM_ON, wifi=GOOD_WIFI, **opts)
        self.assertEqual(calls, ["1.1.1.1", "9.9.9.9"])
        self.assertEqual(step(diag, "internet").status, "ok")


class Dns(unittest.TestCase):
    def test_dns_both_none_fails_but_internet_still_tested(self):
        diag = diagnose(dns=None)
        dns = step(diag, "dns")
        self.assertEqual(dns.status, "fail")
        self.assertEqual(dns.detail, "Names do not resolve - DNS is broken")
        self.assertEqual(step(diag, "internet").status, "ok")
        self.assertEqual(diag.suggestion, "dns")

    def test_slow_dns_is_warn(self):
        diag = diagnose(dns=420.0)
        self.assertEqual(step(diag, "dns").status, "warn")
        self.assertIn("420 ms", step(diag, "dns").detail)


    def test_stalled_dual_lookup_is_explained(self):
        # IPv4+IPv6 lookups wait 5 s but IPv4 alone answers fast: the router drops one of the pair
        opts = probes(dns=5030.0)
        diag = dr.run_doctor(net=GOOD_NET, nm=NM_ON, wifi=GOOD_WIFI, resolve4=lambda name: 14.0, **opts)
        dns = step(diag, "dns")
        self.assertEqual(dns.status, "warn")
        self.assertIn("5 s", dns.detail)
        self.assertIn("IPv4 alone: 14 ms", dns.detail)
        self.assertEqual(diag.suggestion, "dns")

    def test_dns_none_but_ipv4_fast_is_stall_not_broken(self):
        opts = probes(dns=None)
        diag = dr.run_doctor(net=GOOD_NET, nm=NM_ON, wifi=GOOD_WIFI, resolve4=lambda name: 20.0, **opts)
        self.assertEqual(step(diag, "dns").status, "warn")
        self.assertIn("more than 7 s", step(diag, "dns").detail)


class Portal(unittest.TestCase):
    def test_portal_suggests_portal(self):
        diag = diagnose(portal="portal")
        self.assertEqual(step(diag, "portal").status, "fail")
        self.assertEqual(diag.suggestion, "portal")
        self.assertEqual(diag.level, "fail")

    def test_portal_beats_failed_internet_in_verdict(self):
        diag = diagnose(internet=None, portal="portal")
        self.assertIn("log in", diag.verdict)
        self.assertEqual(diag.suggestion, "portal")

    def test_limited_is_warn(self):
        diag = diagnose(portal="limited")
        self.assertEqual(step(diag, "portal").status, "warn")

    def test_connectivity_none_skips_and_level_unaffected(self):
        diag = diagnose(portal=None)
        self.assertEqual(step(diag, "portal").status, "skip")
        self.assertEqual(step(diag, "portal").detail, "Could not check")
        self.assertEqual(diag.level, "ok")


class Progress(unittest.TestCase):
    def test_running_is_emitted_before_each_result(self):
        updates = []
        diagnose(on_update=updates.append)
        for key, _ in dr.STEPS:
            running_at = next(i for i, d in enumerate(updates)
                              if step(d, key).status == "running")
            final_at = next(i for i, d in enumerate(updates)
                            if step(d, key).status not in ("running", "pending"))
            self.assertLess(running_at, final_at, key)
        self.assertTrue(updates[-1].done)


class Cancel(unittest.TestCase):
    def test_cancel_after_router_stops_the_rest(self):
        cancel = threading.Event()
        opts = probes()

        def resolve(name):
            cancel.set()        # the user pressed stop while DNS was being checked
            return 18.0

        opts["resolve"] = resolve
        diag = dr.run_doctor(net=GOOD_NET, nm=NM_ON, wifi=GOOD_WIFI, cancel=cancel, **opts)
        self.assertTrue(diag.done)
        self.assertEqual(diag.verdict, "Cancelled")
        self.assertEqual(step(diag, "router").status, "ok")
        self.assertEqual(step(diag, "internet").status, "skip")
        self.assertEqual(step(diag, "portal").status, "skip")
        self.assertIsNone(diag.suggestion)

    def test_cancel_during_router_pings_sends_no_more(self):
        cancel = threading.Event()
        opts = probes()
        calls = []

        def ping(target):
            calls.append(target)
            cancel.set()        # stop pressed while the first ping was out
            return 3.0

        opts["ping"] = ping
        diag = dr.run_doctor(net=GOOD_NET, nm=NM_ON, wifi=GOOD_WIFI, cancel=cancel, **opts)
        self.assertEqual(len(calls), 1)
        self.assertEqual(diag.verdict, "Cancelled")
        self.assertEqual(step(diag, "router").status, "skip")


if __name__ == "__main__":
    unittest.main()
