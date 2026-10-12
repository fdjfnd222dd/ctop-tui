"""Tests for ctop/alerts.py. Inputs are built from the model dataclasses; no machine data."""
from __future__ import annotations

import unittest

from ctop.alerts import AlertInput, Thresholds, evaluate
from ctop.models import Battery, Fan, PingData, PingResult, Thermal, WifiData

GOOD = (20.0,) * 10          # a full window of replies
LOST = (None,) * 10          # a full window of lost pings


def keys(alerts):
    return [a.key for a in alerts]


def by(alerts, key):
    for a in alerts:
        if a.key == key:
            return a
    return None


def battery(**kw):
    base = dict(pct=50, status="Discharging", watts=None, volts=None,
                wh_now=None, wh_full=None, wh_design=None, cycles=None)
    base.update(kw)
    return Battery(**base)


def ping(label, history, loss_pct, avg_ms=None):
    target = "192.0.2.1" if label == "Router" else "198.51.100.7"
    return PingResult(label=label, target=target, ms=None, avg_ms=avg_ms,
                      jitter_ms=None, loss_pct=loss_pct, history=tuple(history))


def wifi(quality, dbm=None, pct=None):
    return WifiData(iface="wlan0", ssid="ExampleNet", signal_pct=pct, signal_dbm=dbm,
                    bitrate_mbps=None, freq_mhz=None, channel=None, security="WPA2",
                    quality=quality)


class AlertRulesTest(unittest.TestCase):

    def test_empty_input_gives_no_alerts(self):
        self.assertEqual(evaluate(AlertInput()), [])

    def test_ram_levels(self):
        self.assertEqual(evaluate(AlertInput(ram_pct=84.9)), [])
        a = evaluate(AlertInput(ram_pct=85.0))
        self.assertEqual(keys(a), ["ram-high"])
        self.assertEqual(a[0].level, "warn")
        self.assertEqual(a[0].text, "Memory is 85% full.")
        a = evaluate(AlertInput(ram_pct=93.0))
        self.assertEqual(a[0].level, "crit")
        a = evaluate(AlertInput(ram_pct=93.6))
        self.assertEqual(keys(a), ["ram-high"])
        self.assertIn("94%", a[0].text)

    def test_swap_levels(self):
        self.assertEqual(evaluate(AlertInput(swap_pct=59.9)), [])
        a = evaluate(AlertInput(swap_pct=60.0))
        self.assertEqual((a[0].level, a[0].text), ("warn", "Swap is 60% full - memory is under pressure."))
        a = evaluate(AlertInput(swap_pct=85.0))
        self.assertEqual((a[0].level, a[0].text), ("crit", "Swap is 85% full - the computer will feel stuck."))

    def test_battery_low_only_when_discharging(self):
        a = evaluate(AlertInput(battery=battery(pct=7, status="Discharging")))
        self.assertEqual((a[0].level, a[0].text), ("crit", "Battery is at 7% - plug in now."))
        a = evaluate(AlertInput(battery=battery(pct=20, status="Discharging")))
        self.assertEqual((a[0].level, a[0].text), ("warn", "Battery is at 20% - plug in soon."))
        self.assertEqual(evaluate(AlertInput(battery=battery(pct=21, status="Discharging"))), [])
        self.assertIsNone(by(evaluate(AlertInput(battery=battery(pct=7, status="Charging"))), "battery-low"))

    def test_battery_drain_text_with_time_left(self):
        a = evaluate(AlertInput(battery=battery(watts=21.3, wh_now=30.0, status="Discharging")))
        self.assertEqual(a[0].text, "Battery is draining fast (21.3 W) - about 1 h 24 min left.")
        a = evaluate(AlertInput(battery=battery(watts=21.3, wh_now=None, status="Discharging")))
        self.assertEqual(a[0].text, "Battery is draining fast (21.3 W).")
        self.assertEqual(evaluate(AlertInput(battery=battery(watts=17.9, status="Discharging"))), [])
        self.assertEqual(evaluate(AlertInput(battery=battery(watts=25.0, status="Charging"))), [])

    def test_battery_drain_minutes_only(self):
        a = evaluate(AlertInput(battery=battery(watts=20.0, wh_now=10.0, status="Discharging")))
        self.assertEqual(a[0].text, "Battery is draining fast (20.0 W) - about 30 min left.")

    def test_battery_health_info(self):
        a = evaluate(AlertInput(battery=battery(wh_full=31.0, wh_design=50.0, status="Discharging")))
        self.assertEqual((a[0].level, a[0].text), ("info", "Battery holds only 62% of its original capacity."))
        self.assertEqual(evaluate(AlertInput(battery=battery(wh_full=35.0, wh_design=50.0))), [])
        a = evaluate(AlertInput(battery=battery(wh_full=31.0, wh_design=50.0, status="Charging")))
        self.assertEqual(keys(a), ["battery-health"])
        self.assertEqual(evaluate(AlertInput(battery=battery(wh_full=31.0, wh_design=None))), [])
        self.assertEqual(evaluate(AlertInput(battery=battery(wh_full=60.0, wh_design=50.0))), [])

    def test_cpu_heat(self):
        self.assertEqual(evaluate(AlertInput(cpu_temp=84.9)), [])
        a = evaluate(AlertInput(cpu_temp=85.0))
        self.assertEqual((a[0].level, a[0].text), ("warn", "CPU is hot (85°C)."))
        a = evaluate(AlertInput(cpu_temp=95.0))
        self.assertEqual(a[0].level, "crit")
        a = evaluate(AlertInput(cpu_temp=None, thermal=Thermal(cpu_temp=90.0)))
        self.assertEqual((a[0].level, a[0].text), ("warn", "CPU is hot (90°C)."))
        self.assertEqual(evaluate(AlertInput(cpu_temp=50.0, thermal=Thermal(cpu_temp=99.0))), [])

    def test_fans_idle(self):
        a = evaluate(AlertInput(thermal=Thermal(fans=(Fan("Fan 1", 0),), cpu_temp=78.0)))
        self.assertEqual((a[0].level, a[0].text),
                         ("warn", "Fans are not spinning while the CPU is at 78°C."))
        self.assertEqual(evaluate(AlertInput(thermal=Thermal(fans=(Fan("Fan 1", 1200),), cpu_temp=78.0))), [])
        self.assertEqual(evaluate(AlertInput(thermal=Thermal(fans=(), cpu_temp=78.0))), [])
        self.assertEqual(evaluate(AlertInput(thermal=Thermal(fans=(Fan("Fan 1", 0),), cpu_temp=74.0))), [])

    def test_wifi_weak_and_poor(self):
        a = evaluate(AlertInput(wifi=wifi("Weak", dbm=-76)))
        self.assertEqual(a[0].text, "Wi-Fi signal is weak (-76 dBm) - move closer to the router.")
        a = evaluate(AlertInput(wifi=wifi("Poor", pct=20)))
        self.assertEqual(a[0].text, "Wi-Fi signal is poor (20%) - move closer to the router.")
        self.assertEqual(evaluate(AlertInput(wifi=wifi("Fair", dbm=-70))), [])
        self.assertEqual(evaluate(AlertInput(wifi=wifi("Unknown"))), [])

    def test_ping_needs_enough_samples(self):
        short = PingData(internet=ping("Internet", (None,) * 5, 100.0))
        self.assertEqual(evaluate(AlertInput(ping=short)), [])
        full = PingData(internet=ping("Internet", LOST, 100.0))
        a = evaluate(AlertInput(ping=full))
        self.assertEqual((a[0].key, a[0].level), ("internet-loss", "crit"))

    def test_internet_loss_levels(self):
        def loss(pct):
            return evaluate(AlertInput(ping=PingData(internet=ping("Internet", GOOD, pct))))
        self.assertEqual(loss(9.9), [])
        a = loss(10.0)
        self.assertEqual((a[0].level, a[0].text),
                         ("warn", "Internet connection is dropping packets (10% lost)."))
        a = loss(50.0)
        self.assertEqual((a[0].level, a[0].text),
                         ("crit", "Internet is mostly unreachable (50% of pings lost)."))

    def test_router_loss(self):
        a = evaluate(AlertInput(ping=PingData(router=ping("Router", GOOD, 10.0))))
        self.assertEqual(keys(a), ["router-loss"])
        self.assertIn("Router is dropping packets (10% lost)", a[0].text)
        self.assertEqual(evaluate(AlertInput(ping=PingData(router=None))), [])

    def test_internet_slow_info(self):
        a = evaluate(AlertInput(ping=PingData(internet=ping("Internet", GOOD, 0.0, avg_ms=210.0))))
        self.assertEqual((a[0].level, a[0].text),
                         ("info", "Internet is slow to respond (average 210 ms)."))
        a = evaluate(AlertInput(ping=PingData(internet=ping("Internet", GOOD, 20.0, avg_ms=210.0))))
        self.assertEqual(keys(a), ["internet-loss"])
        self.assertEqual(evaluate(AlertInput(ping=PingData(internet=ping("Internet", GOOD, 0.0, avg_ms=149.0)))), [])

    def test_sorted_crit_then_warn_then_info(self):
        inp = AlertInput(
            ram_pct=95.0,
            cpu_temp=86.0,
            battery=battery(pct=7, status="Discharging", wh_full=31.0, wh_design=50.0),
        )
        a = evaluate(inp)
        self.assertEqual([x.level for x in a], ["crit", "crit", "warn", "info"])
        self.assertEqual(keys(a)[:2], ["ram-high", "battery-low"])

    def test_custom_thresholds(self):
        a = evaluate(AlertInput(ram_pct=60.0), Thresholds(ram_warn=50.0))
        self.assertEqual((a[0].level, a[0].text), ("warn", "Memory is 60% full."))

    def test_all_texts_are_single_line_ascii_except_degree(self):
        worst = AlertInput(
            ram_pct=100.0,
            swap_pct=99.0,
            battery=battery(pct=1, status="Discharging", watts=30.0, wh_now=5.0,
                            wh_full=20.0, wh_design=50.0),
            thermal=Thermal(fans=(Fan("Fan 1", 0),), cpu_temp=99.0),
            wifi=wifi("Poor", dbm=-90),
            ping=PingData(router=ping("Router", LOST, 100.0),
                          internet=ping("Internet", LOST, 100.0)),
        )
        a = evaluate(worst)
        self.assertEqual(len(a), 10)
        for alert in a:
            self.assertNotIn("\n", alert.text)
            self.assertTrue(alert.text.endswith("."), alert.text)
            self.assertTrue(all(ord(c) < 128 or c == "°" for c in alert.text), alert.text)


if __name__ == "__main__":
    unittest.main()
