"""
Tests for the Search & Filter microservice.

Run: python -m unittest test_service.py -v

No server needs to be running: HTTP tests start a temporary one on a free port.
"""
import json
import threading
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest import mock

import search_filter_service as svc
from client_examples import SearchServiceError, call_search_service


@contextmanager
def running_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), svc.SearchHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def raw_request(port, method, path, body=None, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, (json.loads(data) if data else None)


class ParsingTests(unittest.TestCase):
    def test_records_required(self):
        with self.assertRaises(svc.InvalidRequest):
            svc.parse_request({})

    def test_records_must_be_objects(self):
        for bad in (3, "x", [1, 2], [{"a": 1}, "b"]):
            with self.assertRaises(svc.InvalidRequest):
                svc.parse_request({"records": bad})

    def test_option_types_checked(self):
        bad_payloads = [
            {"records": [], "query": 5},
            {"records": [], "search_fields": "name"},
            {"records": [], "search_fields": [""]},
            {"records": [], "filters": []},
            {"records": [], "filters": {"a": {"nested": 1}}},
            {"records": [], "case_sensitive": "yes"},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(svc.InvalidRequest):
                svc.parse_request(payload)

    def test_filters_normalized_to_tuples(self):
        request = svc.parse_request({"records": [], "filters": {"a": 1, "b": [2, 3]}})
        self.assertEqual(request.filters, {"a": (1,), "b": (2, 3)})


class MatchingTests(unittest.TestCase):
    def run_search(self, **payload):
        return svc.search(svc.parse_request(payload))

    def test_grocery_search_and_filter(self):
        records = [{"name": "Milk", "category": "Dairy"},
                   {"name": "Almond Milk", "category": "Plant-Based"}]
        result = svc.search_and_filter({"records": records, "query": "milk",
                                        "search_fields": ["name"],
                                        "filters": {"category": "Dairy"}})
        self.assertEqual(result, {"status": "success", "count": 1, "results": [records[0]]})

    def test_other_project(self):
        tasks = [{"title": "Study", "status": "open"}, {"title": "Study", "status": "done"}]
        self.assertEqual(self.run_search(records=tasks, query="stu", search_fields=["title"],
                                         filters={"status": "open"}), [tasks[0]])

    def test_no_criteria_returns_everything_in_order(self):
        records = [{"n": 3}, {"n": 1}, {"n": 2}]
        self.assertEqual(self.run_search(records=records), records)

    def test_query_searches_all_scalar_fields_when_no_search_fields(self):
        records = [{"name": "Max", "breed": "Beagle"}, {"name": "Luna", "breed": "Tabby"}]
        self.assertEqual(self.run_search(records=records, query="beag"), [records[0]])

    def test_or_within_field_and_across_fields(self):
        records = [{"s": "open", "p": "high"}, {"s": "pending", "p": "low"},
                   {"s": "done", "p": "high"}]
        result = self.run_search(records=records,
                                 filters={"s": ["open", "pending"], "p": "high"})
        self.assertEqual(result, [records[0]])

    def test_missing_field_never_matches_filter(self):
        self.assertEqual(self.run_search(records=[{"a": 1}], filters={"b": None}), [])

    def test_list_field_matches_if_any_element_matches(self):
        pets = [{"name": "Max", "tags": ["friendly", "vaccinated"]},
                {"name": "Luna", "tags": ["shy"]}]
        self.assertEqual(self.run_search(records=pets, filters={"tags": "Friendly"}), [pets[0]])

    def test_values_compared_as_text_and_null_only_matches_null(self):
        records = [{"q": 2}, {"q": "2"}, {"q": None}]
        self.assertEqual(self.run_search(records=records, filters={"q": "2"}), records[:2])
        self.assertEqual(self.run_search(records=records, filters={"q": None}), [records[2]])

    def test_case_sensitive_option(self):
        records = [{"name": "Milk"}, {"name": "milk"}]
        self.assertEqual(self.run_search(records=records, query="Milk", case_sensitive=True),
                         [records[0]])

    def test_nested_values_are_not_searched(self):
        records = [{"name": "A", "meta": {"note": "milk"}}]
        self.assertEqual(self.run_search(records=records, query="milk"), [])

    def test_input_records_are_not_modified(self):
        records = [{"name": "Milk", "tags": ["x"]}]
        snapshot = json.dumps(records)
        self.run_search(records=records, query="milk", filters={"tags": "x"})
        self.assertEqual(json.dumps(records), snapshot)


class HttpTests(unittest.TestCase):
    def test_real_http_request_with_example_client(self):
        with running_server() as port:
            url = f"http://127.0.0.1:{port}/search_filter"
            result = call_search_service([{"name": "Milk"}, {"name": "Cheese"}],
                                         "milk", ["name"], url=url)
        self.assertEqual(result, [{"name": "Milk"}])

    def test_health(self):
        with running_server() as port:
            response, body = raw_request(port, "GET", "/health")
        self.assertEqual(response.status, 200)
        self.assertEqual(body, {"status": "ok", "service": "search_filter"})

    def test_error_status_codes(self):
        json_headers = {"Content-Type": "application/json"}
        cases = [
            ("GET", "/nope", None, {}, 404),
            ("POST", "/nope", b"{}", json_headers, 404),
            ("POST", "/search_filter", b"not json", json_headers, 400),
            ("POST", "/search_filter", b'{"records": 3}', json_headers, 400),
            ("POST", "/search_filter", b"{}",
             {**json_headers, "Content-Length": str(svc.MAX_REQUEST_BYTES + 1)}, 413),
        ]
        with running_server() as port:
            for method, path, body, headers, expected in cases:
                with self.subTest(path=path, body=body, expected=expected):
                    response, data = raw_request(port, method, path, body, headers)
                    self.assertEqual(response.status, expected)
                    self.assertEqual(data["status"], "error")
                    self.assertTrue(data["message"])

    def test_unexpected_error_returns_500_instead_of_dropping_connection(self):
        with running_server() as port, \
                mock.patch.object(svc, "search", side_effect=RuntimeError("boom")), \
                self.assertLogs("search_filter", level="ERROR"):
            response, data = raw_request(port, "POST", "/search_filter", b'{"records": []}',
                                         {"Content-Type": "application/json"})
        self.assertEqual(response.status, 500)
        self.assertEqual(data, {"status": "error", "message": "Internal server error"})

    def test_query_string_on_path_is_ignored(self):
        with running_server() as port:
            response, _ = raw_request(port, "GET", "/health?check=1")
        self.assertEqual(response.status, 200)

    def test_cors_preflight_and_headers(self):
        with running_server() as port:
            preflight, _ = raw_request(port, "OPTIONS", "/search_filter")
            response, _ = raw_request(port, "POST", "/search_filter", b'{"records": []}',
                                      {"Content-Type": "application/json"})
        self.assertEqual(preflight.status, 204)
        self.assertEqual(preflight.getheader("Access-Control-Allow-Origin"), "*")
        self.assertIn("POST", preflight.getheader("Access-Control-Allow-Methods"))
        self.assertEqual(response.getheader("Access-Control-Allow-Origin"), "*")

    def test_client_reports_service_not_running(self):
        with self.assertRaises(SearchServiceError):
            call_search_service([], url="http://127.0.0.1:9/search_filter", timeout=1)


if __name__ == "__main__":
    unittest.main()
