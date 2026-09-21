# DNS SOA Serial Advancement Audit API

Audit and propose DNS SOA serial number updates using [RFC 1982](https://www.rfc-editor.org/rfc/rfc1982)
32-bit ring arithmetic, built with FastAPI.

## Run

Single command (requires Docker with the Compose plugin):

```bash
docker compose up --build
```

The API is then available at **http://localhost:8000**

- Interactive docs (Swagger UI): http://localhost:8000/docs
- OpenAPI schema: http://localhost:8000/openapi.json

## Serial comparison rules

For a replica with `current` serial and a `proposed` value, the forward modular
distance is `delta = (proposed - current) mod 2^32`:

| `delta`                  | status      | meaning                                  |
|--------------------------|-------------|------------------------------------------|
| `0`                      | `equal`     | identical serials                        |
| `1 .. 2^31 - 1`          | `newer`     | proposed is strictly newer (publishable) |
| `2^31`                   | `ambiguous` | anti-podal value, order undefined        |
| `2^31 + 1 .. 2^32 - 1`   | `older`     | proposed is behind the replica           |

## `POST /audit`

Audit one proposed serial against the current serials of every replica.

```bash
curl -s http://localhost:8000/audit \
  -H 'Content-Type: application/json' \
  -d '{
        "proposed": 20,
        "replicas": [
          {"name": "a", "current": 10},
          {"name": "b", "current": 18},
          {"name": "c", "current": 30},
          {"name": "d", "current": 2147483668}
        ]
      }'
```

```json
{
  "proposed": 20,
  "blocked": true,
  "blocking_replicas": ["c", "d"],
  "results": [
    {"name": "a", "current": 10, "proposed": 20, "status": "newer", "forward_distance": 10},
    {"name": "b", "current": 18, "proposed": 20, "status": "newer", "forward_distance": 2},
    {"name": "c", "current": 30, "proposed": 20, "status": "older", "forward_distance": 4294967286},
    {"name": "d", "current": 2147483668, "proposed": 20, "status": "ambiguous", "forward_distance": 2147483648}
  ]
}
```

A replica blocks publication unless the proposed serial is **strictly newer**
from that replica's current serial (`equal`, `older`, and `ambiguous` all block).
A distance of exactly `2147483648` (2^31) is reported as `ambiguous`.

## `POST /propose`

Derive a candidate serial that is **strictly newer than and comparable with
every replica**, minimizing the maximum forward distance across replicas; on a
tie the numerically smallest candidate is chosen.

```bash
curl -s http://localhost:8000/propose \
  -H 'Content-Type: application/json' \
  -d '{
        "replicas": [
          {"name": "a", "current": 4294967290},
          {"name": "b", "current": 4294967295}
        ]
      }'
```

```json
{
  "candidate": 0,
  "maximum_forward_distance": 6,
  "replicas": [
    {"name": "a", "current": 4294967290, "forward_distance": 6},
    {"name": "b", "current": 4294967295, "forward_distance": 1}
  ]
}
```

If no such serial exists — the replicas span the RFC 1982 ambiguity gap (e.g.
two serials exactly 2^31 apart, or spanning the full forward semicircle) — the
endpoint responds with **409 Conflict** and lists the blocking replicas:

```json
{
  "detail": "no 32-bit serial is strictly newer than and comparable with every replica (replicas span the RFC 1982 ambiguity gap)",
  "code": "NO_CANDIDATE",
  "blocking_replicas": ["a", "b"]
}
```

## Validation errors (422)

- Any serial outside the 32-bit unsigned range `0 .. 4294967295`.
- Duplicate (or blank) replica names.
- An empty replica list, wrong types, or unknown fields.

These are rejected with HTTP **422 Unprocessable Entity** and a Pydantic error
detail body.

## Local development (without Docker)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_logic.py` is framework-free and exhaustively verifies the RFC 1982
algorithm over small moduli plus 32-bit boundary cases; `tests/test_api.py`
exercises the HTTP endpoints with FastAPI's `TestClient`.
