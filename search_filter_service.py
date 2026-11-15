"""Reusable search-and-filter microservice using only the Python standard library.

Run this file in its own process; clients call POST /search_filter (default port 5002).

The file is organized in three layers:
  1. Parsing   - turn raw JSON into a validated SearchRequest (or raise InvalidRequest)
  2. Matching  - pure functions that assume a valid SearchRequest (no I/O)
  3. HTTP      - status codes, headers, CORS and the JSON response envelope
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

MAX_REQUEST_BYTES = 5 * 1024 * 1024  # 5 MiB

log = logging.getLogger("search_filter")


# --------------------------------------------------------------------------- #
# 1. Parsing / validation
# --------------------------------------------------------------------------- #
class InvalidRequest(ValueError):
    """Invalid JSON request or unsupported search/filter options."""


@dataclass(frozen=True)
class SearchRequest:
    records: list[dict]
    query: str = ""
    search_fields: tuple[str, ...] | None = None
    # Always a tuple of allowed values per field, so matching never has to
    # ask "is this a list or a single value?"
    filters: dict[str, tuple] = field(default_factory=dict)
    case_sensitive: bool = False


def _is_scalar(value) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def parse_request(payload) -> SearchRequest:
    """Validate a decoded JSON body and return a SearchRequest.

    JSON contract:
      records: list[object]                      (required)
      query: str                                 (optional, default "")
      search_fields: list[str]                   (optional; all scalar fields if omitted)
      filters: {field: scalar | list[scalar]}    (optional; AND across fields, OR within a list)
      case_sensitive: bool                       (optional, default false)
    """
    if not isinstance(payload, dict):
        raise InvalidRequest("Request body must be a JSON object")
    if "records" not in payload:
        raise InvalidRequest("records is required")

    records = payload["records"]
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise InvalidRequest("records must be a list of JSON objects")

    query = payload.get("query", "")
    if not isinstance(query, str):
        raise InvalidRequest("query must be a string")

    fields = payload.get("search_fields")
    if fields is not None and (
        not isinstance(fields, list)
        or any(not isinstance(f, str) or not f for f in fields)
    ):
        raise InvalidRequest("search_fields must be a list of nonempty strings")

    raw_filters = payload.get("filters", {})
    if not isinstance(raw_filters, dict) or any(not k for k in raw_filters):
        raise InvalidRequest("filters must be an object with nonempty string keys")
    filters: dict[str, tuple] = {}
    for key, expected in raw_filters.items():
        options = tuple(expected) if isinstance(expected, list) else (expected,)
        if any(not _is_scalar(v) for v in options):
            raise InvalidRequest(f"filter '{key}' must contain scalar JSON values")
        filters[key] = options

    case_sensitive = payload.get("case_sensitive", False)
    if not isinstance(case_sensitive, bool):
        raise InvalidRequest("case_sensitive must be a boolean")

    return SearchRequest(
        records=records,
        query=query,
        search_fields=tuple(fields) if fields is not None else None,
        filters=filters,
        case_sensitive=case_sensitive,
    )


# --------------------------------------------------------------------------- #
# 2. Matching (pure: no JSON, HTTP or OS imports used below this line)
# --------------------------------------------------------------------------- #
def _normalize(value, *, case_sensitive: bool) -> str:
    text = str(value)
    return text if case_sensitive else text.casefold()


def _equal(actual, expected, case_sensitive: bool) -> bool:
    """Exact comparison, including membership in list-valued record fields.

    Values are compared as text, so 2 matches "2". null only matches null.
    """
    if isinstance(actual, list):
        return any(_equal(item, expected, case_sensitive) for item in actual)
    if not _is_scalar(actual) or not _is_scalar(expected):
        return False
    if actual is None or expected is None:
        return actual is expected
    return _normalize(actual, case_sensitive=case_sensitive) == _normalize(
        expected, case_sensitive=case_sensitive
    )


def _matches_query(record: dict, needle: str, fields, case_sensitive: bool) -> bool:
    """Substring search over scalar values; nested objects/arrays are skipped."""
    if not needle:
        return True
    values = record.values() if fields is None else (record.get(f) for f in fields)
    return any(
        needle in _normalize(v, case_sensitive=case_sensitive)
        for v in values
        if v is not None and _is_scalar(v)
    )


def _matches_filters(record: dict, filters: dict, case_sensitive: bool) -> bool:
    """AND across fields, OR across the allowed values of one field."""
    return all(
        name in record
        and any(_equal(record[name], option, case_sensitive) for option in options)
        for name, options in filters.items()
    )


def search(request: SearchRequest) -> list[dict]:
    """Return matching records unchanged and in their original order."""
    cs = request.case_sensitive
    needle = _normalize(request.query.strip(), case_sensitive=cs)
    return [
        record
        for record in request.records
        if _matches_query(record, needle, request.search_fields, cs)
        and _matches_filters(record, request.filters, cs)
    ]


def search_and_filter(payload) -> dict:
    """Convenience wrapper: raw JSON payload in, success response body out."""
    results = search(parse_request(payload))
    return {"status": "success", "count": len(results), "results": results}


# --------------------------------------------------------------------------- #
# 3. HTTP transport
# --------------------------------------------------------------------------- #
class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SearchHandler(BaseHTTPRequestHandler):
    # Browsers calling from another origin (e.g. a website on port 3000) need
    # CORS headers. The service holds no data, so any origin is allowed by default.
    allow_origin = os.getenv("SEARCH_FILTER_ALLOW_ORIGIN", "*")

    # ---- helpers ----------------------------------------------------------- #
    def _send_cors_headers(self) -> None:
        if self.allow_origin:
            self.send_header("Access-Control-Allow-Origin", self.allow_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _respond(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, status: int, message: str) -> None:
        self._respond(status, {"status": "error", "message": message})

    def _route(self) -> str:
        return urlsplit(self.path).path.rstrip("/") or "/"

    def _read_json_body(self):
        header = self.headers.get("Content-Length")
        if header is None:
            raise HttpError(411, "Content-Length required")
        try:
            length = int(header)
        except ValueError:
            raise HttpError(400, "Invalid Content-Length") from None
        if length < 0:
            raise HttpError(400, "Invalid Content-Length")
        if length > MAX_REQUEST_BYTES:
            raise HttpError(413, "Request too large (limit is 5 MiB)")
        try:
            return json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HttpError(400, "Invalid JSON") from None

    def log_message(self, format, *args) -> None:  # noqa: A002 (stdlib signature)
        log.info("%s - %s", self.address_string(), format % args)

    # ---- routes ------------------------------------------------------------ #
    def do_OPTIONS(self) -> None:
        """CORS preflight for browser clients."""
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self._route() == "/health":
            self._respond(200, {"status": "ok", "service": "search_filter"})
        else:
            self._error(404, "Unknown endpoint")

    def do_POST(self) -> None:
        if self._route() != "/search_filter":
            self._error(404, "Unknown endpoint")
            return
        try:
            body = search_and_filter(self._read_json_body())
        except HttpError as error:
            self._error(error.status, error.message)
        except InvalidRequest as error:
            self._error(400, str(error))
        except Exception:  # last resort: never drop the connection silently
            log.exception("Unhandled error while processing request")
            self._error(500, "Internal server error")
        else:
            self._respond(200, body)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = os.getenv("SEARCH_FILTER_HOST", "127.0.0.1")
    port = int(os.getenv("SEARCH_FILTER_PORT", "5002"))
    server = ThreadingHTTPServer((host, port), SearchHandler)
    server.daemon_threads = True  # in-flight requests don't block Ctrl+C
    print(f"Search & Filter service running at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
