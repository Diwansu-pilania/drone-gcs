"""Response-recommendation endpoints. RUNS ON THE DATABASE MACHINE.

Deploy this beside the existing /nearby service on the box that holds the
Postgres credentials (100.111.81.89). The GCS never touches the database; it
posts a detected object and its threat score, and gets back a ranked list of
checkpoint-drone pairings with the reasoning.

Mount it on the existing app:

    from response_api import router as response_router
    app.include_router(response_router)

Endpoints
---------
POST /response/recommend    rank checkpoint-drone pairs for a detected object
POST /response/authorize    record a human's decision on a recommendation
GET  /response/authorizations   recent decisions

Nothing here commands a drone. /response/authorize RECORDS that a person
approved or rejected a pairing; wiring that to an actual dispatch is a
separate, deliberate step.

Requires the table below. Run it once:

    CREATE TABLE IF NOT EXISTS response_authorizations (
        authorization_id   SERIAL PRIMARY KEY,
        decision           VARCHAR(20)  NOT NULL,
        authorized_by      VARCHAR(100) NOT NULL,
        checkpoint_id      INTEGER,
        drone_id           INTEGER,
        detection_ref      VARCHAR(200),
        threat_score       NUMERIC(5,2),
        recommendation     JSONB,
        note               TEXT,
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- and, if it is not already there, the spatial index the lookups need
    CREATE INDEX IF NOT EXISTS checkpoints_location_gix
        ON checkpoints USING GIST (location);
    CREATE INDEX IF NOT EXISTS drones_location_gix
        ON drones USING GIST (cur_location);
"""

import json
import os
from typing import Any, Dict, List, Optional

import psycopg
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from response_scoring import rank_pairs

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")

router = APIRouter(prefix="/response", tags=["response"])

# How far out to look for drones when the caller does not say. Drones farther
# than their own operational range are filtered out by the scoring anyway;
# this only bounds the query.
DEFAULT_DRONE_SEARCH_KM = 50.0


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
def get_nearby_checkpoints(latitude: float, longitude: float,
                           radius_m: float) -> List[Dict[str, Any]]:
    """Checkpoints within radius_m of the point, nearest first."""
    query = """
        SELECT
            id,
            name,
            checkpoint_type,
            status,
            ST_Y(location::geometry) AS latitude,
            ST_X(location::geometry) AS longitude,
            ST_Distance(
                location,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) AS distance_m
        FROM checkpoints
        WHERE ST_DWithin(
            location,
            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
            %s
        )
        ORDER BY distance_m;
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (longitude, latitude, longitude, latitude,
                                radius_m))
            rows = cur.fetchall()

    return [
        {"id": r[0], "name": r[1], "checkpoint_type": r[2], "status": r[3],
         "latitude": float(r[4]), "longitude": float(r[5]),
         "distance_m": round(float(r[6]), 2)}
        for r in rows
    ]


def get_candidate_drones(latitude: float, longitude: float,
                         radius_m: float) -> List[Dict[str, Any]]:
    """Available drones within radius_m of the point, nearest first.

    Only availability is filtered here — every other rejection (status,
    battery, range, endurance) is made by the scoring so it can be reported
    with a reason rather than vanishing from the result.
    """
    query = """
        SELECT
            drone_id,
            drone_name,
            type_of_drone,
            status,
            availability,
            battery_percentage,
            max_flight_time_min,
            operational_range_km,
            time_to_airborne_min,
            ST_Y(cur_location::geometry) AS latitude,
            ST_X(cur_location::geometry) AS longitude,
            ST_Distance(
                cur_location,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
            ) AS distance_m
        FROM drones
        WHERE availability = TRUE
          AND cur_location IS NOT NULL
          AND ST_DWithin(
                cur_location,
                ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,
                %s
          )
        ORDER BY distance_m;
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (longitude, latitude, longitude, latitude,
                                radius_m))
            rows = cur.fetchall()

    def maybe_float(value):
        return None if value is None else float(value)

    def maybe_int(value):
        return None if value is None else int(value)

    return [
        {"drone_id": r[0], "drone_name": r[1], "type_of_drone": r[2],
         "status": r[3], "availability": r[4],
         "battery_percentage": maybe_float(r[5]),
         "max_flight_time_min": maybe_int(r[6]),
         "operational_range_km": maybe_float(r[7]),
         "time_to_airborne_min": maybe_int(r[8]),
         "latitude": float(r[9]), "longitude": float(r[10]),
         "distance_m": round(float(r[11]), 2)}
        for r in rows
    ]


def record_authorization(payload: "AuthorizeRequest") -> Dict[str, Any]:
    """Store a human decision and return the stored row."""
    query = """
        INSERT INTO response_authorizations
            (decision, authorized_by, checkpoint_id, drone_id,
             detection_ref, threat_score, recommendation, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING authorization_id, created_at;
    """
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (
                payload.decision,
                payload.authorized_by,
                payload.checkpoint_id,
                payload.drone_id,
                payload.detection_ref,
                payload.threat_score,
                json.dumps(payload.recommendation) if payload.recommendation
                else None,
                payload.note,
            ))
            row = cur.fetchone()
        conn.commit()

    return {"authorization_id": row[0], "created_at": row[1].isoformat()}


# --------------------------------------------------------------------------
# Request / response models
# --------------------------------------------------------------------------
class RecommendRequest(BaseModel):
    latitude: float = Field(..., description="Detected object's latitude")
    longitude: float = Field(..., description="Detected object's longitude")
    radius_m: float = Field(..., gt=0,
                            description="Checkpoint search radius, metres — "
                                        "normally the equipment's max range")
    threat_score: Optional[float] = Field(
        None, ge=0, le=100, description="The GCS's 0-100 threat score, "
                                        "carried through for context")
    threat_band: Optional[str] = None
    object_class: Optional[str] = None
    detection_ref: Optional[str] = Field(
        None, description="Identifies the detection, e.g. its image_file")
    drone_search_radius_m: Optional[float] = Field(
        None, gt=0, description="How far out to consider drones; defaults to "
                                f"{DEFAULT_DRONE_SEARCH_KM:.0f} km")


class AuthorizeRequest(BaseModel):
    decision: str = Field(..., description="'approved' or 'rejected'")
    authorized_by: str = Field(..., min_length=1,
                               description="Who is accountable for this")
    checkpoint_id: Optional[int] = None
    drone_id: Optional[int] = None
    detection_ref: Optional[str] = None
    threat_score: Optional[float] = None
    recommendation: Optional[Dict[str, Any]] = None
    note: Optional[str] = None


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------
@router.post("/recommend")
def recommend_response(request: RecommendRequest):
    """Rank checkpoint-drone pairings for a detected object."""
    drone_radius = request.drone_search_radius_m or (
        DEFAULT_DRONE_SEARCH_KM * 1000.0)

    try:
        checkpoints = get_nearby_checkpoints(request.latitude,
                                             request.longitude,
                                             request.radius_m)
        drones = get_candidate_drones(request.latitude, request.longitude,
                                      drone_radius)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503,
                            detail=f"database unavailable: {exc}") from exc

    ranking = rank_pairs(checkpoints, drones, request.latitude,
                         request.longitude)

    # Say plainly when there is nothing to recommend and why, rather than
    # returning an empty list that reads the same for every cause.
    if not checkpoints:
        ranking["no_options_because"] = (
            f"no checkpoints within {request.radius_m / 1000.0:.1f} km of the "
            f"object")
    elif not drones:
        ranking["no_options_because"] = (
            f"no available drone within {drone_radius / 1000.0:.0f} km")
    elif not ranking["options"]:
        ranking["no_options_because"] = (
            "every checkpoint-drone pairing was ruled out; see 'excluded' "
            "for the reason against each")

    return {
        "target": {"latitude": request.latitude,
                   "longitude": request.longitude},
        "radius_m": request.radius_m,
        "drone_search_radius_m": drone_radius,
        "threat_score": request.threat_score,
        "threat_band": request.threat_band,
        "object_class": request.object_class,
        "detection_ref": request.detection_ref,
        "checkpoint_count": len(checkpoints),
        "drone_count": len(drones),
        # The decision is a person's to make; this only ranks the options.
        "requires_human_authorization": True,
        **ranking,
    }


@router.post("/authorize")
def authorize_response(request: AuthorizeRequest):
    """Record a human's approval or rejection of a recommended pairing."""
    decision = request.decision.strip().lower()
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=422,
                            detail="decision must be 'approved' or 'rejected'")
    request.decision = decision

    try:
        stored = record_authorization(request)
    except psycopg.Error as exc:
        raise HTTPException(status_code=503,
                            detail=f"could not record decision: {exc}") from exc

    return {"recorded": True, "decision": decision,
            "authorized_by": request.authorized_by,
            "checkpoint_id": request.checkpoint_id,
            "drone_id": request.drone_id, **stored}


@router.get("/authorizations")
def list_authorizations(limit: int = Query(20, gt=0, le=200)):
    """Most recent decisions, newest first."""
    query = """
        SELECT authorization_id, decision, authorized_by, checkpoint_id,
               drone_id, detection_ref, threat_score, note, created_at
        FROM response_authorizations
        ORDER BY created_at DESC
        LIMIT %s;
    """
    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(query, (limit,))
                rows = cur.fetchall()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503,
                            detail=f"database unavailable: {exc}") from exc

    return {"authorizations": [
        {"authorization_id": r[0], "decision": r[1], "authorized_by": r[2],
         "checkpoint_id": r[3], "drone_id": r[4], "detection_ref": r[5],
         "threat_score": None if r[6] is None else float(r[6]),
         "note": r[7], "created_at": r[8].isoformat()}
        for r in rows
    ]}
