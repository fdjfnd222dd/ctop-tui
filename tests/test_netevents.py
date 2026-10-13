"""Tests for ctop/netevents.py. Snapshots are built from the models directly; times are passed in, nothing is slept."""
from __future__ import annotations

import unittest

from ctop.models import LinkInfo, NmState, PingData, PingResult, VpnStatus, VpnTunnel, WifiData
from ctop.netevents import EventLog

GW = "192.0.2.1"
INTERNET = "198.51.100.1"
ALL_OK = (1.0, 1.0, 1.0)


def wifi(ssid="ExampleNet", quality="Good", dbm=-55):
    return WifiData(iface="wlan0", ssid=ssid, signal_pct=60, signal_dbm=dbm, bitrate_mbps=400.0,
                    freq_mhz=5680, channel=136, security="WPA2", quality=quality)


def link(bssid="AA:BB:CC:DD:EE:01", band="5 GHz", channel=136):
    return LinkInfo(iface="wlan0", connected=True, bssid=bssid, band=band, channel=channel)


def result(label, target, ms, avg, history):
    return PingResult(label=label, target=target, ms=ms, avg_ms=avg, jitter_ms=None, loss_pct=0.0,
                      history=tuple(history))


def ping(internet_ms=21.0, internet_avg=21.0, internet_hist=(21.0, 21.0, 21.0),
         router_ms=2.0, router_hist=(2.0, 2.0, 2.0)):
    return PingData(
        router=result("Router", GW, router_ms, router_ms, router_hist),
        internet=result("Internet", INTERNET, internet_ms, internet_avg, internet_hist),
    )


def net(ip="192.0.2.10", gateway=GW):
    return {"iface": "wlan0", "ip": ip, "gateway": gateway}


def nm(connectivity="full", wifi_enabled=True):
    return NmState(available=True, connectivity=connectivity, wifi_enabled=wifi_enabled)


def vpn(tailscale=None, tunnels=()):
    return VpnStatus(tailscale=tailscale, tunnels=tuple(tunnels))


def texts(log):
    return [e.text for e in log.events]


class FirstObservationTests(unittest.TestCase):
    def test_first_observation_logs_nothing(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(), link=link(), ping=ping(), net=net(), nm=nm(), vpn=vpn("Running"))
        self.assertEqual(log.events, ())

    def test_none_arguments_never_log(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi())
        log.observe(5.0)                       # nothing new at all
        log.observe(6.0, wifi=None, net=None)  # collectors not ready
        self.assertEqual(log.events, ())

    def test_missing_collector_does_not_look_like_a_drop(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi("ExampleNet"))
        log.observe(5.0, wifi=None, net=net())
        log.observe(9.0, wifi=wifi("ExampleNet"))
        self.assertEqual(log.events, ())


class RuleTests(unittest.TestCase):
    def test_wifi_connected(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(None))
        log.observe(1.0, wifi=wifi("ExampleNet"))
        self.assertEqual(texts(log), ["Connected to ExampleNet"])
        self.assertEqual(log.events[-1].level, "info")

    def test_wifi_dropped(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi("ExampleNet"))
        log.observe(1.0, wifi=wifi(None))
        self.assertEqual(texts(log), ["Wi-Fi dropped (was ExampleNet)"])
        self.assertEqual(log.events[-1].level, "warn")

    def test_wifi_switched(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi("ExampleNet"))
        log.observe(1.0, wifi=wifi("Neighbour Net"))
        self.assertEqual(texts(log), ["Switched Wi-Fi from ExampleNet to Neighbour Net"])

    def test_roamed_to_other_access_point(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(), link=link("AA:BB:CC:DD:EE:01", "2.4 GHz", 1))
        log.observe(1.0, wifi=wifi(), link=link("AA:BB:CC:DD:EE:02", "5 GHz", 36))
        self.assertEqual(texts(log), ["Roamed to another access point (5 GHz, channel 36)"])

    def test_roam_not_logged_when_network_changed(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi("ExampleNet"), link=link("AA:BB:CC:DD:EE:01"))
        log.observe(1.0, wifi=wifi("Cafe: Free"), link=link("AA:BB:CC:DD:EE:02"))
        self.assertEqual(texts(log), ["Switched Wi-Fi from ExampleNet to Cafe: Free"])

    def test_band_moved_with_same_access_point(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(), link=link("AA:BB:CC:DD:EE:01", "2.4 GHz", 1))
        log.observe(1.0, wifi=wifi(), link=link("AA:BB:CC:DD:EE:01", "5 GHz", 36))
        self.assertEqual(texts(log), ["Moved to 5 GHz"])

    def test_signal_weak_then_recovered(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(quality="Good", dbm=-55))
        log.observe(1.0, wifi=wifi(quality="Weak", dbm=-78))
        self.assertEqual(texts(log), ["Wi-Fi signal became weak (-78 dBm)"])
        self.assertEqual(log.events[-1].level, "warn")
        log.observe(2.0, wifi=wifi(quality="Good", dbm=-58))
        self.assertEqual(texts(log)[-1], "Wi-Fi signal recovered")
        self.assertEqual(log.events[-1].level, "info")

    def test_signal_hovering_at_the_line_does_not_flap(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(quality="Good", dbm=-55))
        log.observe(1.0, wifi=wifi(quality="Weak", dbm=-71))
        for i, q in enumerate(("Fair", "Weak", "Fair", "Weak")):
            log.observe(20.0 * (i + 2), wifi=wifi(quality=q, dbm=-70))
        self.assertEqual(texts(log), ["Wi-Fi signal became weak (-71 dBm)"])

    def test_signal_weak_without_dbm(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(quality="Fair", dbm=None))
        log.observe(1.0, wifi=wifi(quality="Poor", dbm=None))
        self.assertEqual(texts(log), ["Wi-Fi signal became weak"])

    def test_ip_changed(self):
        log = EventLog()
        log.observe(0.0, net=net("192.0.2.10"))
        log.observe(1.0, net=net("192.0.2.11"))
        self.assertEqual(texts(log), ["IP address changed: 192.0.2.10 -> 192.0.2.11"])

    def test_got_ip(self):
        log = EventLog()
        log.observe(0.0, net=net(None))
        log.observe(1.0, net=net("192.0.2.10"))
        self.assertEqual(texts(log), ["Got an IP address: 192.0.2.10"])

    def test_lost_ip(self):
        log = EventLog()
        log.observe(0.0, net=net("192.0.2.10"))
        log.observe(1.0, net=net(None))
        self.assertEqual(texts(log), ["Lost the IP address"])
        self.assertEqual(log.events[-1].level, "warn")

    def test_router_changed(self):
        log = EventLog()
        log.observe(0.0, net=net(gateway=GW))
        log.observe(1.0, net=net(gateway="192.0.2.254"))
        self.assertEqual(texts(log), ["Router changed: 192.0.2.1 -> 192.0.2.254"])

    def test_internet_loss_and_back_with_elapsed_seconds(self):
        log = EventLog()
        log.observe(0.0, ping=ping())
        lost = (21.0, None, None, None)
        log.observe(100.0, ping=ping(internet_ms=None, internet_avg=21.0, internet_hist=lost))
        self.assertEqual(texts(log), ["Internet stopped answering"])
        self.assertEqual(log.events[-1].level, "warn")
        # Still lost on the next look: no second "stopped" line.
        log.observe(110.0, ping=ping(internet_ms=None, internet_avg=21.0, internet_hist=lost))
        self.assertEqual(len(log.events), 1)
        log.observe(142.0, ping=ping(internet_ms=22.0, internet_avg=21.0, internet_hist=(None, None, None, 22.0)))
        self.assertEqual(texts(log)[-1], "Internet back after 42 s")
        self.assertEqual(log.events[-1].level, "info")

    def test_router_loss_and_back(self):
        log = EventLog()
        log.observe(0.0, ping=ping())
        log.observe(50.0, ping=ping(router_ms=None, router_hist=(None, None, None)))
        self.assertEqual(texts(log), ["Router stopped answering"])
        log.observe(57.0, ping=ping(router_ms=3.0, router_hist=(None, None, 3.0)))
        self.assertEqual(texts(log)[-1], "Router back after 7 s")

    def test_lag_spike(self):
        log = EventLog()
        log.observe(0.0, ping=ping(internet_ms=21.0, internet_avg=21.0))
        log.observe(10.0, ping=ping(internet_ms=480.0, internet_avg=21.0))
        self.assertEqual(texts(log), ["Lag spike: 480 ms to the internet (normally 21 ms)"])
        self.assertEqual(log.events[-1].level, "warn")

    def test_small_bump_is_not_a_spike(self):
        log = EventLog()
        log.observe(0.0, ping=ping(internet_ms=21.0, internet_avg=21.0))
        log.observe(10.0, ping=ping(internet_ms=120.0, internet_avg=21.0))   # below the 150 ms floor
        self.assertEqual(log.events, ())

    def test_captive_portal(self):
        log = EventLog()
        log.observe(0.0, nm=nm("full"))
        log.observe(1.0, nm=nm("portal"))
        self.assertEqual(texts(log), ["Captive portal: this network wants a login page"])

    def test_internet_access_lost_and_back(self):
        log = EventLog()
        log.observe(0.0, nm=nm("full"))
        log.observe(1.0, nm=nm("limited"))
        self.assertEqual(texts(log), ["Internet access lost (connected to the network, but no internet)"])
        self.assertEqual(log.events[-1].level, "warn")
        log.observe(2.0, nm=nm("full"))
        self.assertEqual(texts(log)[-1], "Internet access is back")

    def test_wifi_dropping_is_not_reported_as_connected_but_no_internet(self):
        log = EventLog()
        log.observe(0.0, nm=nm("full"))
        log.observe(1.0, nm=NmState(available=True, state="disconnected", connectivity="none"))
        self.assertEqual(log.events, ())
        log.observe(2.0, nm=NmState(available=True, state="connected", connectivity="full"))
        self.assertEqual(log.events, ())

    def test_wifi_turned_off_and_on(self):
        log = EventLog()
        log.observe(0.0, nm=nm(wifi_enabled=True))
        log.observe(1.0, nm=nm(wifi_enabled=False))
        self.assertEqual(texts(log), ["Wi-Fi turned off"])
        log.observe(2.0, nm=nm(wifi_enabled=True))
        self.assertEqual(texts(log)[-1], "Wi-Fi turned on")

    def test_tailscale_connected_and_disconnected(self):
        log = EventLog()
        log.observe(0.0, vpn=vpn("Running"))
        log.observe(1.0, vpn=vpn("Stopped"))
        self.assertEqual(texts(log), ["Tailscale disconnected"])
        log.observe(2.0, vpn=vpn("Running"))
        self.assertEqual(texts(log)[-1], "Tailscale connected")

    def test_vpn_tunnel_up_and_down(self):
        log = EventLog()
        log.observe(0.0, vpn=vpn())
        log.observe(1.0, vpn=vpn(tunnels=[VpnTunnel("wg0", "WireGuard", True, "10.8.0.2")]))
        self.assertEqual(texts(log), ["VPN wg0 up"])
        log.observe(2.0, vpn=vpn(tunnels=[VpnTunnel("wg0", "WireGuard", False, None)]))
        self.assertEqual(texts(log)[-1], "VPN wg0 down")

    def test_vpn_tunnel_disappearing_while_up(self):
        log = EventLog()
        log.observe(0.0, vpn=vpn(tunnels=[VpnTunnel("wg0", "WireGuard", True, "10.8.0.2")]))
        log.observe(1.0, vpn=vpn())
        self.assertEqual(texts(log), ["VPN wg0 down"])


class LogBehaviourTests(unittest.TestCase):
    def test_events_tuple_reused_when_nothing_changed(self):
        log = EventLog()
        log.observe(0.0, wifi=wifi(None))
        log.observe(1.0, wifi=wifi("ExampleNet"))
        first = log.events
        log.observe(2.0, wifi=wifi("ExampleNet"))
        self.assertIs(log.events, first)
        self.assertIsInstance(first, tuple)

    def test_max_keeps_newest_200(self):
        log = EventLog()
        for i in range(250):
            log.add(float(i * 20), "info", "event %d" % i)   # spaced out so debounce never hides one
        events = log.events
        self.assertEqual(len(events), 200)
        self.assertEqual(events[0].text, "event 50")
        self.assertEqual(events[-1].text, "event 249")

    def test_debounce_same_text_within_10_seconds(self):
        log = EventLog()
        log.add(0.0, "info", "x")
        log.add(5.0, "info", "x")
        self.assertEqual(len(log.events), 1)
        log.add(11.0, "info", "x")
        self.assertEqual(len(log.events), 2)

    def test_spike_rate_limit_30_seconds_apart_is_one_event(self):
        log = EventLog()
        log.observe(0.0, ping=ping(internet_ms=21.0, internet_avg=21.0))
        log.observe(100.0, ping=ping(internet_ms=480.0, internet_avg=21.0))
        log.observe(130.0, ping=ping(internet_ms=500.0, internet_avg=21.0))
        spikes = [t for t in texts(log) if t.startswith("Lag spike")]
        self.assertEqual(len(spikes), 1)

    def test_spike_rate_limit_61_seconds_apart_is_two_events(self):
        log = EventLog()
        log.observe(0.0, ping=ping(internet_ms=21.0, internet_avg=21.0))
        log.observe(100.0, ping=ping(internet_ms=480.0, internet_avg=21.0))
        log.observe(161.0, ping=ping(internet_ms=500.0, internet_avg=21.0))
        spikes = [t for t in texts(log) if t.startswith("Lag spike")]
        self.assertEqual(len(spikes), 2)

    def test_add_is_available_for_app_messages(self):
        log = EventLog()
        log.add(3.0, "info", "You connected to ExampleNet")
        self.assertEqual(texts(log), ["You connected to ExampleNet"])
        self.assertEqual(log.events[0].ts, 3.0)


if __name__ == "__main__":
    unittest.main()
