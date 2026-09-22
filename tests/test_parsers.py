"""Nginx log parsing (SPEC-004).

Lines are inline strings: a parser that reads a file would already be wrong.
The fixtures here are synthetic - written from the Nginx and PHP formats, not
captured from a real aaPanel server.
"""

from __future__ import annotations

import re
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import _support  # noqa: F401  (sys.path bootstrap)

from aadoctor import parsers
from aadoctor.collectors.logs import ACCESS, ERROR, LogEvent
from aadoctor.parsers import nginx_access, nginx_error
from aadoctor.parsers.nginx_access import parse_access_line
from aadoctor.parsers.nginx_error import parse_error_line

COMBINED = (
    '177.10.20.30 - - [22/Sep/2026:16:20:31 -0300] '
    '"GET /produto/42?ref=home HTTP/1.1" 200 4812 '
    '"https://example.com/" "Mozilla/5.0"'
)

COMMON = (
    '177.10.20.30 - - [22/Sep/2026:16:20:31 -0300] '
    '"GET /produto/42 HTTP/1.1" 200 4812'
)

# Nginx's own default `main` format, which aaPanel uses: combined plus
# "$http_x_forwarded_for".
MAIN = COMBINED + ' "10.0.0.1"'

UPSTREAM_TIMEOUT = (
    '2026/09/22 16:42:12 [error] 1234#1234: *928 upstream timed out '
    "(110: Connection timed out) while reading response header from upstream, "
    'client: 177.10.20.30, server: example.com, '
    'request: "GET /checkout HTTP/1.1", '
    'upstream: "fastcgi://unix:/tmp/php.sock:", host: "example.com"'
)


class AccessCombined(unittest.TestCase):
    def test_every_field_of_a_combined_line(self):
        event = parse_access_line(COMBINED)

        self.assertEqual(event.remote_addr, "177.10.20.30")
        self.assertIsNone(event.remote_user)
        self.assertEqual(event.method, "GET")
        self.assertEqual(event.request_target, "/produto/42?ref=home")
        self.assertEqual(event.path, "/produto/42")
        self.assertEqual(event.query_string, "ref=home")
        self.assertEqual(event.protocol, "HTTP/1.1")
        self.assertEqual(event.status, 200)
        self.assertEqual(event.body_bytes_sent, 4812)
        self.assertEqual(event.referer, "https://example.com/")
        self.assertEqual(event.user_agent, "Mozilla/5.0")
        self.assertIsNone(event.extra)

    def test_the_timestamp_keeps_its_offset(self):
        event = parse_access_line(COMBINED)

        self.assertEqual(
            event.timestamp,
            datetime(2026, 9, 22, 16, 20, 31, tzinfo=timezone(timedelta(hours=-3))),
        )

    def test_the_common_format_parses_without_the_extra_fields(self):
        event = parse_access_line(COMMON)

        self.assertEqual(event.status, 200)
        self.assertEqual(event.path, "/produto/42")
        self.assertIsNone(event.referer)
        self.assertIsNone(event.user_agent)

    def test_nginx_default_main_format_with_a_trailing_field(self):
        """The shape a real aaPanel server writes; it must not be unparsed."""
        event = parse_access_line(MAIN)

        self.assertEqual(event.status, 200)
        self.assertEqual(event.user_agent, "Mozilla/5.0")
        # Kept verbatim, and deliberately not called the client address.
        self.assertEqual(event.extra, '"10.0.0.1"')
        self.assertEqual(event.remote_addr, "177.10.20.30")

    def test_a_remote_user_is_kept(self):
        line = COMBINED.replace("177.10.20.30 - -", "177.10.20.30 - admin")
        event = parse_access_line(line)

        self.assertEqual(event.remote_user, "admin")


class AccessAddresses(unittest.TestCase):
    def test_ipv4(self):
        self.assertEqual(parse_access_line(COMBINED).remote_addr, "177.10.20.30")

    def test_ipv6(self):
        line = COMBINED.replace("177.10.20.30", "2001:db8::1", 1)
        self.assertEqual(parse_access_line(line).remote_addr, "2001:db8::1")

    def test_a_full_length_ipv6_address(self):
        address = "2001:0db8:85a3:0000:0000:8a2e:0370:7334"
        line = COMBINED.replace("177.10.20.30", address, 1)

        self.assertEqual(parse_access_line(line).remote_addr, address)

    def test_a_unix_socket_or_hostname_is_taken_as_written(self):
        line = COMBINED.replace("177.10.20.30", "unix:", 1)
        self.assertEqual(parse_access_line(line).remote_addr, "unix:")


class AccessMethods(unittest.TestCase):
    def test_methods(self):
        for method in ("GET", "POST", "HEAD", "PUT", "DELETE", "OPTIONS", "PATCH"):
            line = COMBINED.replace('"GET ', f'"{method} ')
            self.assertEqual(parse_access_line(line).method, method, method)

    def test_a_post_with_a_body_size(self):
        line = COMBINED.replace('"GET ', '"POST ').replace(" 200 4812", " 201 0")
        event = parse_access_line(line)

        self.assertEqual(event.method, "POST")
        self.assertEqual(event.status, 201)
        self.assertEqual(event.body_bytes_sent, 0)


class AccessStatus(unittest.TestCase):
    def test_statuses_are_integers(self):
        for status in (200, 301, 404, 499, 500, 502, 504):
            line = COMBINED.replace(" 200 4812", f" {status} 4812")
            self.assertEqual(parse_access_line(line).status, status, status)

    def test_a_non_numeric_status_makes_the_line_unparsed(self):
        line = COMBINED.replace(" 200 4812", " abc 4812")

        self.assertIsNone(parse_access_line(line))

    def test_a_two_digit_status_makes_the_line_unparsed(self):
        line = COMBINED.replace(" 200 4812", " 20 4812")

        self.assertIsNone(parse_access_line(line))


class AccessOptionalFields(unittest.TestCase):
    def test_a_dash_body_size_is_absent_not_zero(self):
        line = COMBINED.replace(" 200 4812", " 200 -")
        event = parse_access_line(line)

        self.assertIsNone(event.body_bytes_sent)

    def test_a_dash_referer_is_absent(self):
        line = COMBINED.replace('"https://example.com/"', '"-"')

        self.assertIsNone(parse_access_line(line).referer)

    def test_a_dash_user_agent_is_absent(self):
        line = COMBINED.replace('"Mozilla/5.0"', '"-"')

        self.assertIsNone(parse_access_line(line).user_agent)

    def test_an_empty_user_agent_is_absent(self):
        line = COMBINED.replace('"Mozilla/5.0"', '""')

        self.assertIsNone(parse_access_line(line).user_agent)

    def test_a_dash_request_leaves_the_request_fields_empty(self):
        line = COMBINED.replace('"GET /produto/42?ref=home HTTP/1.1"', '"-"')
        event = parse_access_line(line)

        self.assertIsNone(event.method)
        self.assertIsNone(event.path)
        self.assertIsNone(event.request_target)
        # The rest of the line is still usable.
        self.assertEqual(event.status, 200)
        self.assertEqual(event.remote_addr, "177.10.20.30")


class AccessRequestTarget(unittest.TestCase):
    def test_a_path_without_a_query(self):
        event = parse_access_line(COMMON)

        self.assertEqual(event.path, "/produto/42")
        self.assertIsNone(event.query_string)

    def test_the_query_is_kept_exactly_as_it_arrived(self):
        line = COMBINED.replace(
            "/produto/42?ref=home", "/p?b=2&a=1&utm_source=X&e=%20%C3%A9"
        )
        event = parse_access_line(line)

        self.assertEqual(event.path, "/p")
        # No sorting, no decoding, no dropping of tracking parameters.
        self.assertEqual(event.query_string, "b=2&a=1&utm_source=X&e=%20%C3%A9")

    def test_an_empty_query_after_a_question_mark(self):
        line = COMBINED.replace("/produto/42?ref=home", "/produto/42?")
        event = parse_access_line(line)

        self.assertEqual(event.path, "/produto/42")
        self.assertIsNone(event.query_string)

    def test_only_the_first_question_mark_splits(self):
        line = COMBINED.replace("/produto/42?ref=home", "/p?a=1?b=2")
        event = parse_access_line(line)

        self.assertEqual(event.path, "/p")
        self.assertEqual(event.query_string, "a=1?b=2")

    def test_an_absolute_form_target(self):
        line = COMBINED.replace(
            "/produto/42?ref=home", "http://example.com/foo?a=1"
        )
        event = parse_access_line(line)

        self.assertEqual(event.path, "/foo")
        self.assertEqual(event.query_string, "a=1")
        self.assertEqual(event.request_target, "http://example.com/foo?a=1")

    def test_a_request_without_a_protocol(self):
        line = COMBINED.replace('"GET /produto/42?ref=home HTTP/1.1"', '"GET /foo"')
        event = parse_access_line(line)

        self.assertEqual(event.method, "GET")
        self.assertEqual(event.path, "/foo")
        self.assertIsNone(event.protocol)

    def test_a_request_with_only_a_target(self):
        line = COMBINED.replace('"GET /produto/42?ref=home HTTP/1.1"', '"/foo"')
        event = parse_access_line(line)

        self.assertIsNone(event.method)
        self.assertEqual(event.path, "/foo")

    def test_an_unencoded_space_in_the_target(self):
        line = COMBINED.replace(
            '"GET /produto/42?ref=home HTTP/1.1"', '"GET /a b/c HTTP/1.1"'
        )
        event = parse_access_line(line)

        self.assertEqual(event.method, "GET")
        self.assertEqual(event.request_target, "/a b/c")
        self.assertEqual(event.protocol, "HTTP/1.1")


class AccessTimestamps(unittest.TestCase):
    def test_a_positive_offset(self):
        line = COMBINED.replace("-0300", "+0530")
        event = parse_access_line(line)

        self.assertEqual(event.timestamp.utcoffset(), timedelta(hours=5, minutes=30))

    def test_utc(self):
        line = COMBINED.replace("-0300", "+0000")

        self.assertEqual(parse_access_line(line).timestamp.utcoffset(), timedelta(0))

    def test_a_malformed_timestamp_leaves_the_rest_of_the_line_usable(self):
        line = COMBINED.replace("22/Sep/2026:16:20:31 -0300", "not a timestamp")
        event = parse_access_line(line)

        self.assertIsNone(event.timestamp)
        self.assertEqual(event.status, 200)
        self.assertEqual(event.path, "/produto/42")

    def test_an_impossible_date_is_not_invented(self):
        line = COMBINED.replace("22/Sep/2026", "31/Feb/2026")

        self.assertIsNone(parse_access_line(line).timestamp)

    def test_an_unknown_month_name(self):
        line = COMBINED.replace("/Sep/", "/Xyz/")

        self.assertIsNone(parse_access_line(line).timestamp)

    def test_month_names_do_not_depend_on_the_process_locale(self):
        """Nginx writes English months whatever the server's locale is."""
        self.assertIsNotNone(nginx_access.parse_time_local("22/Sep/2026:16:20:31 -0300"))
        self.assertIsNotNone(nginx_access.parse_time_local("01/Dec/2026:00:00:00 +0000"))


class AccessMalformed(unittest.TestCase):
    def test_an_empty_line(self):
        self.assertIsNone(parse_access_line(""))

    def test_a_truncated_line(self):
        self.assertIsNone(parse_access_line("177.10.20.30 - - [22/Sep/2026"))

    def test_an_unrelated_line(self):
        self.assertIsNone(parse_access_line("this is not an access log line"))

    def test_a_json_log_format_is_unparsed_not_guessed_at(self):
        line = '{"remote_addr":"1.2.3.4","status":200,"request":"GET / HTTP/1.1"}'

        self.assertIsNone(parse_access_line(line))

    def test_an_unknown_field_order_is_unparsed(self):
        line = '200 177.10.20.30 "GET / HTTP/1.1" [22/Sep/2026:16:20:31 -0300]'

        self.assertIsNone(parse_access_line(line))

    def test_the_replacement_character_does_not_stop_parsing(self):
        line = COMBINED.replace("Mozilla/5.0", "Mozilla/5.0 ��")
        event = parse_access_line(line)

        self.assertIn("�", event.user_agent)
        self.assertEqual(event.status, 200)

    def test_a_very_long_query_string_parses_in_reasonable_time(self):
        line = COMBINED.replace("ref=home", "q=" + "a" * 8000)

        started = time.monotonic()
        event = parse_access_line(line)
        elapsed = time.monotonic() - started

        self.assertEqual(len(event.query_string), 8002)
        self.assertLess(elapsed, 0.5, "the access pattern is backtracking")


class ErrorPrefix(unittest.TestCase):
    def test_every_field_of_a_full_error_line(self):
        event = parse_error_line(UPSTREAM_TIMEOUT)

        self.assertEqual(event.timestamp, datetime(2026, 9, 22, 16, 42, 12))
        self.assertEqual(event.level, "error")
        self.assertEqual(event.pid, 1234)
        self.assertEqual(event.tid, 1234)
        self.assertEqual(event.connection, 928)
        self.assertEqual(event.client, "177.10.20.30")
        self.assertEqual(event.server, "example.com")
        self.assertEqual(event.request, "GET /checkout HTTP/1.1")
        self.assertEqual(event.upstream, "fastcgi://unix:/tmp/php.sock:")
        self.assertEqual(event.host, "example.com")

    def test_the_message_stops_before_the_context(self):
        event = parse_error_line(UPSTREAM_TIMEOUT)

        self.assertEqual(
            event.message,
            "upstream timed out (110: Connection timed out) "
            "while reading response header from upstream",
        )
        self.assertNotIn("client:", event.message)

    def test_the_timestamp_is_naive_because_the_line_carries_no_zone(self):
        event = parse_error_line(UPSTREAM_TIMEOUT)

        self.assertIsNone(event.timestamp.tzinfo)

    def test_every_level_is_accepted(self):
        for level in nginx_error.LEVELS:
            line = UPSTREAM_TIMEOUT.replace("[error]", f"[{level}]")
            self.assertEqual(parse_error_line(line).level, level, level)

    def test_an_unknown_level_is_kept_not_dropped(self):
        line = UPSTREAM_TIMEOUT.replace("[error]", "[bizarre]")

        self.assertEqual(parse_error_line(line).level, "bizarre")

    def test_a_line_without_a_connection_id(self):
        line = '2026/09/22 16:42:12 [notice] 1#1: using the "epoll" event method'
        event = parse_error_line(line)

        self.assertIsNone(event.connection)
        self.assertEqual(event.pid, 1)
        self.assertIn("epoll", event.message)

    def test_a_line_without_a_pid(self):
        line = "2026/09/22 16:42:12 [emerg] something went very wrong"
        event = parse_error_line(line)

        self.assertIsNone(event.pid)
        self.assertEqual(event.level, "emerg")
        self.assertEqual(event.message, "something went very wrong")


class ErrorContext(unittest.TestCase):
    def test_a_message_containing_commas_is_not_split_on_them(self):
        line = (
            "2026/09/22 16:42:12 [error] 1#1: *1 a message with, commas, inside "
            'it, client: 1.2.3.4, server: x.com'
        )
        event = parse_error_line(line)

        self.assertEqual(event.message, "a message with, commas, inside it")
        self.assertEqual(event.client, "1.2.3.4")
        self.assertEqual(event.server, "x.com")

    def test_a_request_url_containing_a_comma(self):
        line = (
            "2026/09/22 16:42:12 [error] 1#1: *1 boom, client: 1.2.3.4, "
            'request: "GET /a,b,c?x=1,2 HTTP/1.1", host: "x.com"'
        )
        event = parse_error_line(line)

        self.assertEqual(event.request, "GET /a,b,c?x=1,2 HTTP/1.1")
        self.assertEqual(event.host, "x.com")

    def test_a_trailing_referrer_does_not_leak_into_the_host(self):
        """`referrer` is not kept, but it must still end the value before it."""
        line = (
            "2026/09/22 16:42:12 [error] 1#1: *1 boom, client: 1.2.3.4, "
            'host: "x.com", referrer: "https://ref.example/"'
        )
        event = parse_error_line(line)

        self.assertEqual(event.host, "x.com")

    def test_a_line_with_no_context_at_all(self):
        line = "2026/09/22 16:42:12 [alert] 1#1: worker process exited on signal 9"
        event = parse_error_line(line)

        self.assertEqual(event.message, "worker process exited on signal 9")
        self.assertIsNone(event.client)

    def test_a_very_long_message_is_bounded(self):
        line = "2026/09/22 16:42:12 [error] 1#1: *1 " + "x" * 9000
        event = parse_error_line(line)

        self.assertEqual(len(event.message), nginx_error.MAX_MESSAGE)


class ErrorKinds(unittest.TestCase):
    """A kind names what a line says. It is not a finding (SPEC-007)."""

    def test_upstream_timeout(self):
        self.assertEqual(parse_error_line(UPSTREAM_TIMEOUT).kind, "upstream_timeout")

    def _kind_of(self, message):
        line = f"2026/09/22 16:42:12 [error] 1#1: *1 {message}"
        return parse_error_line(line).kind

    def test_the_patterns_we_claim_to_recognise(self):
        cases = {
            "connect() failed (111: Connection refused) while connecting to upstream":
                "connect_failed",
            "upstream prematurely closed connection while reading response header":
                "upstream_closed",
            "recv() failed (104: Connection reset by peer) while reading":
                "recv_failed",
            "768 worker_connections are not enough": "worker_connections_exhausted",
            "accept4() failed (24: Too many open files)": "too_many_open_files",
            "client intended to send too large body: 20000000 bytes": "body_too_large",
            'open() failed (2: No such file or directory)': "open_failed",
            "no live upstreams while connecting to upstream": "no_live_upstreams",
        }
        for message, expected in cases.items():
            self.assertEqual(self._kind_of(message), expected, message)

    def test_php_messages_carried_through_fastcgi(self):
        cases = {
            'FastCGI sent in stderr: "PHP message: PHP Fatal error: Uncaught Error"':
                "php_fatal",
            'FastCGI sent in stderr: "PHP message: PHP Warning: fopen failed"':
                "php_warning",
            'FastCGI sent in stderr: "PHP message: PHP Parse error: syntax error"':
                "php_parse_error",
            'FastCGI sent in stderr: "PHP message: PHP Fatal error: Allowed memory '
            'size of 134217728 bytes exhausted"': "php_fatal",
            'FastCGI sent in stderr: "PHP message: Maximum execution time of 30 '
            'seconds exceeded"': "php_execution_timeout",
        }
        for message, expected in cases.items():
            self.assertEqual(self._kind_of(message), expected, message)

    def test_a_plain_fastcgi_stderr_line(self):
        message = 'FastCGI sent in stderr: "Primary script unknown"'

        self.assertEqual(self._kind_of(message), "fastcgi_stderr")

    def test_an_unrecognised_message_has_no_kind(self):
        self.assertIsNone(self._kind_of("something entirely new happened"))

    def test_a_kind_never_looks_like_a_finding_code(self):
        """SPEC-007 findings are SCREAMING_CASE; kinds are not, on purpose."""
        for _pattern, kind in nginx_error.KIND_PATTERNS:
            self.assertEqual(kind, kind.lower(), kind)


class ErrorMalformed(unittest.TestCase):
    def test_an_empty_line(self):
        self.assertIsNone(parse_error_line(""))

    def test_a_continuation_line_from_a_stack_trace(self):
        """No prefix, so no event; joining these is deferred, see SPEC-004."""
        self.assertIsNone(parse_error_line("#1 /www/wwwroot/x.php(12): foo()"))

    def test_an_access_log_line_fed_to_the_error_parser(self):
        self.assertIsNone(parse_error_line(COMBINED))

    def test_a_truncated_prefix(self):
        self.assertIsNone(parse_error_line("2026/09/22 16:42"))

    def test_an_impossible_date(self):
        line = "2026/02/31 16:42:12 [error] 1#1: *1 boom"

        self.assertIsNone(parse_error_line(line).timestamp)


class Purity(unittest.TestCase):
    """The parsers must stay functions of their input alone."""

    def test_the_same_line_always_gives_the_same_result(self):
        first = parse_access_line(COMBINED)
        second = parse_access_line(COMBINED)

        self.assertEqual(first, second)

    def test_error_parsing_is_repeatable(self):
        self.assertEqual(
            parse_error_line(UPSTREAM_TIMEOUT), parse_error_line(UPSTREAM_TIMEOUT)
        )

    def test_the_parsers_never_raise_on_arbitrary_text(self):
        pieces = [
            "",
            " ",
            "-",
            '"',
            '""',
            "[",
            "]",
            "�",
            "\x00",
            "2026/09/22",
            "[error]",
            '"GET',
            "1.2.3.4",
            "a" * 500,
            "\\",
            "%",
            "?",
            ",",
            ": ",
        ]
        for first in pieces:
            for second in pieces:
                for line in (first + second, second + first, first + " " + second):
                    parse_access_line(line)
                    parse_error_line(line)
                    # Reaching here without an exception is the assertion.

    def test_truncations_of_a_valid_line_never_raise(self):
        for index in range(len(COMBINED)):
            parse_access_line(COMBINED[:index])
            parse_error_line(UPSTREAM_TIMEOUT[:index])


class Dispatch(unittest.TestCase):
    def _event(self, line, log_type=ACCESS):
        return LogEvent(Path("/www/wwwlogs/a.log"), log_type, line, ("example.com",))

    def test_the_log_type_chooses_the_parser(self):
        access = parsers.parse_event(self._event(COMBINED, ACCESS))
        error = parsers.parse_event(self._event(UPSTREAM_TIMEOUT, ERROR))

        self.assertIsInstance(access, nginx_access.AccessEvent)
        self.assertIsInstance(error, nginx_error.ErrorEvent)

    def test_the_type_is_never_guessed_from_the_line(self):
        """An error line arriving on an access log stays unparsed."""
        self.assertIsNone(parsers.parse_event(self._event(UPSTREAM_TIMEOUT, ACCESS)))

    def test_site_metadata_travels_with_the_event(self):
        parsed = parsers.parse_event(self._event(COMBINED))

        self.assertEqual(parsed.site, "example.com")
        self.assertEqual(parsed.log_path, Path("/www/wwwlogs/a.log"))
        self.assertEqual(parsed.log_type, ACCESS)

    def test_an_unknown_log_type_yields_nothing(self):
        self.assertIsNone(parsers.parse("anything", "mysql"))


class Counting(unittest.TestCase):
    def _event(self, line, log_type=ACCESS, path="/www/wwwlogs/a.log"):
        return LogEvent(Path(path), log_type, line, ("example.com",))

    def test_parsed_and_unparsed_are_counted_separately(self):
        stats = parsers.ParseStats()
        parsers.consume(self._event(COMBINED), stats)
        parsers.consume(self._event("rubbish"), stats)
        parsers.consume(self._event(UPSTREAM_TIMEOUT, ERROR), stats)
        parsers.consume(self._event("rubbish", ERROR), stats)

        self.assertEqual(stats.access_parsed, 1)
        self.assertEqual(stats.access_unparsed, 1)
        self.assertEqual(stats.error_parsed, 1)
        self.assertEqual(stats.error_unparsed, 1)
        self.assertEqual(stats.failures, 0)

    def test_a_parser_bug_is_counted_apart_from_a_malformed_line(self):
        stats = parsers.ParseStats()
        broken = LogEvent(Path("/l/a.log"), ACCESS, COMBINED, ("x",))
        broken.log_type = None  # makes parse() return None, not raise

        parsers.consume(broken, stats)
        self.assertEqual(stats.failures, 0)

        class Exploding:
            path = Path("/l/a.log")
            log_type = ACCESS
            site = "x"

            @property
            def line(self):
                raise RuntimeError("boom")

        with self.assertLogs("aadoctor.parsers", level="WARNING"):
            parsers.consume(Exploding(), stats)

        self.assertEqual(stats.failures, 1)

    def test_an_unfamiliar_format_is_reported_once_per_file(self):
        stats = parsers.ParseStats()

        with self.assertLogs("aadoctor.parsers", level="WARNING") as captured:
            for _ in range(60):
                parsers.consume(self._event("not a log line"), stats)

        self.assertEqual(len(captured.records), 1)
        self.assertIn("log_format", captured.records[0].getMessage())

    def test_no_warning_below_the_minimum_volume(self):
        stats = parsers.ParseStats()
        for _ in range(parsers.MIN_LINES_FOR_RATIO - 1):
            parsers.consume(self._event("not a log line"), stats)

        self.assertEqual(stats._warned, set())

    def test_no_warning_when_most_lines_parse(self):
        stats = parsers.ParseStats()
        for _ in range(60):
            parsers.consume(self._event(COMBINED), stats)
        for _ in range(5):
            parsers.consume(self._event("not a log line"), stats)

        self.assertEqual(stats._warned, set())

    def test_counters_are_bounded_by_the_number_of_files(self):
        stats = parsers.ParseStats()
        for index in range(3):
            for _ in range(100):
                parsers.consume(self._event(COMBINED, path=f"/l/{index}.log"), stats)

        self.assertEqual(len(stats.lines_by_path), 3)
        self.assertEqual(stats.access_parsed, 300)

    def test_the_summary_is_counts_not_content(self):
        stats = parsers.ParseStats()
        parsers.consume(self._event(COMBINED), stats)
        summary = stats.summary()

        self.assertIn("1 parsed", summary)
        self.assertNotIn("produto", summary)


class SourcePurity(unittest.TestCase):
    """The parser modules must touch nothing outside their argument."""

    MODULES = ("nginx_access.py", "nginx_error.py")

    def _code_lines(self, name):
        """Lines with comments and string literals removed.

        The error parser's pattern table contains strings like "open() failed",
        which are data, not calls.
        """
        path = _support.ROOT / "src" / "aadoctor" / "parsers" / name
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            yield number, re.sub(r"\"[^\"]*\"|'[^']*'", "", line)

    def test_no_input_or_output(self):
        forbidden = (
            "open(",
            "os.stat",
            "os.path",
            "Path(",
            "subprocess",
            "socket",
            "urlopen",
            "eval(",
            "exec(",
        )
        for name in self.MODULES:
            for number, line in self._code_lines(name):
                for call in forbidden:
                    self.assertNotIn(call, line, f"{name}:{number}")


class Performance(unittest.TestCase):
    """A smoke test, not a benchmark: it catches an absurdly slow parser."""

    def test_parsing_many_lines_stays_quick(self):
        lines = [COMBINED] * 20000

        started = time.monotonic()
        for line in lines:
            parse_access_line(line)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 10.0, f"20k lines took {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main()
