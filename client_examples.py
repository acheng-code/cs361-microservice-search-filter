"""
Example Python client for the Search & Filter microservice (standard library only).

Copy `call_search_service` and `SearchServiceError` into your own app.
Run this file directly (with the service running) to see four team examples.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SEARCH_URL = "http://127.0.0.1:5002/search_filter"


class SearchServiceError(RuntimeError):
    """The service is not running, timed out, or rejected the request."""


def call_search_service(records, query="", search_fields=None, filters=None,
                        url=SEARCH_URL, timeout=3):
    """Send records + criteria to the service and return the matching records."""
    body = {"records": records, "query": query, "filters": filters or {}}
    if search_fields is not None:
        body["search_fields"] = search_fields
    request = Request(url, data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:  # 400, 404, 413, 500 ...
        try:
            message = json.loads(error.read().decode("utf-8")).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        raise SearchServiceError(f"Search service returned HTTP {error.code}: {message}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise SearchServiceError(f"Could not reach the search service at {url}: {error}") from error
    return data["results"]


if __name__ == "__main__":
    examples = [
        ("To-do", [{"title": "Buy milk", "status": "pending"},
                   {"title": "Write report", "status": "done"}],
         "buy", ["title"], {"status": "pending"}),
        ("Grocery", [{"name": "Milk", "category": "Dairy"},
                     {"name": "Apple", "category": "Produce"}],
         "milk", ["name"], {"category": "Dairy"}),
        ("Stocks", [{"symbol": "ACME", "sector": "Technology"},
                    {"symbol": "FOOD", "sector": "Consumer"}],
         "acm", ["symbol"], {"sector": "Technology"}),
        ("Animal rescue", [{"name": "Max", "species": "Dog"},
                           {"name": "Luna", "species": "Cat"}],
         "max", ["name"], {"species": "Dog"}),
    ]
    try:
        for label, records, text, fields, filters in examples:
            print(label, call_search_service(records, text, fields, filters))
    except SearchServiceError as error:
        print(f"Error: {error}\nIs search_filter_service.py running?")
