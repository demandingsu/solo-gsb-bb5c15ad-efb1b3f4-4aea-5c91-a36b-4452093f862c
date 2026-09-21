"""End-to-end API tests with FastAPI's TestClient.

Run with:  pip install -r requirements-dev.txt && pytest -q
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

M = 1 << 32
H = 1 << 31


# ---------------- POST /audit ----------------

def test_audit_mixed_statuses_and_blocking():
    resp = client.post("/audit", json={
        "proposed": 20,
        "replicas": [
            {"name": "new1", "current": 10},
            {"name": "eq", "current": 20},
            {"name": "old1", "current": 30},
            {"name": "wrapnew", "current": M - 1},
            {"name": "amb", "current": 20 + H},
        ],
    })
    assert resp.status_code == 200
    body = resp.json()
    by = {r["name"]: r for r in body["results"]}

    assert by["new1"]["status"] == "newer"
    assert by["new1"]["forward_distance"] == 10

    assert by["eq"]["status"] == "equal"
    assert by["eq"]["forward_distance"] == 0

    assert by["old1"]["status"] == "older"
    assert by["old1"]["forward_distance"] == M - 10

    assert by["wrapnew"]["status"] == "newer"
    assert by["wrapnew"]["forward_distance"] == 21

    # exactly 2^31 forward must be ambiguous
    assert by["amb"]["status"] == "ambiguous"
    assert by["amb"]["forward_distance"] == H

    assert body["blocked"] is True
    assert body["blocking_replicas"] == ["eq", "old1", "amb"]


def test_audit_order_preserved_and_unblocked():
    resp = client.post("/audit", json={
        "proposed": 5,
        "replicas": [
            {"name": "z", "current": 1},
            {"name": "a", "current": 4},
            {"name": "m", "current": M - 100},
        ],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert [r["name"] for r in body["results"]] == ["z", "a", "m"]
    assert body["blocked"] is False
    assert body["blocking_replicas"] == []


def test_audit_boundaries():
    # distance H-1 -> newer; distance H -> ambiguous; distance H+1 -> older
    for dist, status in (
        (H - 1, "newer"),
        (H, "ambiguous"),
        (H + 1, "older"),
        (1, "newer"),
        (M - 1, "older"),
        (0, "equal"),
    ):
        resp = client.post("/audit", json={
            "proposed": dist,
            "replicas": [{"name": "a", "current": 0}],
        })
        result = resp.json()["results"][0]
        assert result["status"] == status, (dist, status)
        assert result["forward_distance"] == dist


# ---------------- POST /propose ----------------

def test_propose_wraparound():
    resp = client.post("/propose", json={
        "replicas": [
            {"name": "a", "current": M - 10},
            {"name": "b", "current": M - 5},
            {"name": "c", "current": M - 1},
        ],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate"] == 0
    assert body["maximum_forward_distance"] == 10
    assert {d["name"]: d["forward_distance"]
            for d in body["replicas"]} == {"a": 10, "b": 5, "c": 1}


def test_propose_single_replica():
    resp = client.post("/propose", json={
        "replicas": [{"name": "a", "current": 42}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate"] == 43
    assert body["maximum_forward_distance"] == 1


def test_propose_minimizes_max_distance():
    # span 2^31-2: the only min-max candidate closes the gap
    resp = client.post("/propose", json={
        "replicas": [
            {"name": "a", "current": 10},
            {"name": "b", "current": 10 + H - 2},
        ],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate"] == 10 + H - 1
    assert body["maximum_forward_distance"] == H - 1


def test_propose_409_when_antipodal():
    for current in (0, 1, 12345, H, M - 1):
        resp = client.post("/propose", json={
            "replicas": [
                {"name": "a", "current": current},
                {"name": "b", "current": (current + H) % M},
            ],
        })
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "NO_CANDIDATE"
        assert set(body["blocking_replicas"]) == {"a", "b"}


def test_propose_409_when_spanning_semicircle():
    resp = client.post("/propose", json={
        "replicas": [
            {"name": "a", "current": 10},
            {"name": "b", "current": 10 + H - 1},
        ],
    })
    assert resp.status_code == 409


# ---------------- 422 validation ----------------

def test_422_serial_out_of_range():
    payloads = [
        {"proposed": M, "replicas": [{"name": "a", "current": 0}]},
        {"proposed": -1, "replicas": [{"name": "a", "current": 0}]},
        {"proposed": 0, "replicas": [{"name": "a", "current": M}]},
        {"proposed": 0, "replicas": [{"name": "a", "current": -1}]},
    ]
    for payload in payloads:
        assert client.post("/audit", json=payload).status_code == 422


def test_422_duplicate_names():
    for path in ("/audit", "/propose"):
        payload = {
            "replicas": [
                {"name": "x", "current": 1},
                {"name": "x", "current": 2},
            ]
        }
        if path == "/audit":
            payload["proposed"] = 3
        resp = client.post(path, json=payload)
        assert resp.status_code == 422
        assert "duplicate" in str(resp.json()).lower()


def test_422_misc_bad_inputs():
    cases = [
        # blank name
        ("/propose", {"replicas": [{"name": "  ", "current": 1}]}),
        # bool must not count as an integer
        ("/audit", {"proposed": True,
                    "replicas": [{"name": "a", "current": 1}]}),
        # float must not count as an integer
        ("/audit", {"proposed": 1.5,
                    "replicas": [{"name": "a", "current": 1}]}),
        # empty replica list
        ("/propose", {"replicas": []}),
        # missing field
        ("/audit", {"proposed": 1, "replicas": [{"name": "a"}]}),
        # extra unknown field at top level
        ("/audit", {"proposed": 1, "replicas": [{"name": "a", "current": 1}],
                    "bogus": 2}),
        # extra unknown field nested
        ("/propose", {"replicas": [{"name": "a", "current": 1, "x": 2}]}),
        # string as integer
        ("/audit", {"proposed": "1",
                    "replicas": [{"name": "a", "current": 1}]}),
    ]
    for path, payload in cases:
        resp = client.post(path, json=payload)
        assert resp.status_code == 422, (path, payload, resp.status_code)
