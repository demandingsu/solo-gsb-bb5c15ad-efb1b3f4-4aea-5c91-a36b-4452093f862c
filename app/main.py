"""DNS SOA serial number advancement audit API.

Serial arithmetic follows RFC 1982 (32-bit serial numbers):

* delta == 0           -> equal
* 1 <= delta < 2**31   -> proposed is NEWER than current
* delta == 2**31       -> ambiguous (the anti-podal value)
* 2**31 < delta        -> proposed is OLDER than current

where delta is the forward modular distance
``(proposed - current) mod 2**32``.
"""

from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import logic
from .logic import (
    AMBIGUOUS_DELTA,
    MODULUS,
    NoCandidateError,
    forward_distance,
)

SerialStatus = Literal["newer", "equal", "older", "ambiguous"]


def _validate_serial(value: object, field_name: str) -> int:
    # bool is a subclass of int; reject it so True/False are not treated as 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if not 0 <= value < MODULUS:
        raise ValueError(
            f"{field_name} must be a 32-bit unsigned integer (0..{MODULUS - 1})"
        )
    return value


class Replica(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    current: int

    @field_validator("name", mode="before")
    @classmethod
    def _non_blank_name(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("replica name must be a non-blank string")
        return value

    @field_validator("current", mode="before")
    @classmethod
    def _valid_current(cls, value: object) -> int:
        return _validate_serial(value, "current")


def _check_unique_names(names: list[str]) -> None:
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate replica names: {', '.join(duplicates)}")


class AuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed: int
    replicas: list[Replica] = Field(..., min_length=1)

    @field_validator("proposed", mode="before")
    @classmethod
    def _valid_proposed(cls, value: object) -> int:
        return _validate_serial(value, "proposed")

    @model_validator(mode="after")
    def _unique_replica_names(self) -> "AuditRequest":
        _check_unique_names([replica.name for replica in self.replicas])
        return self


class ReplicasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replicas: list[Replica] = Field(..., min_length=1)

    @model_validator(mode="after")
    def _unique_replica_names(self) -> "ReplicasRequest":
        _check_unique_names([replica.name for replica in self.replicas])
        return self


class ReplicaAudit(BaseModel):
    name: str
    current: int
    proposed: int
    status: SerialStatus
    forward_distance: int = Field(
        ...,
        description="(proposed - current) mod 2**32; 2**31 means ambiguous",
    )


class AuditResponse(BaseModel):
    proposed: int
    blocked: bool
    blocking_replicas: list[str] = Field(
        ...,
        description=(
            "Replicas for which proposed is not strictly newer "
            "(equal, older, or ambiguous)"
        )
    )
    results: list[ReplicaAudit]


class ReplicaDistance(BaseModel):
    name: str
    current: int
    forward_distance: int


class ProposeResponse(BaseModel):
    candidate: int
    maximum_forward_distance: int
    replicas: list[ReplicaDistance]


app = FastAPI(
    title="DNS SOA Serial Advancement Audit API",
    version="1.0.0",
    description=(
        "Audit and propose DNS SOA serial number updates under "
        "RFC 1982 ring arithmetic."
    ),
)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "soa-serial-audit", "docs": "/docs"}


@app.post("/audit", response_model=AuditResponse)
def audit(request: AuditRequest) -> AuditResponse:
    results: list[ReplicaAudit] = []
    blocking_replicas: list[str] = []

    for replica in request.replicas:
        status, distance = logic.compare(replica.current, request.proposed)
        results.append(
            ReplicaAudit(
                name=replica.name,
                current=replica.current,
                proposed=request.proposed,
                status=status,
                forward_distance=distance,
            )
        )
        # Publication is blocked unless the proposed serial is strictly newer
        # (and therefore comparable) from this replica's current serial.
        if status != logic.NEWER:
            blocking_replicas.append(replica.name)

    return AuditResponse(
        proposed=request.proposed,
        blocked=bool(blocking_replicas),
        blocking_replicas=blocking_replicas,
        results=results,
    )


@app.post(
    "/propose",
    response_model=ProposeResponse,
    responses={409: {"description": "No valid candidate exists"}},
)
def propose(request: ReplicasRequest):
    replicas = [(replica.name, replica.current) for replica in request.replicas]
    try:
        candidate = logic.propose_candidate(replicas)
    except NoCandidateError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": (
                    "no 32-bit serial is strictly newer than and comparable "
                    "with every replica (replicas span the RFC 1982 "
                    "ambiguity gap)"
                ),
                "code": "NO_CANDIDATE",
                "blocking_replicas": exc.blocking_replicas,
            },
        )

    return ProposeResponse(
        candidate=candidate.value,
        maximum_forward_distance=candidate.maximum_forward_distance,
        replicas=[
            ReplicaDistance(
                name=name,
                current=current,
                forward_distance=distance,
            )
            for name, current, distance in candidate.distances
        ],
    )
