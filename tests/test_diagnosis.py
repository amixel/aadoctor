"""SPEC-007 - correlation, and the answer it produces.

Three of these matter more than the rest, and they are the specification's own
scenarios: a dominant site with a path, an address and failures; high load with
nothing in the logs to blame; and a site that is only a third of the traffic
but holds nearly all the failures. Getting the second one right - answering
"no clear cause" instead of inventing one - is what makes the first one worth
believing.
"""

from __future__ import annotations

import json
import unittest

import _support  # noqa: F401
import _scenarios as fixtures

from aadoctor import rules
from aadoctor.analyzers import diagnosis
from aadoctor.rules import builtin


def codes(findings):
    return [finding.code for finding in findings]


class MainScenario(unittest.TestCase):
    """One site, one path, one address, and failures (SPEC-007 section 99)."""

    def setUp(self):
        self.result = diagnosis.diagnose(fixtures.dominant_site_scenario())

    def test_it_names_the_site_the_path_and_the_address(self):
        self.assertTrue(self.result.conclusive)
        self.assertEqual(self.result.primary_site, "site-a.com.br")
        self.assertEqual(self.result.primary_path, "/wp-cron.php")
        self.assertEqual(self.result.associated_ip, "185.1.2.3")

    def test_the_confidence_is_strong(self):
        self.assertEqual(self.result.level, rules.VERY_HIGH)

    def test_all_five_expected_findings_are_present(self):
        for code in (builtin.ONE_SITE_DOMINATING, builtin.ONE_URL_DOMINATING,
                     builtin.ONE_IP_DOMINATING, builtin.HTTP_5XX_SPIKE,
                     builtin.UPSTREAM_TIMEOUT):
            self.assertIn(code, codes(self.result.findings))

    def test_the_numbers_the_specification_asks_for_are_in_the_evidence(self):
        evidence = " ".join(self.result.evidence())
        self.assertIn("84.2%", evidence)
        self.assertIn("62.5%", evidence)
        self.assertIn("57.8%", evidence)
        self.assertIn("120", evidence)
        self.assertIn("40 upstream timeout", evidence)

    def test_it_does_not_claim_the_address_called_the_path(self):
        # Aggregation holds site -> paths and site -> ips, never site x ip x
        # path. Saying that 185.1.2.3 is what called /wp-cron.php would be
        # inventing the one correlation the data does not contain.
        text = json.dumps(self.result.as_dict())
        self.assertNotIn("ip_paths", text)
        for finding in self.result.findings:
            if finding.ip:
                self.assertIsNone(finding.path)

    def test_the_wording_stays_short_of_proof(self):
        text = (self.result.summary + " ".join(self.result.evidence())).lower()
        for word in ("caused by", "root cause", "definitive", "guaranteed", "proves"):
            self.assertNotIn(word, text)
        self.assertIn("evidence points to", self.result.summary.lower())


class DistributedScenario(unittest.TestCase):
    """High load, ten sites, nothing concentrated (SPEC-007 section 100)."""

    def setUp(self):
        self.result = diagnosis.diagnose(fixtures.distributed_scenario())

    def test_it_reaches_no_conclusion(self):
        self.assertFalse(self.result.conclusive)
        self.assertIsNone(self.result.primary_site)
        self.assertIsNone(self.result.primary_path)
        self.assertIsNone(self.result.associated_ip)

    def test_it_says_so_plainly(self):
        self.assertIn("No clear log-based cause identified", self.result.summary)

    def test_it_explains_that_a_cause_can_still_exist_outside_the_logs(self):
        self.assertIn("monitored Nginx and PHP logs", self.result.summary)

    def test_no_confidence_is_presented_at_all(self):
        self.assertEqual(self.result.confidence, 0.0)

    def test_it_invents_no_finding_to_fill_the_gap(self):
        self.assertEqual(self.result.findings, [])


class OneQuietSiteWithOneEndpoint(unittest.TestCase):
    """Ten even sites, one of which serves a single endpoint.

    The path finding is true and fires. It still must not produce a suspect:
    a share of a site says nothing about that site's weight on the server, and
    a 950-request site is not what made a 9,500-request server slow. This case
    came out of the container run, not out of a whiteboard.
    """

    def setUp(self):
        total = 9_500
        per_site = [
            fixtures.site("single.com.br", 950, total, paths=[("/api/poll", 950)],
                          ips=[("10.0.0.1", 300), ("10.0.0.2", 300), ("10.0.0.3", 350)],
                          statuses={"200": 950})
        ]
        for index in range(1, 10):
            per_site.append(
                fixtures.site(f"site-{index}.com.br", 950, total,
                              paths=[("/", 300), ("/produto", 250), ("/blog", 400)],
                              ips=[(f"10.0.{index}.1", 500), (f"10.0.{index}.2", 450)],
                              statuses={"200": 950})
            )
        self.result = diagnosis.diagnose(fixtures.incident(
            peak=fixtures.one_window(fixtures.window(
                300, total_requests=total, per_site=per_site,
                statuses={"200": total}))))

    def test_the_path_finding_is_reported(self):
        self.assertIn(builtin.ONE_URL_DOMINATING, codes(self.result.findings))

    def test_but_no_site_is_named(self):
        self.assertFalse(self.result.conclusive)
        self.assertIsNone(self.result.primary_site)
        self.assertEqual(self.result.confidence, 0.0)

    def test_and_the_reason_is_explained(self):
        self.assertIn("No clear log-based cause identified", self.result.summary)
        self.assertIn("single.com.br", self.result.summary)
        self.assertIn("not a large enough share of the server", self.result.summary)

    def test_one_failing_endpoint_would_be_enough_to_change_the_answer(self):
        # The same shape, plus errors on that site: now something ties it to
        # the server, and the diagnosis is allowed to name it.
        record = fixtures.incident(peak=fixtures.one_window(fixtures.window(
            300,
            total_requests=9_500,
            per_site=[fixtures.site("single.com.br", 950, 9_500,
                                    paths=[("/api/poll", 950)],
                                    statuses={"200": 950},
                                    error_kinds={"upstream_timeout": 200})],
            statuses={"200": 9_500},
            error_kinds={"upstream_timeout": 200})))

        result = diagnosis.diagnose(record)
        self.assertTrue(result.conclusive)
        self.assertEqual(result.primary_site, "single.com.br")


class ErrorConcentrationScenario(unittest.TestCase):
    """A site that is not dominant but is failing (SPEC-007 section 101)."""

    def setUp(self):
        self.result = diagnosis.diagnose(fixtures.error_concentration_scenario())

    def test_the_failing_site_wins_without_dominating_the_traffic(self):
        self.assertTrue(self.result.conclusive)
        self.assertEqual(self.result.primary_site, "site-b.com.br")

    def test_no_site_domination_finding_was_needed_to_get_there(self):
        self.assertNotIn(builtin.ONE_SITE_DOMINATING, codes(self.result.findings))

    def test_the_failures_are_what_carried_it(self):
        self.assertIn(builtin.UPSTREAM_TIMEOUT, codes(self.result.findings))
        self.assertIn(builtin.HTTP_5XX_SPIKE, codes(self.result.findings))

    def test_the_confidence_is_solid_without_being_maximal(self):
        self.assertIn(self.result.level, (rules.HIGH, rules.VERY_HIGH))

    def test_no_path_is_claimed_because_none_dominates(self):
        self.assertIsNone(self.result.primary_path)


class Scoring(unittest.TestCase):
    def test_a_stronger_finding_is_worth_more_than_a_weaker_one(self):
        strong = rules.Finding(code=builtin.ONE_SITE_DOMINATING, confidence=0.95)
        weak = rules.Finding(code=builtin.ONE_SITE_DOMINATING, confidence=0.51)

        self.assertGreater(diagnosis.points_for(strong), diagnosis.points_for(weak))

    def test_a_site_at_ninety_percent_outweighs_one_barely_over_the_line(self):
        heavy = diagnosis.diagnose(self.site_at(0.94))
        marginal = diagnosis.diagnose(self.site_at(0.71))
        self.assertGreater(heavy.score, marginal.score)

    def site_at(self, share):
        total = 10_000
        requests = int(total * share)
        per_site = [
            fixtures.site("a.com", requests, total, paths=[("/", requests)],
                          ips=[("1.1.1.1", requests // 4)], statuses={"200": requests}),
            fixtures.site("b.com", total - requests, total,
                          paths=[("/", total - requests)],
                          statuses={"200": total - requests}),
        ]
        return fixtures.incident(peak=fixtures.one_window(
            fixtures.window(300, total_requests=total, per_site=per_site,
                            statuses={"200": total})))

    def test_every_finding_in_scope_has_a_declared_weight(self):
        for code in builtin.CODES:
            self.assertIn(code, diagnosis.WEIGHTS, code)

    def test_the_weights_are_not_multiplied_into_a_fake_probability(self):
        # Points are summed. Nothing here multiplies two confidences together.
        result = diagnosis.diagnose(fixtures.dominant_site_scenario())
        self.assertGreater(result.score, 1.0)
        self.assertLessEqual(result.confidence, 1.0)

    def test_the_output_says_confidence_is_not_a_probability(self):
        payload = diagnosis.diagnose(fixtures.dominant_site_scenario()).as_dict()
        self.assertIn("not a probability", payload["confidence"]["note"])

    def test_the_score_of_each_site_is_shown_for_auditing(self):
        payload = diagnosis.diagnose(fixtures.dominant_site_scenario()).as_dict()
        self.assertIn("site-a.com.br", payload["diagnosis"]["site_scores"])


class Conflict(unittest.TestCase):
    """Findings that disagree must not produce a confident answer."""

    def conflicting(self):
        total = 10_000
        per_site = [
            fixtures.site("busy.com", 7_600, total,
                          paths=[("/", 4_000), ("/x", 3_600)],
                          ips=[("1.1.1.1", 2_000)], statuses={"200": 7_600}),
            fixtures.site("broken.com", 2_400, total, paths=[("/", 2_400)],
                          ips=[("2.2.2.2", 1_200)],
                          statuses={"200": 2_000, "502": 400},
                          error_kinds={"upstream_timeout": 300, "php_fatal": 60}),
        ]
        return fixtures.incident(peak=fixtures.one_window(
            fixtures.window(300, total_requests=total, per_site=per_site,
                            statuses={"200": 9_600, "502": 400},
                            error_kinds={"upstream_timeout": 300, "php_fatal": 60})))

    def test_the_traffic_suspect_and_the_failing_site_are_both_reported(self):
        # Whichever of the two wins, the other is named rather than dropped.
        result = diagnosis.diagnose(self.conflicting())
        named = {result.primary_site, result.error_site, result.traffic_site}
        self.assertIn("busy.com", named)
        self.assertIn("broken.com", named)

    def test_disagreement_lowers_the_confidence(self):
        conflicted = diagnosis.diagnose(self.conflicting())
        agreed = diagnosis.diagnose(fixtures.dominant_site_scenario())
        self.assertLess(conflicted.confidence, agreed.confidence)

    def test_the_score_is_net_of_what_points_elsewhere(self):
        result = diagnosis.diagnose(self.conflicting())
        best = max(result.scores.values())
        self.assertLess(result.score, best)


class Quality(unittest.TestCase):
    def test_a_twelve_second_window_cannot_produce_the_highest_confidence(self):
        full = diagnosis.diagnose(fixtures.dominant_site_scenario())
        thin = diagnosis.diagnose(fixtures.dominant_site_scenario(coverage=12.0))

        self.assertEqual(full.level, rules.VERY_HIGH)
        self.assertNotEqual(thin.level, rules.VERY_HIGH)
        # The cap did its work inside the findings, so the diagnosis inherits a
        # lower score rather than being capped a second time.
        self.assertLess(thin.score, full.score)
        for finding in thin.findings:
            self.assertIn("low_window_coverage", finding.caps)

    def test_the_short_window_is_stated_and_not_hidden(self):
        thin = diagnosis.diagnose(fixtures.dominant_site_scenario(coverage=12.0))
        self.assertIn("12s of data", " ".join(thin.quality.issues()))

    def test_a_badly_parsed_access_log_is_reported_to_the_reader(self):
        record = fixtures.dominant_site_scenario()
        record["traffic"]["peak"]["300"]["access_unparsed"] = 22_000

        result = diagnosis.diagnose(record)
        issues = " ".join(result.quality.issues())
        self.assertIn("did not parse", issues)
        self.assertIn("incomplete", issues)

    def test_a_badly_parsed_access_log_weakens_the_traffic_evidence(self):
        record = fixtures.dominant_site_scenario()
        clean = diagnosis.diagnose(record)
        record["traffic"]["peak"]["300"]["access_unparsed"] = 22_000
        dirty = diagnosis.diagnose(record)

        self.assertLess(dirty.score, clean.score)

    def test_the_window_actually_used_is_reported(self):
        payload = diagnosis.diagnose(fixtures.dominant_site_scenario()).as_dict()
        self.assertEqual(payload["window"]["source"], "peak")
        self.assertEqual(payload["window"]["window_seconds"], 300)
        self.assertEqual(payload["window"]["total_requests"], 9_500)


class NotEvaluable(unittest.TestCase):
    def test_rules_that_could_not_run_are_listed_separately(self):
        payload = diagnosis.diagnose(fixtures.distributed_scenario()).as_dict()

        self.assertTrue(payload["not_evaluable"])
        reported = {item["code"] for item in payload["not_evaluable"]}
        self.assertIn(builtin.UPSTREAM_TIMEOUT, reported)

    def test_each_one_carries_a_reason(self):
        payload = diagnosis.diagnose(fixtures.distributed_scenario()).as_dict()
        for item in payload["not_evaluable"]:
            self.assertTrue(item["reason"])

    def test_a_rule_that_could_not_run_is_never_listed_as_a_finding(self):
        result = diagnosis.diagnose(fixtures.distributed_scenario())
        blocked = {item.code for item in result.not_evaluable}
        self.assertFalse(blocked & set(codes(result.findings)))


class Serialization(unittest.TestCase):
    def setUp(self):
        self.payload = diagnosis.diagnose(fixtures.dominant_site_scenario()).as_dict()

    def test_the_whole_diagnosis_is_json(self):
        json.dumps(self.payload)

    def test_the_document_carries_every_section_the_spec_asks_for(self):
        for key in ("incident", "diagnosis", "findings", "not_evaluable",
                    "evidence", "confidence", "data_quality", "thresholds"):
            self.assertIn(key, self.payload)

    def test_the_ruleset_version_is_recorded(self):
        # Findings are computed on demand, so an old incident is read with
        # today's rules. Which those were has to be visible.
        self.assertEqual(self.payload["ruleset_version"], rules.RULESET_VERSION)

    def test_the_thresholds_actually_used_are_recorded(self):
        self.assertEqual(self.payload["thresholds"]["site_share"], 0.70)

    def test_every_finding_carries_its_own_threshold(self):
        for finding in self.payload["findings"]:
            self.assertTrue(
                any("threshold" in key for key in finding["evidence"]),
                finding["code"],
            )

    def test_a_configured_threshold_reaches_the_rules_and_the_output(self):
        limits = rules.Thresholds({"site_share": 0.95})
        result = diagnosis.diagnose(fixtures.dominant_site_scenario(), limits)

        self.assertNotIn(builtin.ONE_SITE_DOMINATING, codes(result.findings))
        self.assertEqual(result.as_dict()["thresholds"]["site_share"], 0.95)


class Determinism(unittest.TestCase):
    def test_the_same_incident_produces_byte_identical_output(self):
        record = fixtures.dominant_site_scenario()
        first = json.dumps(diagnosis.diagnose(record).as_dict(), sort_keys=True)
        second = json.dumps(diagnosis.diagnose(record).as_dict(), sort_keys=True)
        self.assertEqual(first, second)

    def test_the_order_of_the_sites_in_the_file_does_not_change_the_answer(self):
        record = fixtures.dominant_site_scenario()
        first = diagnosis.diagnose(record)

        window = record["traffic"]["peak"]["300"]
        window["per_site"] = list(reversed(window["per_site"]))
        window["sites"] = list(reversed(window["sites"]))
        second = diagnosis.diagnose(record)

        self.assertEqual(first.as_dict(), second.as_dict())

    def test_an_exact_tie_is_broken_the_same_way_every_time(self):
        total = 2_000
        per_site = [
            fixtures.site("a.com", 1_000, total, paths=[("/", 1_000)],
                          statuses={"200": 900, "502": 100},
                          error_kinds={"upstream_timeout": 50}),
            fixtures.site("b.com", 1_000, total, paths=[("/", 1_000)],
                          statuses={"200": 900, "502": 100},
                          error_kinds={"upstream_timeout": 50}),
        ]
        record = fixtures.incident(peak=fixtures.one_window(
            fixtures.window(300, total_requests=total, per_site=per_site,
                            statuses={"200": 1_800, "502": 200},
                            error_kinds={"upstream_timeout": 100})))

        answers = {diagnosis.diagnose(record).primary_site for _ in range(5)}
        self.assertEqual(len(answers), 1)


class Safety(unittest.TestCase):
    def test_diagnosing_does_not_change_the_incident(self):
        record = fixtures.dominant_site_scenario()
        before = json.dumps(record, sort_keys=True)
        diagnosis.diagnose(record)
        self.assertEqual(json.dumps(record, sort_keys=True), before)

    def test_nothing_in_the_output_tells_the_administrator_to_act(self):
        for scenario in (fixtures.dominant_site_scenario(),
                         fixtures.error_concentration_scenario()):
            result = diagnosis.diagnose(scenario)
            text = json.dumps(result.as_dict()).lower()
            for word in ("restart ", "reload ", "systemctl", "iptables",
                         "you should", "block this", "deny "):
                self.assertNotIn(word, text)

    def test_the_analyzer_imports_nothing_that_could_reach_outside(self):
        import ast

        with open(diagnosis.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        allowed = {"typing", "dataclasses", "__future__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                self.assertIn((node.module or "").split(".")[0], allowed)

    def test_an_open_incident_can_still_be_diagnosed(self):
        record = fixtures.dominant_site_scenario()
        record["status"] = "open"
        record["ended_at"] = None

        result = diagnosis.diagnose(record)
        self.assertEqual(result.status, "open")
        self.assertTrue(result.conclusive)

    def test_an_incident_with_no_traffic_says_the_logs_cannot_answer(self):
        result = diagnosis.diagnose(fixtures.incident())
        self.assertFalse(result.conclusive)
        self.assertIn("No traffic was recorded", result.summary)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
