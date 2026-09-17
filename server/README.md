# Response service — deploy on the database machine

These two files run on `100.111.81.89`, beside the existing `/nearby`
service. They stay here in the GCS repo so the scoring and the client that
consumes it are versioned together, but the GCS never imports them: it has no
database credentials, and is not meant to.

| File | |
|---|---|
| `response_scoring.py` | The model. Pure Python — no database, no HTTP. |
| `response_api.py` | FastAPI routes and the psycopg queries. |

## Install

```bash
pip install fastapi psycopg[binary] python-dotenv pydantic
```

`DATABASE_URL` is read from the environment or a `.env`, exactly as your
existing `db.py` does.

## Database

```sql
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

CREATE INDEX IF NOT EXISTS checkpoints_location_gix
    ON checkpoints USING GIST (location);
CREATE INDEX IF NOT EXISTS drones_location_gix
    ON drones USING GIST (cur_location);
```

## Mount it

Copy both files beside your existing app and add two lines:

```python
from response_api import router as response_router
app.include_router(response_router)
```

## Endpoints

```
POST /response/recommend        rank checkpoint-drone pairs for an object
POST /response/authorize        record a person's decision
GET  /response/authorizations   recent decisions
```

```bash
curl -X POST http://localhost:8000/response/recommend \
  -H 'Content-Type: application/json' \
  -d '{"latitude":18.6071,"longitude":73.8750,"radius_m":5000,
       "threat_score":51.3,"threat_band":"HIGH",
       "object_class":"military_tank","detection_ref":"tank.jpg"}'
```

## The model

A **pair** is one drone and the checkpoint it flies from. Each drone is
matched to its nearest checkpoint — a drone only ever launches from where it
stands, so pairing one with a checkpoint it is not at describes nothing real
and would list the same drone several times.

**Feasibility comes first, as a hard filter.** A drone that is unavailable,
not in a ready status, under 20% battery, out of range, or short on endurance
for the round trip is *excluded with a stated reason*, never ranked low — a
low rank still reads as "possible". Excluded pairs are returned too, so the
recommendation can be justified against what was ruled out.

**Survivors are scored out of 100:**

| Factor | Weight | |
|---|---:|---|
| ETA | 35 | time to get airborne + flight time to the object |
| Range margin | 20 | reach left over beyond the distance flown |
| Battery | 20 | charge in hand |
| Checkpoint proximity | 15 | how close the station is to the object |
| Readiness | 10 | availability and status |

Weights and thresholds are constants at the top of `response_scoring.py`.
They need not total 100 — the score is scaled by whatever they sum to.

**Speed is derived, not stored.** The table has no speed column, so
`operational_range_km ÷ max_flight_time_min` is used. That treats the range
as one-way, which is the conservative reading: if it is meant as a round
trip, the real speed is higher and the ETA is pessimistic rather than
optimistic.

## Authorization

`/response/recommend` always returns `"requires_human_authorization": true`,
and **nothing here dispatches a drone**. `/response/authorize` records that a
person approved or rejected a pairing, with their name, the drone, the
checkpoint, the threat score and the full recommendation. Acting on an
approval is a separate step you would add deliberately.
