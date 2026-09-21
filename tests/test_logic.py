"""Exhaustive verification of app/logic.py using small moduli + 32-bit edges."""

import itertools
from app import logic


def reference_compare(current, proposed, m):
    d = (proposed - current) % m
    h = m // 2
    if d == 0:
        return logic.EQUAL
    if d < h:
        return logic.NEWER
    if d == h:
        return logic.AMBIGUOUS
    return logic.OLDER


def brute_propose(replicas, m):
    """Return (value, maxdist) or None. replicas = [(name, current), ...]."""
    h = m // 2
    feasible = []
    for c in range(m):
        distances = [(c - cur) % m for _, cur in replicas]
        if all(1 <= d <= h - 1 for d in distances):
            feasible.append((c, max(distances)))
    if not feasible:
        return None
    best = min(feasible, key=lambda x: (x[1], x[0]))
    return best


def with_modulus(m):
    """Context-like patcher of the module constants."""
    class P:
        def __enter__(self_):
            self_.saved = (
                logic.MODULUS,
                logic.AMBIGUOUS_DELTA,
                logic.MAX_FORWARD_DISTANCE,
            )
            logic.MODULUS = m
            logic.AMBIGUOUS_DELTA = m // 2
            logic.MAX_FORWARD_DISTANCE = m // 2 - 1
            return self_

        def __exit__(self_, *exc):
            (
                logic.MODULUS,
                logic.AMBIGUOUS_DELTA,
                logic.MAX_FORWARD_DISTANCE,
            ) = self_.saved

    return P()


def test_exhaustive():
    # Powers of two (valid RFC-style moduli) plus several set sizes.
    for m in (4, 8, 16, 32):
        with with_modulus(m):
            # compare: every pair
            for cur in range(m):
                for prop in range(m):
                    status, dist = logic.compare(cur, prop)
                    assert dist == (prop - cur) % m
                    assert status == reference_compare(cur, prop, m), (
                        m, cur, prop, status
                    )
            assert logic.compare(0, m // 2)[0] == logic.AMBIGUOUS
            assert logic.compare(m - 1, m // 2 - 1)[0] == logic.AMBIGUOUS

            # propose: every 1- and 2-replica multiset of serials
            for size in (1, 2):
                for currents in itertools.product(range(m), repeat=size):
                    replicas = [
                        (f"r{i}", cur) for i, cur in enumerate(currents)
                    ]
                    expected = brute_propose(replicas, m)
                    if expected is None:
                        try:
                            logic.propose_candidate(replicas)
                            raise AssertionError(
                                f"expected NoCandidate {m=} {replicas=}"
                            )
                        except logic.NoCandidateError:
                            pass
                    else:
                        cand = logic.propose_candidate(replicas)
                        assert cand.value == expected[0], (
                            f"{m=} {replicas=} got {cand.value} "
                            f"want {expected[0]}"
                        )
                        assert (
                            cand.maximum_forward_distance == expected[1]
                        )
                        for (_, cur), (_, _, d) in zip(
                            replicas, cand.distances
                        ):
                            assert d == (cand.value - cur) % m

            # Larger distinct-serial sets on m=16
            if m == 16:
                for subset in itertools.combinations(range(m), 3):
                    replicas = [(f"r{i}", cur) for i, cur in enumerate(subset)]
                    expected = brute_propose(replicas, m)
                    for order in (subset, tuple(reversed(subset))):
                        reps = [
                            (f"r{i}", cur) for i, cur in enumerate(order)
                        ]
                        if expected is None:
                            try:
                                logic.propose_candidate(reps)
                                raise AssertionError("expected NoCandidate")
                            except logic.NoCandidateError:
                                pass
                        else:
                            cand = logic.propose_candidate(reps)
                            assert cand.value == expected[0], (
                                subset, order, cand.value, expected
                            )
                            assert (
                                cand.maximum_forward_distance == expected[1]
                            )
    print("exhaustive small-modulus tests passed")


def test_32bit_edges():
    M = 1 << 32
    H = 1 << 31

    # compare boundary deltas
    cases = [
        # (current, proposed, status)
        (0, 0, logic.EQUAL),
        (0, 1, logic.NEWER),
        (0, H - 1, logic.NEWER),
        (0, H, logic.AMBIGUOUS),
        (0, H + 1, logic.OLDER),
        (0, M - 1, logic.OLDER),
        (M - 1, 0, logic.NEWER),
        (M - 1, H - 1, logic.AMBIGUOUS),  # distance H across wrap
        (H, 0, logic.AMBIGUOUS),
        (10, 10, logic.EQUAL),
        (5, 2, logic.OLDER),
    ]
    for cur, prop, want in cases:
        status, dist = logic.compare(cur, prop)
        assert status == want, (cur, prop, status, want)
        assert dist == (prop - cur) % M
        assert 0 <= dist < M

    # single replica -> current + 1 (wrap)
    c = logic.propose_candidate([("a", M - 1)])
    assert c.value == 0 and c.maximum_forward_distance == 1
    c = logic.propose_candidate([("a", 0)])
    assert c.value == 1 and c.maximum_forward_distance == 1

    # 2^31 apart -> impossible
    for cur in (0, 1, 12345, H, M - 1):
        try:
            logic.propose_candidate([("a", cur), ("b", (cur + H) % M)])
            raise AssertionError("expected ambiguity failure")
        except logic.NoCandidateError as exc:
            assert set(exc.blocking_replicas) == {"a", "b"}

    # span 2^31-1 (both boundaries touch) -> impossible
    try:
        logic.propose_candidate([("a", 10), ("b", 10 + H - 1)])
        raise AssertionError
    except logic.NoCandidateError:
        pass

    # span 2^31-2 -> candidate is the unique gap-closing value
    c = logic.propose_candidate([("a", 10), ("b", 10 + H - 2)])
    assert c.value == 10 + H - 1
    assert c.maximum_forward_distance == H - 1
    assert [d for _, _, d in c.distances] == [H - 1, 1]

    # wrap-around cluster
    c = logic.propose_candidate(
        [("a", M - 10), ("b", M - 5), ("c", M - 1)]
    )
    assert c.value == 0, c.value
    assert c.maximum_forward_distance == 10

    # reverse ordering must not change result
    c2 = logic.propose_candidate(
        [("a", M - 1), ("b", M - 5), ("c", M - 10)]
    )
    assert c2.value == 0 and c2.maximum_forward_distance == 10

    # tight cluster mid-range: candidate = max current + 1
    c = logic.propose_candidate([("a", 100), ("b", 105), ("c", 110)])
    assert c.value == 111
    assert c.maximum_forward_distance == 11

    # identical serials behave like one
    c = logic.propose_candidate(
        [("a", 100), ("b", 100), ("c", 105)]
    )
    assert c.value == 106 and c.maximum_forward_distance == 6

    # feasibility spot-check near ambiguity: brute force a sampled 32-bit case
    import random

    rng = random.Random(7)
    for _ in range(200):
        k = rng.randint(1, 5)
        currents = [rng.randrange(M) for _ in range(k)]
        replicas = [(f"r{i}", cur) for i, cur in enumerate(currents)]
        feasible, best = [], None
        # The optimum is one step ahead of some replica, so only those
        # k points need to be evaluated (proved by the interval argument).
        candidates = {(cur + 1) % M for cur in currents}
        for val in candidates:
            dists = [(val - cur) % M for cur in currents]
            if all(1 <= d <= H - 1 for d in dists):
                score = (max(dists), val)
                if best is None or score < best:
                    best = score
        if best is None:
            try:
                logic.propose_candidate(replicas)
                raise AssertionError("expected failure")
            except logic.NoCandidateError:
                pass
        else:
            cand = logic.propose_candidate(replicas)
            assert (cand.maximum_forward_distance, cand.value) == best, (
                currents, (cand.maximum_forward_distance, cand.value), best
            )
    print("32-bit edge tests passed")


if __name__ == "__main__":
    test_exhaustive()
    test_32bit_edges()
    print("ALL TESTS PASSED")
