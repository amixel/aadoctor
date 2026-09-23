"""SPEC-007 - the rules themselves, one at a time.

Thresholds are where the bugs are, so most of this is boundaries: just below,
exactly on, just above. The rest is about honesty - a rule that cannot be
evaluated has to say so rather than quietly not firing, and a rule resting on
damaged data has to carry a lower confidence for it.
"""

from __future__ import annotations

import unittest

import _support  # noqa: F401
import _scenarios as fixtures

from aadoctor import config, rules
from aadoctor.rules import builtin
from aadoctor.rules.facts import Facts


def facts_for(record: dict) -> Facts:
    return Facts.from_incident(record)


def run(record: dict, rule, thresholds=None):
    return rule(facts_for(record), thresholds or rules.Thresholds())


def evaluate(record: dict, thresholds=None):
    return rules.evaluate(facts_for(record), thresholds or rules.Thresholds())


def codes(findings):
    return [finding.code for finding in findings]


def find(findings, code):
    for finding in findings:
        if finding.code == code:
            return finding
    return None


def one_site(requests, total, paths=None, ips=None, statuses=None, kinds=None,
             seconds=300, coverage=None, **kwargs):
    """A window holding a single site, for the share boundaries."""
    record = fixtures.site(
        "site-a.com.br", requests, total, paths=paths, ips=ips,
        statuses=statuses, error_kinds=kinds,
    )
    filler = []
    if total > requests:
        filler = [fixtures.site("site-b.com.br", total - requests, total,
                                paths=[("/", total - requests)])]
    return fixtures.incident(
        peak=fixtures.one_window(
            fixtures.window(seconds, coverage=coverage, total_requests=total,
                            per_site=[record] + filler, statuses=statuses,
                            error_kinds=kinds, **kwargs)
        )
    )


class Scale(unittest.TestCase):
    """The confidence function itself, before any rule uses it."""

    def test_below_the_threshold_is_not_a_finding_at_all(self):
        self.assertEqual(rules.strength(0.49, 0.50, 0.75), 0.0)

    def test_exactly_on_the_threshold_is_the_weakest_a_finding_can_be(self):
        self.assertAlmostEqual(rules.strength(0.50, 0.50, 0.75), 0.60)
        self.assertEqual(rules.level(0.60), rules.MEDIUM)

    def test_at_and_above_the_ceiling_is_the_strongest(self):
        self.assertAlmostEqual(rules.strength(0.75, 0.50, 0.75), 0.98)
        self.assertAlmostEqual(rules.strength(9.0, 0.50, 0.75), 0.98)

    def test_strength_rises_with_the_observation(self):
        values = [rules.strength(share, 0.50, 0.75) for share in (0.5, 0.6, 0.7, 0.75)]
        self.assertEqual(values, sorted(values))

    def test_volume_discounts_a_share_resting_on_few_requests(self):
        self.assertEqual(rules.volume_factor(100, 100), 0.75)
        self.assertEqual(rules.volume_factor(1000, 100), 1.0)
        self.assertGreater(rules.volume_factor(500, 100), rules.volume_factor(200, 100))

    def test_levels_match_the_bands_in_the_spec(self):
        self.assertEqual(rules.level(0.49), rules.LOW)
        self.assertEqual(rules.level(0.50), rules.MEDIUM)
        self.assertEqual(rules.level(0.74), rules.MEDIUM)
        self.assertEqual(rules.level(0.75), rules.HIGH)
        self.assertEqual(rules.level(0.89), rules.HIGH)
        self.assertEqual(rules.level(0.90), rules.VERY_HIGH)

    def test_a_count_rule_scores_on_its_multiple_of_the_threshold(self):
        self.assertEqual(rules.count_confidence(5, 5), 0.60)
        self.assertEqual(rules.count_confidence(20, 5), 0.98)
        self.assertEqual(rules.count_confidence(4, 5), 0.0)


class Thresholds(unittest.TestCase):
    def test_defaults_come_from_the_configuration_schema(self):
        # One source, so a tuned server and the rules cannot disagree.
        limits = rules.Thresholds()
        self.assertEqual(limits.as_dict(), config.DEFAULTS["rules"])

    def test_every_threshold_a_rule_reads_exists(self):
        limits = rules.Thresholds()
        for name in (
            "min_volume", "site_share", "site_share_ceiling", "url_share",
            "url_share_ceiling", "ip_share", "ip_share_ceiling",
            "not_found_share", "not_found_min_rate", "http_5xx_share",
            "http_5xx_min_rate", "http_5xx_min_count", "upstream_timeout_min",
            "fastcgi_error_min", "php_error_min", "traffic_spike_factor",
        ):
            self.assertIsNotNone(getattr(limits, name), name)

    def test_a_configured_value_overrides_the_default(self):
        limits = rules.Thresholds({"site_share": 0.9})
        self.assertEqual(limits.site_share, 0.9)
        self.assertEqual(limits.url_share, config.DEFAULTS["rules"]["url_share"])

    def test_configuration_cannot_introduce_an_unknown_threshold(self):
        limits = rules.Thresholds({"nonsense": 1})
        self.assertNotIn("nonsense", limits.as_dict())

    def test_a_key_of_the_wrong_type_is_ignored_rather_than_crashing_a_rule(self):
        limits = rules.Thresholds({"site_share": "high"})
        self.assertEqual(limits.site_share, config.DEFAULTS["rules"]["site_share"])

    def test_from_config_reads_the_rules_section(self):
        limits = rules.Thresholds.from_config(config.Config({"rules": {"min_volume": 50}}))
        self.assertEqual(limits.min_volume, 50)

    def test_from_config_without_a_config_falls_back_to_defaults(self):
        self.assertEqual(rules.Thresholds.from_config(None).as_dict(),
                         config.DEFAULTS["rules"])

    def test_the_example_configuration_documents_every_threshold(self):
        # A value that produced a diagnosis has to be findable without reading
        # the source.
        from pathlib import Path

        example = Path(__file__).resolve().parents[1] / "config.example.toml"
        content = example.read_text(encoding="utf-8")
        for name in config.DEFAULTS["rules"]:
            self.assertIn(name, content, name)


class SiteDomination(unittest.TestCase):
    def test_fires_on_the_scenario_the_specification_names(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_site_dominating)

        self.assertEqual(finding.code, builtin.ONE_SITE_DOMINATING)
        self.assertEqual(finding.site, "site-a.com.br")
        self.assertEqual(finding.evidence["requests"], 8_000)
        self.assertEqual(finding.evidence["total_requests"], 9_500)
        self.assertAlmostEqual(finding.evidence["share"], 0.8421, places=3)
        self.assertEqual(finding.level, rules.VERY_HIGH)

    def test_just_below_the_threshold_does_not_fire(self):
        self.assertIsNone(run(one_site(699, 1_000), builtin.rule_one_site_dominating))

    def test_exactly_on_the_threshold_fires_at_its_weakest(self):
        finding = run(one_site(700, 1_000), builtin.rule_one_site_dominating)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.level, rules.MEDIUM)

    def test_just_above_the_threshold_fires(self):
        self.assertIsNotNone(run(one_site(701, 1_000), builtin.rule_one_site_dominating))

    def test_a_handful_of_requests_is_not_a_finding_however_lopsided(self):
        # Nine of ten is a 90% share and means nothing.
        result = run(one_site(9, 10), builtin.rule_one_site_dominating)
        self.assertIsInstance(result, rules.NotEvaluable)
        self.assertEqual(result.reason, "below_minimum_volume")

    def test_the_second_place_site_is_kept_for_contrast(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_site_dominating)
        self.assertEqual(finding.evidence["second_place"]["site"], "site-b.com.br")

    def test_two_sites_at_forty_five_percent_each_do_not_produce_a_dominant_one(self):
        total = 1_000
        per_site = [
            fixtures.site("a.com", 450, total, paths=[("/", 450)]),
            fixtures.site("b.com", 450, total, paths=[("/", 450)]),
            fixtures.site("c.com", 100, total, paths=[("/", 100)]),
        ]
        record = fixtures.incident(
            peak=fixtures.one_window(fixtures.window(300, total_requests=total, per_site=per_site))
        )
        self.assertIsNone(run(record, builtin.rule_one_site_dominating))

    def test_the_threshold_used_travels_with_the_finding(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_site_dominating)
        self.assertEqual(finding.evidence["threshold"], config.DEFAULTS["rules"]["site_share"])

    def test_an_unattributed_site_is_never_named_as_the_responsible_one(self):
        total = 1_000
        per_site = [fixtures.site("-", 900, total, paths=[("/", 900)]),
                    fixtures.site("real.com", 100, total, paths=[("/", 100)])]
        record = fixtures.incident(
            peak=fixtures.one_window(fixtures.window(300, total_requests=total, per_site=per_site))
        )
        finding = run(record, builtin.rule_one_site_dominating)
        self.assertNotEqual(getattr(finding, "site", None), "-")


class UrlDomination(unittest.TestCase):
    def test_fires_on_the_scenario_and_keeps_both_denominators(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_url_dominating)

        self.assertEqual(finding.path, "/wp-cron.php")
        self.assertEqual(finding.site, "site-a.com.br")
        self.assertEqual(finding.evidence["path_requests"], 5_000)
        self.assertEqual(finding.evidence["site_requests"], 8_000)
        self.assertAlmostEqual(finding.evidence["share"], 0.625)

    def test_the_share_is_against_the_site_not_the_server(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_url_dominating)
        # 5000/9500 would be 52.6%; the site figure is the meaningful one.
        self.assertNotAlmostEqual(finding.evidence["share"], 5_000 / 9_500, places=3)

    def test_boundary_at_the_threshold(self):
        below = one_site(1_000, 1_000, paths=[("/x", 499), ("/y", 400)])
        exact = one_site(1_000, 1_000, paths=[("/x", 500), ("/y", 400)])
        above = one_site(1_000, 1_000, paths=[("/x", 501), ("/y", 400)])

        self.assertIsNone(run(below, builtin.rule_one_url_dominating))
        self.assertIsNotNone(run(exact, builtin.rule_one_url_dominating))
        self.assertIsNotNone(run(above, builtin.rule_one_url_dominating))

    def test_a_site_below_the_minimum_volume_is_not_evaluated(self):
        result = run(one_site(50, 50, paths=[("/x", 50)]), builtin.rule_one_url_dominating)
        self.assertIsInstance(result, rules.NotEvaluable)
        self.assertEqual(result.reason, "below_minimum_volume")

    def test_a_site_with_no_path_data_says_so(self):
        result = run(one_site(1_000, 1_000), builtin.rule_one_url_dominating)
        self.assertIsInstance(result, rules.NotEvaluable)
        self.assertEqual(result.reason, "no_path_data")

    def test_a_popular_endpoint_name_is_not_itself_suspicious(self):
        # /wp-cron.php, /xmlrpc.php and admin-ajax.php are suspects only when
        # the numbers say so. There is no rule that knows what WordPress is,
        # and there must not be: the engine has to work for Laravel, for a
        # plain PHP site and for an API just the same.
        record = one_site(1_000, 1_000, paths=[
            ("/wp-cron.php", 250), ("/xmlrpc.php", 250),
            ("/wp-admin/admin-ajax.php", 250), ("/", 250),
        ])
        self.assertIsNone(run(record, builtin.rule_one_url_dominating))

    def test_the_same_endpoint_does_fire_once_it_dominates(self):
        record = one_site(1_000, 1_000, paths=[("/wp-cron.php", 700), ("/", 300)])
        finding = run(record, builtin.rule_one_url_dominating)
        self.assertEqual(finding.path, "/wp-cron.php")

    def test_the_evidence_says_the_query_string_was_not_counted(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_url_dominating)
        self.assertIn("query string", finding.evidence["normalized"])


class IpDomination(unittest.TestCase):
    def test_fires_on_the_scenario(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_ip_dominating)

        self.assertEqual(finding.ip, "185.1.2.3")
        self.assertEqual(finding.evidence["requests"], 4_620)
        self.assertAlmostEqual(finding.evidence["share_of_site"], 0.5775)

    def test_both_proportions_are_kept_when_both_exist(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_ip_dominating)
        self.assertIn("share_of_server", finding.evidence)
        self.assertLess(finding.evidence["share_of_server"],
                        finding.evidence["share_of_site"])

    def test_it_never_calls_concentration_an_attack(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_ip_dominating)
        text = (finding.description + str(finding.evidence)).lower()
        for word in ("attack", "attacker", "ddos", "block", "ban", "firewall"):
            self.assertNotIn(word, text)

    def test_the_proxy_and_nat_caveat_is_part_of_the_finding(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_one_ip_dominating)
        caveat = finding.evidence["caveat"].lower()
        self.assertIn("cdn", caveat)
        self.assertIn("nat", caveat)

    def test_boundary_at_the_threshold(self):
        below = one_site(1_000, 1_000, ips=[("1.1.1.1", 499), ("2.2.2.2", 400)],
                         paths=[("/", 1_000)])
        exact = one_site(1_000, 1_000, ips=[("1.1.1.1", 500), ("2.2.2.2", 400)],
                         paths=[("/", 1_000)])
        above = one_site(1_000, 1_000, ips=[("1.1.1.1", 501), ("2.2.2.2", 400)],
                         paths=[("/", 1_000)])

        self.assertIsNone(run(below, builtin.rule_one_ip_dominating))
        self.assertIsNotNone(run(exact, builtin.rule_one_ip_dominating))
        self.assertIsNotNone(run(above, builtin.rule_one_ip_dominating))

    def test_twelve_requests_from_one_address_is_not_a_finding(self):
        record = one_site(12, 12, ips=[("1.1.1.1", 10)], paths=[("/", 12)])
        result = run(record, builtin.rule_one_ip_dominating)
        self.assertIsInstance(result, rules.NotEvaluable)

    def test_missing_address_data_says_so(self):
        result = run(one_site(1_000, 1_000, paths=[("/", 1_000)]),
                     builtin.rule_one_ip_dominating)
        self.assertIsInstance(result, rules.NotEvaluable)
        self.assertEqual(result.reason, "no_address_data")


class NotFoundFlood(unittest.TestCase):
    def flood(self, count, total=5_000, seconds=300):
        return one_site(total, total, paths=[("/", total)],
                        statuses={"200": total - count, "404": count}, seconds=seconds)

    def test_a_real_flood_fires(self):
        finding = run(self.flood(3_000), builtin.rule_not_found_flood)
        self.assertEqual(finding.code, builtin.NOT_FOUND_FLOOD)
        self.assertEqual(finding.evidence["count"], 3_000)

    def test_both_a_share_and_a_rate_are_required(self):
        # 40% of a slow trickle - 120 missing pages over five minutes. The
        # share is there, the rate is not, and nothing here is a flood.
        record = one_site(300, 300, paths=[("/", 300)],
                          statuses={"200": 180, "404": 120}, seconds=300)
        self.assertIsNone(run(record, builtin.rule_not_found_flood))

    def test_a_high_rate_at_a_low_share_does_not_fire(self):
        record = one_site(100_000, 100_000, paths=[("/", 100_000)],
                          statuses={"200": 98_000, "404": 2_000}, seconds=300)
        self.assertIsNone(run(record, builtin.rule_not_found_flood))

    def test_three_of_five_requests_is_not_a_flood(self):
        record = one_site(5, 5, paths=[("/", 5)], statuses={"200": 2, "404": 3})
        result = run(record, builtin.rule_not_found_flood)
        self.assertIsInstance(result, rules.NotEvaluable)

    def test_it_can_fire_on_a_server_that_is_not_busy(self):
        """The two thresholds multiply, and that nearly disabled the rule.

        Requiring a share of S *and* a rate of R means the whole server has to
        be serving R/S requests a second before this can fire. At 0.30 and 5/s
        that was 16.7 req/s - more than most aaPanel servers ever see. The
        first production incident had 82% of 869 requests returning 404 during
        a load spike, at 2.9 req/s, and the rule stayed silent.

        This asserts the property rather than the number: whatever the
        thresholds become, the rule has to be reachable on a small server.
        """
        limits = rules.Thresholds()
        reachable = limits.not_found_min_rate / limits.not_found_share

        self.assertLess(reachable, 5.0, "the rule needs %.1f req/s to fire at all" % reachable)

    def test_the_real_incident_that_exposed_it_now_fires(self):
        # 869 requests over 300s, 713 of them 404 - the numbers as recorded.
        record = one_site(869, 869, paths=[("/", 869)],
                          statuses={"200": 140, "301": 16, "404": 713}, seconds=300)
        finding = run(record, builtin.rule_not_found_flood)

        self.assertIsNotNone(finding)
        self.assertEqual(finding.evidence["count"], 713)
        self.assertAlmostEqual(finding.evidence["share"], 0.8205, places=3)

    def test_a_handful_of_stale_links_on_a_quiet_site_still_does_not_fire(self):
        # 35% of a slow trickle is normal: old links, a missing favicon, a
        # bookmarked page that moved. Lowering the floor must not catch this.
        record = one_site(200, 200, paths=[("/", 200)],
                          statuses={"200": 130, "404": 70}, seconds=300)
        self.assertIsNone(run(record, builtin.rule_not_found_flood))

    def test_it_does_not_claim_to_know_which_paths_were_missing(self):
        # Statuses and paths are counted separately; the cross-product does not
        # exist, and inventing it would be the most convincing kind of wrong.
        finding = run(self.flood(3_000), builtin.rule_not_found_flood)
        self.assertIn("not available", finding.evidence["top_404_paths"])


class HttpErrors(unittest.TestCase):
    def test_fires_on_the_scenario_and_breaks_the_codes_out(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_http_5xx_spike)

        self.assertEqual(finding.evidence["count"], 120)
        self.assertEqual(finding.evidence["by_code"], {"502": 120})
        self.assertEqual(finding.site, "site-a.com.br")

    def test_two_server_errors_are_not_a_spike(self):
        record = one_site(1_000, 1_000, paths=[("/", 1_000)],
                          statuses={"200": 998, "500": 2})
        self.assertIsNone(run(record, builtin.rule_http_5xx_spike))

    def test_a_high_rate_fires_even_at_a_low_share(self):
        record = one_site(100_000, 100_000, paths=[("/", 100_000)],
                          statuses={"200": 99_500, "503": 500}, seconds=300)
        self.assertIsNotNone(run(record, builtin.rule_http_5xx_spike))

    def test_a_high_share_fires_even_at_a_low_rate(self):
        record = one_site(1_000, 1_000, paths=[("/", 1_000)],
                          statuses={"200": 900, "500": 100}, seconds=300)
        self.assertIsNotNone(run(record, builtin.rule_http_5xx_spike))

    def test_the_finding_is_attributed_to_the_site_carrying_the_failures(self):
        finding = run(fixtures.error_concentration_scenario(), builtin.rule_http_5xx_spike)
        self.assertEqual(finding.site, "site-b.com.br")

    def test_failures_spread_evenly_are_not_attributed_to_anyone(self):
        total = 3_000
        per_site = [
            fixtures.site("a.com", 1_000, total, paths=[("/", 1_000)],
                          statuses={"200": 900, "500": 100}),
            fixtures.site("b.com", 1_000, total, paths=[("/", 1_000)],
                          statuses={"200": 900, "500": 100}),
            fixtures.site("c.com", 1_000, total, paths=[("/", 1_000)],
                          statuses={"200": 900, "500": 100}),
        ]
        record = fixtures.incident(peak=fixtures.one_window(
            fixtures.window(300, total_requests=total, per_site=per_site,
                            statuses={"200": 2_700, "500": 300})))
        finding = run(record, builtin.rule_http_5xx_spike)
        self.assertIsNone(finding.site)

    def test_no_status_data_is_reported_rather_than_read_as_health(self):
        record = one_site(1_000, 1_000, paths=[("/", 1_000)])
        result = run(record, builtin.rule_http_5xx_spike)
        self.assertIsInstance(result, rules.NotEvaluable)
        self.assertEqual(result.reason, "no_status_data")


class ErrorLogRules(unittest.TestCase):
    def kinds(self, mapping, site_name="site-a.com.br"):
        total = 1_000
        record = fixtures.site(site_name, total, total, paths=[("/", total)],
                               statuses={"200": total}, error_kinds=mapping)
        return fixtures.incident(peak=fixtures.one_window(
            fixtures.window(300, total_requests=total, per_site=[record],
                            statuses={"200": total}, error_kinds=mapping)))

    def test_upstream_timeout_fires_on_the_scenario(self):
        finding = run(fixtures.dominant_site_scenario(), builtin.rule_upstream_timeout)
        self.assertEqual(finding.evidence["count"], 40)
        self.assertEqual(finding.site, "site-a.com.br")

    def test_upstream_timeout_boundary(self):
        self.assertIsNone(run(self.kinds({"upstream_timeout": 4}),
                              builtin.rule_upstream_timeout))
        self.assertIsNotNone(run(self.kinds({"upstream_timeout": 5}),
                                 builtin.rule_upstream_timeout))

    def test_gateway_timeouts_corroborate_the_error_log(self):
        finding = run(fixtures.error_concentration_scenario(), builtin.rule_upstream_timeout)
        self.assertEqual(finding.evidence["correlated_504"], 100)

    def test_fastcgi_covers_the_classifications_the_parser_produces(self):
        finding = run(
            self.kinds({"fastcgi_stderr": 3, "connect_failed": 2, "recv_failed": 1}),
            builtin.rule_fastcgi_error,
        )
        self.assertEqual(finding.evidence["count"], 6)
        self.assertEqual(sorted(finding.evidence["by_kind"]),
                         ["connect_failed", "fastcgi_stderr", "recv_failed"])

    def test_php_weighs_a_fatal_above_a_warning(self):
        fatal = run(self.kinds({"php_fatal": 10}), builtin.rule_php_error_spike)
        warnings = run(self.kinds({"php_warning": 10}), builtin.rule_php_error_spike)

        self.assertIsNotNone(fatal)
        self.assertIsNone(warnings)

    def test_php_fires_when_enough_warnings_accumulate(self):
        finding = run(self.kinds({"php_warning": 40}), builtin.rule_php_error_spike)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.evidence["minor"], 40)
        self.assertEqual(finding.evidence["severe"], 0)

    def test_php_keeps_the_classes_apart_in_its_evidence(self):
        finding = run(
            self.kinds({"php_fatal": 8, "php_memory_exhausted": 4, "php_warning": 12}),
            builtin.rule_php_error_spike,
        )
        self.assertEqual(finding.evidence["severe"], 12)
        self.assertEqual(finding.evidence["minor"], 12)
        self.assertEqual(finding.evidence["by_kind"]["php_memory_exhausted"], 4)

    def test_an_absent_error_log_is_not_read_as_an_absence_of_errors(self):
        record = one_site(1_000, 1_000, paths=[("/", 1_000)], statuses={"200": 1_000})
        for rule in (builtin.rule_upstream_timeout, builtin.rule_fastcgi_error,
                     builtin.rule_php_error_spike):
            result = run(record, rule)
            self.assertIsInstance(result, rules.NotEvaluable, rule.__name__)
            self.assertEqual(result.reason, "no_error_log_data")

    def test_a_readable_error_log_with_no_matching_events_simply_does_not_fire(self):
        record = self.kinds({"other": 30})
        self.assertIsNone(run(record, builtin.rule_upstream_timeout))

    def test_no_error_rule_suggests_restarting_anything(self):
        findings, _blocked, _quality = evaluate(
            self.kinds({"fastcgi_stderr": 40, "upstream_timeout": 40, "php_fatal": 40})
        )
        text = " ".join(f.description + str(f.evidence) for f in findings).lower()
        for word in ("restart", "reload", "systemctl", "kill", "you should"):
            self.assertNotIn(word, text)


class TrafficSpike(unittest.TestCase):
    def two_windows(self, recent, recent_seconds, long_total, long_seconds=300):
        short = fixtures.window(60, coverage=recent_seconds, total_requests=recent,
                                per_site=[], statuses={"200": recent})
        long = fixtures.window(300, coverage=long_seconds, total_requests=long_total,
                               per_site=[fixtures.site("a.com", long_total, long_total,
                                                       paths=[("/", long_total)])],
                               statuses={"200": long_total})
        return fixtures.incident(peak={"60": short, "300": long})

    def test_it_compares_rates_not_volumes(self):
        # 3,000 requests in the last minute against 3,600 in the four minutes
        # before it. Fewer requests happened recently, and it is still a spike,
        # because 50/s against 15/s is what the server actually felt. Comparing
        # totals would have called this a slowdown.
        finding = run(self.two_windows(3_000, 60.0, 6_600), builtin.rule_traffic_spike)

        self.assertIsNotNone(finding)
        self.assertLess(finding.evidence["recent_requests"],
                        finding.evidence["earlier_requests"])
        self.assertAlmostEqual(finding.evidence["recent_per_second"], 50.0, places=1)
        self.assertAlmostEqual(finding.evidence["earlier_per_second"], 15.0, places=1)
        self.assertAlmostEqual(finding.evidence["spike_ratio"], 3.33, places=1)

    def test_a_steady_rate_is_not_a_spike(self):
        self.assertIsNone(run(self.two_windows(1_000, 60.0, 5_000),
                              builtin.rule_traffic_spike))

    def test_a_genuine_spike_fires(self):
        finding = run(self.two_windows(4_000, 60.0, 5_000), builtin.rule_traffic_spike)
        self.assertIsNotNone(finding)
        self.assertGreater(finding.evidence["spike_ratio"], 3.0)

    def test_it_is_never_more_than_medium_because_there_is_no_baseline(self):
        finding = run(self.two_windows(50_000, 60.0, 50_100), builtin.rule_traffic_spike)
        self.assertLessEqual(finding.confidence, rules.CAP_MEDIUM)
        self.assertIn(finding.level, (rules.LOW, rules.MEDIUM))

    def test_traffic_from_nothing_is_named_rather_than_divided_by_zero(self):
        finding = run(self.two_windows(1_000, 60.0, 1_000), builtin.rule_traffic_spike)
        self.assertIsNone(finding.evidence["spike_ratio"])
        self.assertEqual(finding.evidence["comparison"], "from_idle")

    def test_too_little_previous_data_is_reported_as_not_evaluable(self):
        short = fixtures.window(60, coverage=40.0, total_requests=1_000)
        long = fixtures.window(300, coverage=45.0, total_requests=1_100)
        record = fixtures.incident(peak={"60": short, "300": long})

        result = run(record, builtin.rule_traffic_spike)
        self.assertIsInstance(result, rules.NotEvaluable)
        self.assertEqual(result.reason, "insufficient_previous_window")

    def test_one_window_alone_cannot_be_compared_against_itself(self):
        record = fixtures.incident(peak=fixtures.one_window(
            fixtures.window(300, total_requests=9_000)))
        result = run(record, builtin.rule_traffic_spike)
        self.assertIsInstance(result, rules.NotEvaluable)

    def test_the_evidence_says_what_the_comparison_actually_was(self):
        finding = run(self.two_windows(4_000, 60.0, 5_000), builtin.rule_traffic_spike)
        self.assertIn("not a historical baseline", finding.evidence["baseline"])


class Quality(unittest.TestCase):
    def test_a_short_window_lowers_confidence(self):
        full = fixtures.dominant_site_scenario()
        thin = fixtures.dominant_site_scenario(coverage=12.0)

        strong = find(evaluate(full)[0], builtin.ONE_SITE_DOMINATING)
        weak = find(evaluate(thin)[0], builtin.ONE_SITE_DOMINATING)

        self.assertGreater(strong.confidence, weak.confidence)
        self.assertLessEqual(weak.confidence, rules.CAP_MEDIUM)
        self.assertIn("low_window_coverage", weak.caps)

    def test_an_unparsed_access_log_lowers_the_traffic_findings(self):
        record = fixtures.dominant_site_scenario()
        record["traffic"]["peak"]["300"]["access_unparsed"] = 30_000

        findings, _blocked, _quality = evaluate(record)
        site_finding = find(findings, builtin.ONE_SITE_DOMINATING)

        self.assertLessEqual(site_finding.confidence, rules.CAP_MEDIUM)
        self.assertIn("access_log_mostly_unparsed", site_finding.caps)

    def test_an_unparsed_access_log_does_not_touch_the_error_log_findings(self):
        # 300 timeouts read cleanly are 300 timeouts, whatever the access log
        # looked like. Capping them would punish evidence that is not damaged.
        record = fixtures.dominant_site_scenario()
        record["traffic"]["peak"]["300"]["access_unparsed"] = 30_000

        findings, _blocked, _quality = evaluate(record)
        timeout = find(findings, builtin.UPSTREAM_TIMEOUT)

        self.assertEqual(timeout.caps, [])
        self.assertEqual(timeout.level, rules.VERY_HIGH)

    def test_pruned_paths_weaken_the_path_finding_and_nothing_else(self):
        # The path finding would be VERY HIGH on its own; it rests on a table
        # the cardinality cap pruned, so it is held back. The 40 timeouts were
        # read from the error log and are untouched by that.
        record = fixtures.dominant_site_scenario()
        window = record["traffic"]["peak"]["300"]
        window["per_site"][0]["paths"] = fixtures.ranked(
            [("/wp-cron.php", 7_600), ("/", 400)], 8_000
        )

        clean = find(evaluate(record)[0], builtin.ONE_URL_DOMINATING)
        window["other"] = {"paths": 4_000}
        findings, _blocked, _quality = evaluate(record)

        path = find(findings, builtin.ONE_URL_DOMINATING)
        timeout = find(findings, builtin.UPSTREAM_TIMEOUT)

        self.assertEqual(clean.level, rules.VERY_HIGH)
        self.assertLess(path.confidence, clean.confidence)
        self.assertIn("paths_truncated_by_cardinality_cap", path.caps)
        self.assertEqual(timeout.caps, [])
        self.assertEqual(timeout.level, rules.VERY_HIGH)

    def test_an_unparsed_error_log_lowers_only_the_error_findings(self):
        record = fixtures.dominant_site_scenario()
        record["traffic"]["peak"]["300"]["error_unparsed"] = 500

        findings, _blocked, _quality = evaluate(record)
        timeout = find(findings, builtin.UPSTREAM_TIMEOUT)
        site_finding = find(findings, builtin.ONE_SITE_DOMINATING)

        self.assertLessEqual(timeout.confidence, rules.CAP_MEDIUM)
        self.assertEqual(site_finding.caps, [])

    def test_the_reader_is_told_what_was_wrong_with_the_data(self):
        _findings, _blocked, quality = evaluate(fixtures.dominant_site_scenario(coverage=30.0))
        issues = " ".join(quality.issues())
        self.assertIn("30s of data", issues)


class Evaluation(unittest.TestCase):
    def test_the_scenario_produces_the_findings_the_specification_lists(self):
        findings, _blocked, _quality = evaluate(fixtures.dominant_site_scenario())

        for code in (builtin.ONE_SITE_DOMINATING, builtin.ONE_URL_DOMINATING,
                     builtin.ONE_IP_DOMINATING, builtin.HTTP_5XX_SPIKE,
                     builtin.UPSTREAM_TIMEOUT):
            self.assertIn(code, codes(findings))

    def test_a_quiet_distributed_server_produces_no_finding_at_all(self):
        findings, _blocked, _quality = evaluate(fixtures.distributed_scenario())
        self.assertEqual(findings, [])

    def test_findings_come_back_strongest_first(self):
        findings, _blocked, _quality = evaluate(fixtures.dominant_site_scenario())
        confidences = [finding.confidence for finding in findings]
        self.assertEqual(confidences, sorted(confidences, reverse=True))

    def test_the_same_incident_evaluates_identically_every_time(self):
        record = fixtures.dominant_site_scenario()
        first = [finding.as_dict() for finding in evaluate(record)[0]]
        second = [finding.as_dict() for finding in evaluate(record)[0]]
        self.assertEqual(first, second)

    def test_every_finding_carries_the_numbers_that_produced_it(self):
        findings, _blocked, _quality = evaluate(fixtures.dominant_site_scenario())
        for finding in findings:
            self.assertTrue(finding.evidence, finding.code)
            self.assertTrue(finding.description, finding.code)

    def test_every_rule_in_the_table_is_one_of_the_nine_in_scope(self):
        self.assertEqual(len(builtin.RULES), 9)
        self.assertEqual(len(builtin.CODES), 9)

    def test_an_empty_incident_produces_no_findings_and_explains_why(self):
        findings, blocked, _quality = evaluate(fixtures.incident())

        self.assertEqual(findings, [])
        self.assertEqual(len(blocked), 9)
        self.assertTrue(all(item.reason.startswith("no_") for item in blocked))

    def test_a_rule_that_could_not_run_is_distinguishable_from_one_that_found_nothing(self):
        _findings, blocked, _quality = evaluate(fixtures.distributed_scenario())
        # Errors could not be assessed at all; traffic concentration could, and
        # simply was not there.
        self.assertIn(builtin.UPSTREAM_TIMEOUT, [item.code for item in blocked])
        self.assertNotIn(builtin.ONE_SITE_DOMINATING, [item.code for item in blocked])

    def test_nothing_in_a_finding_is_phrased_as_an_instruction(self):
        findings, _blocked, _quality = evaluate(fixtures.dominant_site_scenario())
        text = " ".join(f.description for f in findings).lower()
        for word in ("should", "must", "run ", "disable", "block"):
            self.assertNotIn(word, text)


class Robustness(unittest.TestCase):
    """The incident file is input, and input is never trusted."""

    def test_a_record_that_is_not_a_dictionary_yields_empty_facts(self):
        for value in (None, [], "text", 7):
            facts = Facts.from_incident(value)
            self.assertFalse(facts.has_traffic)

    def test_missing_traffic_makes_every_rule_report_rather_than_raise(self):
        findings, blocked, _quality = evaluate({"id": "x", "system": {}})
        self.assertEqual(findings, [])
        self.assertEqual(len(blocked), 9)

    def test_rubbish_in_a_window_does_not_reach_a_rule_as_a_number(self):
        record = fixtures.incident(peak={"300": {
            "window_seconds": "soon",
            "total_requests": None,
            "sites": "not a list",
            "statuses": {"200": "many"},
            "per_site": [{"name": None}, "nonsense"],
        }})
        findings, _blocked, _quality = evaluate(record)
        self.assertEqual(findings, [])

    def test_the_peak_snapshot_is_preferred_over_the_opening_one(self):
        record = fixtures.dominant_site_scenario()
        record["traffic"]["start"] = fixtures.one_window(
            fixtures.window(300, total_requests=100,
                            per_site=[fixtures.site("other.com", 100, 100,
                                                    paths=[("/", 100)])]))
        facts = facts_for(record)
        self.assertEqual(facts.source, "peak")
        self.assertEqual(facts.total_requests, 9_500)

    def test_the_opening_snapshot_is_used_when_there_is_no_peak(self):
        record = fixtures.incident(
            start=fixtures.one_window(fixtures.window(300, total_requests=500)),
            peak=None,
        )
        self.assertEqual(facts_for(record).source, "start")

    def test_the_better_covered_window_is_the_one_read(self):
        # A 300s window holding 20s is weaker evidence than a full 60s one.
        record = fixtures.incident(peak={
            "60": fixtures.window(60, coverage=20.0, total_requests=400),
            "300": fixtures.window(300, coverage=20.0, total_requests=400),
        })
        self.assertEqual(facts_for(record).window_seconds, 60)

    def test_the_longer_window_wins_when_both_are_full(self):
        record = fixtures.incident(peak={
            "60": fixtures.window(60, total_requests=400),
            "300": fixtures.window(300, total_requests=9_000),
        })
        self.assertEqual(facts_for(record).window_seconds, 300)

    def test_windows_that_contradict_each_other_produce_no_baseline(self):
        record = fixtures.incident(peak={
            "60": fixtures.window(60, total_requests=9_000),
            "300": fixtures.window(300, total_requests=100),
        })
        self.assertEqual(facts_for(record).earlier_requests, 0)

    def test_no_rule_reads_a_file_or_reaches_the_network(self):
        # Checked against the import statements themselves rather than the
        # text, so that a docstring mentioning the PHP-FPM socket does not
        # read as a socket being opened.
        import ast
        import aadoctor.rules.builtin as module
        import aadoctor.rules.facts as facts_module

        allowed = {"typing", "dataclasses", "__future__"}
        for target in (module, facts_module):
            with open(target.__file__, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed, target.__name__)
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # relative: inside aaDoctor itself
                        continue
                    self.assertIn((node.module or "").split(".")[0], allowed, target.__name__)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
