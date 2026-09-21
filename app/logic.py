"""Pure RFC 1982 serial arithmetic, free of any web framework dependency."""

from dataclasses import dataclass

MODULUS = 1 << 32
AMBIGUOUS_DELTA = 1 << 31
MAX_FORWARD_DISTANCE = AMBIGUOUS_DELTA - 1

# status: proposed relative to current
EQUAL = "equal"
NEWER = "newer"
OLDER = "older"
AMBIGUOUS = "ambiguous"


class NoCandidateError(Exception):
    """No serial is strictly newer than and comparable with every replica."""

    def __init__(self, blocking_replicas: list[str]):
        super().__init__(
            "replicas span the RFC 1982 ambiguity gap; no candidate exists"
        )
        self.blocking_replicas = blocking_replicas


def forward_distance(current: int, proposed: int) -> int:
    """Signed-free modular distance: (proposed - current) mod 2**32."""
    return (proposed - current) % MODULUS


def compare(current: int, proposed: int) -> tuple[str, int]:
    """Classify *proposed* against *current* per RFC 1982.

    Returns ``(status, forward_distance)`` where status is one of
    ``equal``, ``newer``, ``older``, ``ambiguous``.
    """
    distance = forward_distance(current, proposed)
    if distance == 0:
        return EQUAL, distance
    if distance < AMBIGUOUS_DELTA:
        return NEWER, distance
    if distance == AMBIGUOUS_DELTA:
        return AMBIGUOUS, distance
    return OLDER, distance


@dataclass(frozen=True)
class Candidate:
    value: int
    maximum_forward_distance: int
    distances: list[tuple[str, int, int]]  # (name, current, distance)


def propose_candidate(replicas: list[tuple[str, int]]) -> Candidate:
    """Find the optimal serial to publish.

    The candidate must be strictly newer than (and therefore comparable with)
    every replica's current serial, and among such candidates it minimizes the
    maximum forward distance; ties are broken by smallest numeric value.

    Implemented by cutting the serial ring at the first replica (s0) and
    intersecting, for each replica, the integer interval of offsets that are
    1..2**31-1 ahead of it.
    """
    s0 = replicas[0][1]

    lower = -AMBIGUOUS_DELTA
    upper = MAX_FORWARD_DISTANCE
    lower_owners: list[str] = []
    upper_owners: list[str] = []

    for name, current in replicas:
        offset = (current - s0) % MODULUS
        if offset <= MAX_FORWARD_DISTANCE:
            replica_lower = offset + 1
            replica_upper = offset + MAX_FORWARD_DISTANCE
        else:
            replica_lower = offset + 1 - MODULUS
            replica_upper = offset - AMBIGUOUS_DELTA - 1

        if replica_lower > lower:
            lower = replica_lower
            lower_owners = [name]
        elif replica_lower == lower:
            lower_owners.append(name)

        if replica_upper < upper:
            upper = replica_upper
            upper_owners = [name]
        elif replica_upper == upper:
            upper_owners.append(name)

    if lower > upper:
        raise NoCandidateError(sorted(set(lower_owners + upper_owners)))

    # Each replica's forward distance grows with the chosen offset, so the
    # smallest feasible offset minimizes the maximum. The optimum is unique,
    # which also satisfies the "smallest numeric value" tie-break rule.
    value = (s0 + lower) % MODULUS
    distances = [
        (name, current, forward_distance(current, value))
        for name, current in replicas
    ]
    maximum_forward_distance = max(distance for _, _, distance in distances)
    return Candidate(value, maximum_forward_distance, distances)
