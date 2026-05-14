# VisionAPISmart

A semantic REST API layer for [Axis](https://www.axis.com) IP camera systems. It acquires frames via VAPIX, optionally crops a Region of Interest, and sends them to an AI vision model to answer structured questions — returning machine-readable JSON for every query.

---

## What it does

```
Axis Camera ──VAPIX──► VisionAPISmart ──LLM──► { "people_count": 3, "confidence": 0.97 }
```

- **Live snapshot analysis** — capture a frame right now and run any configured action on it
- **Historical recording analysis** — point to a past timestamp, retrieve the H.264 recording from the camera's SD card, extract the frame with ffmpeg, and analyze it
- **Sequence analysis** — download a time window of footage, split it into chunks, run the LLM on each chunk, then synthesize a final verdict (e.g. "was the cabinet accessed?")
- **ROI masking** — each camera can define a polygon; pixels outside it are blacked out before sending to the model
- **Full CRUD** — cameras, actions, and sequences are managed at runtime via API, persisted to YAML, no restart needed

---

## Architecture

```
app/
├── main.py           # FastAPI app, custom OpenAPI schema
├── auth.py           # Dual-level API key auth (X-API-Key / X-Admin-Key)
├── config.py         # Pydantic Settings, YAML loaders
├── axis.py           # VAPIX client: snapshots, recording export, ffmpeg frame extraction
├── vision.py         # OpenAI-compatible LLM client, JSON extraction, retry logic
└── routers/
    ├── cameras.py    # GET/POST/PUT/DELETE /cameras
    ├── actions.py    # GET/POST/PUT/DELETE /actions
    ├── frame.py      # GET /frame/{camera_id}  (JPEG or ZIP)
    ├── analyze.py    # POST /analyze/{camera_id}/{action_id}
    └── sequence.py   # GET/POST/PUT/DELETE /sequence + POST /{camera_id}/{sequence_id}
```

**Key technology choices:**
- **FastAPI** + Pydantic v2
- **httpx** with Digest Auth for VAPIX calls
- **ffmpeg** (system package, ARM64-safe) for H.264 → JPEG frame extraction
- **OpenAI SDK** against any OpenAI-compatible endpoint; supports `enable_thinking` for reasoning models
- **Docker** multi-arch image (`linux/amd64` + `linux/arm64`)

---

## Quick start

### Prerequisites

| Requirement | Min version |
|---|---|
| Docker Engine | 24.0 |
| Docker Compose | v2.20 |
| Axis camera reachable on the network | VAPIX firmware 10+ |
| OpenAI-compatible LLM endpoint | — |

### 1. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
PORT=8000

API_PASSWORD=<reader-key>
API_ADMIN_PASSWORD=<admin-key>

LLM_BASE_URL=https://<your-llm-endpoint>/v1
LLM_API_KEY=<api-key>
LLM_MODEL=<model-name>
LLM_REASONING=false        # set true for models with enable_thinking support
LLM_TIMEOUT=120
LLM_CONTEXT_WINDOW=64000

AXIS_DEFAULT_USER=<axis-username>
AXIS_DEFAULT_PASS=<axis-password>
```

### 2. Configure cameras

Edit `cameras.yaml`:

```yaml
cameras:
  - id: lobby
    label: Lobby Entrance
    base_url: http://10.0.0.10
    channel: 1
    resolution: 1280x720
    compression: 30
    rotation: 0
    recording_diskid: SD_DISK   # required for historical analysis
    roi:                         # optional polygon crop (x,y pairs in px)
      - [100, 50]
      - [900, 50]
      - [900, 600]
      - [100, 600]
```

### 3. Start

```bash
docker compose up -d --build
```

Health check:

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"1.0.0"}
```

Swagger UI: `http://localhost:8000/docs`

---

## API overview

All endpoints require `X-API-Key` (reader) or `X-Admin-Key` (admin) in the request header.

### Frame capture

```bash
# Live JPEG
curl -H "X-API-Key: $KEY" http://localhost:8000/frame/lobby --output frame.jpg

# Historical JPEG (from SD card recording)
curl -H "X-API-Key: $KEY" "http://localhost:8000/frame/lobby?at=2024-06-01T10:30:00Z" --output frame.jpg

# All cameras in a ZIP
curl -H "X-API-Key: $KEY" http://localhost:8000/frame --output all.zip
```

### Single-frame analysis

```bash
curl -s -X POST -H "X-API-Key: $KEY" \
  "http://localhost:8000/analyze/lobby/people_count" | jq .
```

Response:
```json
{
  "camera_id": "lobby",
  "action": "people_count",
  "source": "live",
  "result": { "number": 3, "confidence": 0.95, "describe": "Three individuals in business attire." },
  "frames_captured": 1
}
```

### Sequence analysis

Runs a multi-chunk LLM pipeline over a time window (live or recorded):

```bash
curl -s -X POST -H "X-API-Key: $KEY" \
  "http://localhost:8000/sequence/lobby/cabinet_access_detection?at=2024-06-01T10:30:00Z" | jq .
```

Response includes per-chunk results and a final synthesized verdict:
```json
{
  "source": "recording",
  "chunks_analyzed": 8,
  "final_result": {
    "cabinet_accessed": true,
    "item_taken": true,
    "verdict": "CONFIRMED_ACCESS"
  }
}
```

### Camera / action / sequence management

```bash
# Add a camera (admin key required)
curl -s -X POST -H "X-Admin-Key: $ADMIN_KEY" -H "Content-Type: application/json" \
  -d '{"id":"parking","label":"Parking Lot","base_url":"http://10.0.0.11","channel":1}' \
  http://localhost:8000/cameras | jq .

# List actions
curl -s -H "X-API-Key: $KEY" http://localhost:8000/actions | jq .
```

---

## Built-in actions

| ID | Description | Key output fields |
|---|---|---|
| `people_count` | Counts people in frame | `number`, `confidence`, `describe` |
| `vehicle_count` | Counts cars and trucks | `number`, `confidence`, `describe` |
| `patio_check` | Detects abandoned tableware on outdoor patio | `detected`, `confidence`, `describe` |
| `cleaning_detection` | Detects cleaning equipment indoors | `cleaning_detected`, `tools_found`, `describe` |
| `outdoor_maintenance_detection` | Detects outdoor maintenance activity | `activity_detected`, `tools_found`, `describe` |
| `buffet_setup_detection` | Detects professional buffet/catering setup | `buffet_detected`, `items_found`, `describe` |

Custom actions can be added at runtime via `POST /actions`.

---

## Deployment notes

- **ARM64**: the Docker image runs natively on Apple Silicon, AWS Graviton, and Raspberry Pi 4/5 — no emulation needed
- **YAML persistence**: `cameras.yaml`, `actions.yaml`, `sequences.yaml` are volume-mounted; changes via API survive container rebuilds
- **No reindex required**: cameras with `recording_diskid` automatically use `record/list.cgi` to locate recordings before export
- **Reasoning models**: set `LLM_REASONING=true` to pass `enable_thinking` to the model; the response extractor handles both standard `content` and extended thinking formats
