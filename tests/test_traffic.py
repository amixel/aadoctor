"""Traffic aggregation (SPEC-005).

The clock is injected, so windows and expiry are tested by moving time rather
than by sleeping.
"""

from __future__ import annotations

import random
import unittest

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor.analyzers import traffic
from aadoctor.analyzers.traffic import BoundedCounter, TrafficAggregator
from aadoctor.parsers.nginx_access import AccessEvent
from aadoctor.parsers.nginx_error import ErrorEvent


class FakeClock:
    """A clock the test moves on purpose."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def access(site="a.com", ip="1.1.1.1", path="/", status=200, method="GET", agent="UA"):
    return AccessEvent(
        remote_addr=ip,
        method=method,
        path=path,
        status=status,
        user_agent=agent,
        site=site,
    )


def error(site="a.com", kind="upstream_timeout", level="error"):
    return ErrorEvent(level=level, kind=kind, message="boom", site=site)


class AggregatorCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.aggregator = TrafficAggregator(clock=self.clock)

    def feed(self, event, times=1):
        for _ in range(times):
            self.aggregator.add(event)

    def snapshot(self, window=300, **kwargs):
        return self.aggregator.snapshot(window_seconds=window, **kwargs)


class Counting(AggregatorCase):
    def test_a_single_request(self):
        self.feed(access())
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 1)
        self.assertEqual(snapshot.sites[0].key, "a.com")
        self.assertEqual(snapshot.sites[0].count, 1)

    def test_requests_by_site(self):
        self.feed(access(site="a.com"), 8)
        self.feed(access(site="b.com"), 2)
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 10)
        self.assertEqual([(e.key, e.count) for e in snapshot.sites],
                         [("a.com", 8), ("b.com", 2)])
        self.assertAlmostEqual(snapshot.sites[0].share, 0.8)

    def test_requests_by_ip(self):
        self.feed(access(ip="1.1.1.1"), 5)
        self.feed(access(ip="2001:db8::1"), 3)

        self.assertEqual(
            [(e.key, e.count) for e in self.snapshot().ips],
            [("1.1.1.1", 5), ("2001:db8::1", 3)],
        )

    def test_requests_by_path(self):
        self.feed(access(path="/wp-cron.php"), 7)
        self.feed(access(path="/"), 1)

        self.assertEqual(self.snapshot().paths[0].key, "/wp-cron.php")

    def test_requests_by_status_and_class(self):
        for status in (200, 200, 301, 404, 404, 404, 502, 499):
            self.feed(access(status=status))
        snapshot = self.snapshot()

        self.assertEqual(snapshot.statuses["404"], 3)
        self.assertEqual(snapshot.statuses["502"], 1)
        self.assertEqual(snapshot.status_classes,
                         {"2xx": 2, "3xx": 1, "4xx": 4, "5xx": 1})

    def test_requests_by_method_including_unusual_ones(self):
        for method in ("GET", "GET", "POST", "HEAD", "PROPFIND"):
            self.feed(access(method=method))
        methods = self.snapshot().methods

        self.assertEqual(methods["GET"], 2)
        # No whitelist: a method we do not know is still counted.
        self.assertEqual(methods["PROPFIND"], 1)

    def test_user_agents_are_counted(self):
        self.feed(access(agent="Googlebot/2.1"), 3)

        self.assertEqual(self.snapshot().user_agents[0].key, "Googlebot/2.1")

    def test_missing_fields_become_a_placeholder_not_a_crash(self):
        self.aggregator.add(AccessEvent(site="a.com"))
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 1)
        self.assertEqual(snapshot.paths[0].key, traffic.UNKNOWN)
        self.assertEqual(snapshot.ips[0].key, traffic.UNKNOWN)

    def test_a_request_without_a_status_is_still_a_request(self):
        self.aggregator.add(access(status=None))
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 1)
        self.assertEqual(snapshot.statuses, {})

    def test_the_query_string_is_not_part_of_the_key(self):
        """The parser splits them; aggregation counts the path (README §60)."""
        self.feed(AccessEvent(path="/produto", query_string="id=1", site="a.com"))
        self.feed(AccessEvent(path="/produto", query_string="id=2", site="a.com"))
        self.feed(AccessEvent(path="/produto", query_string="id=3", site="a.com"))

        paths = self.snapshot().paths
        self.assertEqual(len(paths), 1)
        self.assertEqual((paths[0].key, paths[0].count), ("/produto", 3))


class Errors(AggregatorCase):
    def test_error_events_are_counted_apart_from_requests(self):
        self.feed(access(), 5)
        self.feed(error(), 2)
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 5)
        self.assertEqual(snapshot.total_errors, 2)

    def test_errors_by_kind(self):
        self.feed(error(kind="upstream_timeout"), 38)
        self.feed(error(kind="php_fatal"), 12)
        kinds = self.snapshot().error_kinds

        self.assertEqual(kinds["upstream_timeout"], 38)
        self.assertEqual(kinds["php_fatal"], 12)

    def test_errors_by_level(self):
        self.feed(error(level="error"), 3)
        self.feed(error(level="warn"), 1)

        self.assertEqual(self.snapshot().error_levels, {"error": 3, "warn": 1})

    def test_an_unclassified_error_is_counted_as_other(self):
        self.feed(error(kind=None), 4)

        self.assertEqual(self.snapshot().error_kinds, {"other": 4})

    def test_errors_by_site(self):
        self.feed(error(site="a.com"), 20)
        self.feed(error(site="b.com"), 2)

        self.assertEqual(self.snapshot().error_sites, {"a.com": 20, "b.com": 2})


class Unparsed(AggregatorCase):
    def test_an_unparsed_line_is_not_a_request(self):
        self.feed(access(), 3)
        for _ in range(7):
            self.aggregator.note_unparsed(traffic.ACCESS, "a.com")
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 3)
        self.assertEqual(snapshot.access_unparsed, 7)
        self.assertAlmostEqual(snapshot.unparsed_ratio, 0.7)

    def test_unparsed_error_lines_are_tracked_separately(self):
        self.aggregator.note_unparsed(traffic.ERROR)

        self.assertEqual(self.snapshot().error_unparsed, 1)

    def test_the_ratio_is_zero_on_an_empty_window(self):
        self.assertEqual(self.snapshot().unparsed_ratio, 0.0)

    def test_unparsed_lines_are_attributed_to_their_site(self):
        self.feed(access(site="a.com"), 1)
        for _ in range(4):
            self.aggregator.note_unparsed(traffic.ACCESS, "a.com")

        site = self.snapshot().site("a.com")
        self.assertEqual(site.unparsed, 4)
        self.assertEqual(site.requests, 1)


class Windows(AggregatorCase):
    def test_one_minute_and_five_minute_windows_differ(self):
        self.feed(access(), 10)
        self.clock.advance(120)          # two minutes later
        self.feed(access(), 5)

        self.assertEqual(self.snapshot(60).total_requests, 5)
        self.assertEqual(self.snapshot(300).total_requests, 15)

    def test_data_leaves_the_one_minute_window(self):
        self.feed(access(), 4)
        self.clock.advance(70)

        self.assertEqual(self.snapshot(60).total_requests, 0)
        self.assertEqual(self.snapshot(300).total_requests, 4)

    def test_data_leaves_the_five_minute_window(self):
        self.feed(access(), 4)
        self.clock.advance(320)

        self.assertEqual(self.snapshot(300).total_requests, 0)

    def test_time_passing_without_events_empties_the_window(self):
        """A quiet server must not keep reporting old traffic."""
        self.feed(access(), 100)
        self.assertEqual(self.snapshot(300).total_requests, 100)

        self.clock.advance(3600)
        snapshot = self.snapshot(300)

        self.assertEqual(snapshot.total_requests, 0)
        self.assertEqual(snapshot.sites, [])
        self.assertEqual(self.aggregator.bucket_count, 0)

    def test_an_empty_window_has_sane_values(self):
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 0)
        self.assertEqual(snapshot.requests_per_second, 0.0)
        self.assertEqual(snapshot.sites, [])
        self.assertEqual(snapshot.status_classes, {})

    def test_requests_per_second_uses_the_window(self):
        self.feed(access(), 300)

        self.assertAlmostEqual(self.snapshot(300).requests_per_second, 1.0)

    def test_expired_buckets_are_dropped_from_memory(self):
        self.feed(access(), 1)
        self.clock.advance(20)
        self.feed(access(), 1)
        self.assertEqual(self.aggregator.bucket_count, 2)

        self.clock.advance(400)
        self.aggregator.expire()

        self.assertEqual(self.aggregator.bucket_count, 0)

    def test_a_window_longer_than_the_maximum_is_clamped(self):
        self.feed(access(), 1)

        self.assertEqual(self.snapshot(99999).window_seconds, 300)

    def test_buckets_are_ten_seconds_by_default(self):
        self.feed(access(), 1)
        self.clock.advance(5)
        self.feed(access(), 1)
        self.assertEqual(self.aggregator.bucket_count, 1)

        self.clock.advance(10)
        self.feed(access(), 1)
        self.assertEqual(self.aggregator.bucket_count, 2)


class PerSite(AggregatorCase):
    def setUp(self):
        super().setUp()
        self.feed(access(site="site-a", path="/wp-cron.php", ip="IP-A", status=200), 900)
        self.feed(access(site="site-a", path="/wp-cron.php", ip="IP-X", status=502), 100)
        self.feed(access(site="site-b", path="/api", ip="IP-B", status=200), 450)
        self.feed(access(site="site-b", path="/other", ip="IP-C", status=200), 50)
        self.feed(error(site="site-a", kind="upstream_timeout"), 20)
        self.feed(error(site="site-b", kind="upstream_timeout"), 2)

    def test_each_site_keeps_its_own_paths(self):
        snapshot = self.snapshot()

        self.assertEqual(snapshot.site("site-a").paths[0].key, "/wp-cron.php")
        self.assertEqual(snapshot.site("site-a").paths[0].count, 1000)
        self.assertEqual(snapshot.site("site-b").paths[0].key, "/api")
        self.assertEqual(snapshot.site("site-b").paths[0].count, 450)

    def test_each_site_keeps_its_own_ips(self):
        snapshot = self.snapshot()

        self.assertEqual(snapshot.site("site-a").ips[0].key, "IP-A")
        self.assertEqual(snapshot.site("site-a").ips[0].count, 900)
        self.assertEqual(snapshot.site("site-b").ips[0].key, "IP-B")

    def test_a_sites_ip_share_is_relative_to_that_site(self):
        site = self.snapshot().site("site-a")

        self.assertAlmostEqual(site.ips[0].share, 0.9)

    def test_each_site_keeps_its_own_statuses(self):
        snapshot = self.snapshot()

        self.assertEqual(snapshot.site("site-a").statuses["502"], 100)
        self.assertNotIn("502", snapshot.site("site-b").statuses)

    def test_errors_are_attributed_to_the_site_that_produced_them(self):
        """A global total of 22 does not say who caused 20 of them."""
        snapshot = self.snapshot()

        self.assertEqual(snapshot.error_kinds["upstream_timeout"], 22)
        self.assertEqual(snapshot.site("site-a").error_kinds["upstream_timeout"], 20)
        self.assertEqual(snapshot.site("site-b").error_kinds["upstream_timeout"], 2)

    def test_sites_are_ordered_by_request_count(self):
        names = [site.name for site in self.snapshot().per_site]

        self.assertEqual(names[:2], ["site-a", "site-b"])

    def test_site_share_is_of_the_server_total(self):
        snapshot = self.snapshot()

        self.assertAlmostEqual(snapshot.site("site-a").share, 1000 / 1500)


class Determinism(AggregatorCase):
    def test_ties_are_broken_by_key(self):
        for name in ("c.com", "a.com", "b.com"):
            self.feed(access(site=name), 5)

        self.assertEqual([e.key for e in self.snapshot().sites],
                         ["a.com", "b.com", "c.com"])

    def test_two_snapshots_of_the_same_data_agree(self):
        for index in range(50):
            self.feed(access(site=f"s{index % 5}.com", path=f"/p{index % 7}"))

        first = self.snapshot()
        second = self.snapshot()

        self.assertEqual([e.key for e in first.paths], [e.key for e in second.paths])
        self.assertEqual(first.as_dict()["sites"], second.as_dict()["sites"])

    def test_the_snapshot_does_not_share_state_with_the_aggregator(self):
        self.feed(access(), 3)
        snapshot = self.snapshot()

        snapshot.statuses["200"] = 999
        snapshot.sites.clear()

        self.assertEqual(self.snapshot().statuses["200"], 3)
        self.assertEqual(len(self.snapshot().sites), 1)


class Cardinality(AggregatorCase):
    def test_a_path_flood_does_not_grow_memory_without_bound(self):
        for index in range(50000):
            self.feed(access(path=f"/random/{index}"))

        self.assertLessEqual(self.aggregator.key_count, 20000)
        self.assertEqual(self.snapshot().total_requests, 50000)

    def test_an_ip_flood_is_bounded_too(self):
        for index in range(20000):
            self.feed(access(ip=f"10.0.{index // 256}.{index % 256}"))

        snapshot = self.snapshot()
        self.assertLessEqual(len(snapshot.ips), 10)
        self.assertGreater(snapshot.other["ips"], 0)

    def test_displaced_volume_is_reported_not_silently_lost(self):
        for index in range(10000):
            self.feed(access(path=f"/{index}"))
        snapshot = self.snapshot()

        counted = sum(entry.count for entry in snapshot.paths)
        self.assertGreater(snapshot.other["paths"], 0)
        self.assertLessEqual(counted, snapshot.total_requests)

    def test_a_very_long_path_is_truncated_deterministically(self):
        long_path = "/" + "a" * 5000
        self.feed(access(path=long_path), 2)
        key = self.snapshot().paths[0].key

        self.assertLessEqual(len(key), traffic.MAX_PATH_CHARS + len(traffic.TRUNCATED))
        self.assertTrue(key.endswith(traffic.TRUNCATED))

    def test_a_very_long_user_agent_is_truncated(self):
        self.feed(access(agent="Mozilla/" + "x" * 4000))
        key = self.snapshot().user_agents[0].key

        self.assertLessEqual(len(key), traffic.MAX_AGENT_CHARS + len(traffic.TRUNCATED))


class HeavyHitters(AggregatorCase):
    def test_a_flood_of_unique_paths_cannot_hide_the_dominant_one(self):
        """The attack this bound exists for (SPEC-005).

        Ten thousand one-off paths arrive first, then the path that actually
        matters. Keeping "the first N keys seen" would lose it entirely.
        """
        for index in range(10000):
            self.feed(access(path=f"/noise/{index}"))
        self.feed(access(path="/wp-cron.php"), 5000)

        snapshot = self.snapshot()

        self.assertEqual(snapshot.paths[0].key, "/wp-cron.php")
        self.assertEqual(snapshot.paths[0].count, 5000)

    def test_it_survives_noise_interleaved_with_the_heavy_key(self):
        rng = random.Random(1234)
        for index in range(20000):
            if index % 4 == 0:
                self.feed(access(path="/wp-cron.php"))
            else:
                self.feed(access(path=f"/noise/{rng.random()}"))

        self.assertEqual(self.snapshot().paths[0].key, "/wp-cron.php")

    def test_a_dominant_ip_survives_an_ip_flood(self):
        for index in range(10000):
            self.feed(access(ip=f"10.1.{index // 256}.{index % 256}"))
        self.feed(access(ip="185.1.2.3"), 4000)

        self.assertEqual(self.snapshot().ips[0].key, "185.1.2.3")

    def test_the_bounded_counter_keeps_the_heaviest_keys(self):
        counter = BoundedCounter(limit=10)
        for index in range(1000):
            counter.add(f"cold-{index}")
        counter.add("hot", 500)

        top = sorted(counter.counts.items(), key=lambda item: -item[1])
        self.assertEqual(top[0], ("hot", 500))
        self.assertLessEqual(len(counter.counts), 20)

    def test_the_counter_total_survives_cutting(self):
        counter = BoundedCounter(limit=5)
        for index in range(100):
            counter.add(f"k{index}")

        self.assertEqual(counter.total, 100)


class Volume(AggregatorCase):
    def test_a_hundred_thousand_events_are_counted_exactly(self):
        sites = ["a.com", "b.com", "c.com"]
        for index in range(100000):
            self.feed(access(site=sites[index % 3], path=f"/p{index % 50}"))

        snapshot = self.snapshot()
        self.assertEqual(snapshot.total_requests, 100000)
        self.assertEqual(sum(entry.count for entry in snapshot.sites), 100000)

    def test_memory_tracks_keys_not_requests(self):
        for _ in range(100000):
            self.feed(access(path="/same", ip="1.1.1.1"))

        # One bucket, a handful of keys, whatever the request count.
        self.assertLess(self.aggregator.key_count, 50)


class Dispatch(AggregatorCase):
    def test_add_routes_by_event_type(self):
        self.aggregator.add(access())
        self.aggregator.add(error())
        snapshot = self.snapshot()

        self.assertEqual(snapshot.total_requests, 1)
        self.assertEqual(snapshot.total_errors, 1)

    def test_an_unknown_object_is_ignored_rather_than_crashing(self):
        self.aggregator.add(object())

        self.assertEqual(self.snapshot().total_requests, 0)


class Serialization(AggregatorCase):
    def test_the_snapshot_serialises_to_plain_data(self):
        import json

        self.feed(access(site="a.com", path="/x", status=502), 3)
        self.feed(error(site="a.com"), 1)

        payload = json.dumps(self.snapshot().as_dict())
        restored = json.loads(payload)

        self.assertEqual(restored["total_requests"], 3)
        self.assertEqual(restored["status_classes"]["5xx"], 3)
        self.assertEqual(restored["per_site"][0]["name"], "a.com")

    def test_no_raw_log_content_is_in_the_snapshot(self):
        import json

        event = access(path="/secret", agent="Mozilla")
        event.request_target = "/secret?token=abcdef123456"
        event.referer = "https://internal.example/admin"
        self.aggregator.add(event)

        payload = json.dumps(self.snapshot().as_dict())

        self.assertNotIn("token=abcdef123456", payload)
        self.assertNotIn("internal.example", payload)


class RealisticScenario(AggregatorCase):
    """The shape aaDoctor exists to make visible - without naming a cause."""

    def setUp(self):
        super().setUp()
        # Ordinary traffic across three sites.
        self.feed(access(site="site-b", path="/", ip="9.9.9.1", status=200), 1000)
        self.feed(access(site="site-c", path="/", ip="9.9.9.2", status=200), 500)
        self.feed(access(site="site-a", path="/", ip="9.9.9.3", status=200), 3000)

        # Then one path, mostly from one address, and upstream trouble.
        self.feed(access(site="site-a", path="/wp-cron.php", ip="185.1.2.3", status=200), 4500)
        self.feed(access(site="site-a", path="/wp-cron.php", ip="177.9.9.9", status=200), 380)
        self.feed(access(site="site-a", path="/wp-cron.php", ip="185.1.2.3", status=502), 120)
        self.feed(error(site="site-a", kind="upstream_timeout"), 40)

        self.snap = self.snapshot()

    def test_the_busiest_site_is_visible(self):
        self.assertEqual(self.snap.sites[0].key, "site-a")
        self.assertEqual(self.snap.sites[0].count, 8000)
        self.assertGreater(self.snap.sites[0].share, 0.8)

    def test_the_busiest_path_of_that_site_is_visible(self):
        site = self.snap.site("site-a")

        self.assertEqual(site.paths[0].key, "/wp-cron.php")
        self.assertEqual(site.paths[0].count, 5000)

    def test_the_busiest_address_of_that_site_is_visible(self):
        site = self.snap.site("site-a")

        self.assertEqual(site.ips[0].key, "185.1.2.3")
        self.assertEqual(site.ips[0].count, 4620)

    def test_the_error_statuses_are_visible_per_site(self):
        self.assertEqual(self.snap.site("site-a").statuses["502"], 120)

    def test_the_upstream_timeouts_are_visible_per_site(self):
        self.assertEqual(self.snap.site("site-a").error_kinds["upstream_timeout"], 40)

    def test_nothing_in_the_snapshot_claims_a_cause(self):
        """SPEC-005 measures. Naming a culprit is SPEC-007's job."""
        import json

        payload = json.dumps(self.snap.as_dict()).lower()

        for word in ("cause", "attack", "culprit", "responsible", "finding", "confidence"):
            self.assertNotIn(word, payload)


if __name__ == "__main__":
    unittest.main()
