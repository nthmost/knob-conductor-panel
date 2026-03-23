# Radio API Contract

This document defines the API contract between the KNOB Conductor Panel
and the KNOB Radio Control API (nbradio). Both systems are deployed on
beyla but are independent repos that can run separately.

**Radio API repo:** `github.com/nthmost/nbradio`
**Panel repo:** `github.com/nthmost/knob-conductor-panel`

**Self-documenting:** The radio API serves a full OpenAPI 3.0 spec at
`GET /api/spec` and a human-readable endpoint index at `GET /`.
Point any LLM at `http://beyla:8081/api/spec` to learn the full API.

## How the panel discovers the API

The panel connects to `$RADIO_API_URL` (default `http://localhost:8081`).
If the radio API is unavailable, all radio features degrade gracefully —
the panel still runs with heartbeats, entropy, network blinken, etc.

## Endpoints the panel consumes

### `GET /api/now-playing` (polled every 3s)

This is the primary data source. The panel reads these fields:

| Field | Type | Used for |
|-------|------|----------|
| `artist` | string | Now-playing card, track history, activity log |
| `title` | string | Now-playing card, track history |
| `source` | string | Source badge (AUTODJ, Pandora's Box, Noisefloor, LIVE DJ) |
| `remaining` | number\|null | Track progress countdown (seconds) |
| `listeners` | integer | Blinken LEDs 0-7 (one green LED per listener) |
| `genre_override` | object\|null | Genre mode badge + program bar + GENRE MODE lamp |
| `genre_override.genre` | string | Genre name display |
| `genre_override.subgenre` | string\|null | Subgenre display |
| `next_source` | string | Current program bar ("Pandora's Box until 10am") |
| `next_hour_fmt` | string | Schedule time display |
| `stream_start_ts` | number | Stream uptime counter |

The panel also reads but does not display:
- `filename` — used for source detection fallback
- `scheduled_source` — stored in `_radio_now`

### `GET /api/genres` (proxied to frontend on demand)

Returns the genre tree for the control panel's genre selector.

**Expected shape:**
```json
{
  "genres": {
    "Electronic": {
      "count": 142,
      "subgenres": {"Ambient": 23, "Dubstep": 45}
    }
  }
}
```

### `GET /api/genre` (proxied to frontend)

Returns current genre override status.

**Expected shape:**
```json
{"active": true, "genre": "Electronic", "subgenre": "Dubstep",
 "tracks_available": 45, "tracks_pushed": 3}
```
or `{"active": false, "genre": null, "subgenre": null}`

### `POST /api/genre` (proxied from control panel)

Sets genre override. Body: `{"genre": "Electronic", "subgenre": "Dubstep"}`

Subgenre is optional. Takes effect after current track finishes.

### `DELETE /api/genre` (proxied from control panel)

Clears genre override. Returns to normal schedule.

## Endpoints the panel does NOT consume

These exist in the radio API but the panel doesn't use them:

- `GET /api/queue` — queue status
- `POST /api/queue` — queue a track
- `DELETE /api/queue` — clear queue
- `POST /api/skip` — skip current track
- `GET /api/search` — search music library

These could be wired into the control panel in the future.

## Liquidsoap telnet (separate connection)

The panel also connects directly to Liquidsoap's telnet interface
(`$LIQUIDSOAP_HOST:$LIQUIDSOAP_PORT`, default `localhost:1234`) for
DJ detection. This is independent of the radio API.

**Command used:** `input.harbor.status`
**Expected response:** Empty string (no DJ) or client info string (DJ connected)

## Icecast (separate connection)

The panel polls Icecast directly for stream metadata:

- `$ICECAST_URL` (default `http://localhost:8000/status-json.xsl`) — stream start time
- `$ICECAST_STREAM_URL` (default `http://localhost:8000/stream.ogg`) — proxied at `/stream`

## Versioning

When changing the radio API response shape:

1. Update this contract document in knob-conductor-panel
2. Update the OpenAPI spec in radio_api.py (`OPENAPI_SPEC`)
3. Ensure backward compatibility (add fields, don't remove or rename)

The radio API's OpenAPI spec version is in `OPENAPI_SPEC["info"]["version"]`.
