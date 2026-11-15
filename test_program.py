"""
Test program: shows the Search & Filter microservice being called and responding.

Start the service first in another terminal:
    python search_filter_service.py
Then run:
    python test_program.py

For each example this program prints:
  1. the JSON request it SENDS to the microservice,
  2. the HTTP status and JSON response it RECEIVES back.

Uses only the Python standard library.
"""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVICE_URL = "http://127.0.0.1:5002/search_filter"

GROCERIES = [
    {"id": 1, "name": "Whole Milk", "category": "Dairy", "quantity": 1},
    {"id": 2, "name": "Almond Milk", "category": "Beverages", "quantity": 3},
    {"id": 3, "name": "Cheddar Cheese", "category": "Dairy", "quantity": 2},
    {"id": 4, "name": "Apple", "category": "Produce", "quantity": 6},
]


def send_request(payload):
    """REQUEST: POST the payload as JSON. RECEIVE: return (HTTP status, decoded JSON)."""
    body = json.dumps(payload).encode("utf-8")
    request = Request(SERVICE_URL, data=body, method="POST",
                      headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=3) as response:            # success (200)
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:                                   # error (400, 404, ...)
        return error.code, json.loads(error.read().decode("utf-8"))


def pretty(obj):
    """JSON with one record per line, so the request/response fit on screen."""
    lines = ["{"]
    items = list(obj.items())
    for i, (key, value) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            lines.append(f'  "{key}": [')
            for j, record in enumerate(value):
                end = "," if j < len(value) - 1 else ""
                lines.append(f"    {json.dumps(record)}{end}")
            lines.append(f"  ]{comma}")
        else:
            lines.append(f'  "{key}": {json.dumps(value)}{comma}')
    lines.append("}")
    return "\n".join(lines)


def run_example(title, payload):
    print("=" * 70)
    print(title)
    print("-" * 70)
    print(f"REQUEST  -> POST {SERVICE_URL}")
    print(pretty(payload))

    status, data = send_request(payload)

    print(f"\nRESPONSE <- HTTP {status}")
    print(pretty(data))

    # Using the received data the way a main program would:
    if data["status"] == "success":
        names = [record["name"] for record in data["results"]]
        print(f"\nReceived {data['count']} matching record(s): {names}")
    else:
        print(f"\nReceived an error message: {data['message']}")
    print()


def main():
    run_example("Example 1: SEARCH by name (query 'milk' in the name field)", {
        "records": GROCERIES,
        "query": "milk",
        "search_fields": ["name"],
    })
    run_example("Example 2: FILTER by category (category = Dairy)", {
        "records": GROCERIES,
        "filters": {"category": "Dairy"},
    })
    run_example("Example 3: SEARCH + FILTER combined (both must match)", {
        "records": GROCERIES,
        "query": "milk",
        "search_fields": ["name"],
        "filters": {"category": "Dairy"},
    })
    run_example("Example 4: INVALID request (records is missing) -> error response", {
        "query": "milk",
    })


if __name__ == "__main__":
    try:
        main()
    except (URLError, TimeoutError, ConnectionError) as error:
        print(f"Could not reach the microservice at {SERVICE_URL}: {error}")
        print("Start it first with:  python search_filter_service.py")
