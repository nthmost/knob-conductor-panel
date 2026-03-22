# knob-conductor-panel

A real-time radio station monitoring panel built on [Conductor OSS](https://github.com/conductor-oss/conductor) and FastAPI. Designed for display on a dedicated screen at the station — no mouse or keyboard required.

Originally built for **KNOB 87.9 FM** at [Noisebridge](https://www.noisebridge.net/), San Francisco's hackerspace.

Live at **knob.nthmost.com**

![Blade Runner theme](docs/screenshot.png)

## What it does

- **Now Playing** — live track info, artist, progress bar, source badge (AutoDJ / Pandora's Box / Noisefloor / Live DJ), in-browser stream playback
- **Live DJ detection** — pulsing red badge when a DJ connects via Icecast/Shoutcast
- **Genre Mode** — green pulsing badge and program bar when genre override is active
- **Listener LEDs** — channels 0-7 on rack A glow green, one LED per active listener
- **Activity monitor** — scrolling log of track changes, DJ events, infrastructure alerts, and Conductor workflow events
- **Entropy scope** — Lissajous oscilloscope reflecting composite system activity (CPU, memory, event velocity, listeners, DJ status)
- **Site health heartbeats** — three-tier monitoring of noisebridge.net and noisebridge.eu (UP / IMPACTED / DOWN) with ECG-style waveforms
- **WiFi signal monitor** — signal quality bars from `/proc/net/wireless`
- **Network blinken** — LEDs 8-15 (rx, blue) and 16-31 (tx, amber) driven by real network traffic
- **Instrument panel** — gauges, lamps, knife switches, tesla coils, and blinken-lights driven by Conductor workflows
- **Conductor workflows** — radio-themed workflows (RF scanning, carrier checks, signal routing) that keep the panel looking alive

## Architecture

```
Icecast / Liquidsoap
       |
       v
  radio HTTP API  -->  FastAPI panel (port 8082)  -->  Browser (SSE)
                              |
                        SQLite (WAL)
                              |
                     Conductor OSS (port 8888)
                       workflows via cron
```

### Backend pollers

| Poller | Interval | Data source | Drives |
|--------|----------|-------------|--------|
| `radio_poller` | 3s | Radio API `/api/now-playing` | Now Playing card, track history, activity log |
| `dj_watcher` | 5s | Liquidsoap telnet (port 1234) | DJ badge, route lamps |
| `icecast_poller` | 30s | Icecast `/status-json.xsl` | Stream uptime counter |
| `listener_blinken_poller` | 100ms | `_radio_now` cache | Green LEDs 0-7 (1 per listener) |
| `site_health_poller` | 60s | HTTP GET to monitored sites | Heartbeat cards, activity log |
| `wifi_poller` | 10s | `/proc/net/wireless` | Signal bars card |
| `net_blinken_poller` | 2s | `/proc/net/dev` | Network LEDs (8-15 rx, 16-31 tx) |
| `entropy_poller` | 2s | Composite calculation | Lissajous scope |

### Blinken LED layout

| Rack | Channels | Purpose | Color |
|------|----------|---------|-------|
| A (LISTENERS // NODE CLUSTER) | 0-7 | Active listeners (steady) | Green |
| A (LISTENERS // NODE CLUSTER) | 8-15 | Network RX traffic | Blue |
| B (SECTOR B // RELAY NET) | 16-31 | Network TX traffic | Amber |

### Heartbeat states

| State | HTTP Status | Waveform | Color | Label | Activity log |
|-------|-------------|----------|-------|-------|-------------|
| UP | 2xx/3xx | Full ECG pulse | Green | `142ms` | "is back UP (142ms)" |
| IMPACTED | 4xx | Low weak pulse | Yellow | `IMPACTED (429)` | "IMPACTED (429)" |
| DOWN | 5xx/timeout | Flatline | Red | `DOWN` | "is DOWN (503)" or "is DOWN (timeout)" |

## Requirements

- Python 3.11+
- [Conductor OSS](https://github.com/conductor-oss/conductor) (any persistence backend; MySQL/MariaDB recommended)
- Icecast 2.x streaming server
- Liquidsoap (optional — for live DJ detection via telnet)
- A radio HTTP API that returns now-playing JSON (see [API contract](#radio-api-contract))
- Linux host (pollers read `/proc/net/dev` and `/proc/net/wireless`)

## Setup

### 1. Install the panel

```bash
git clone https://github.com/nthmost/knob-conductor-panel
cd knob-conductor-panel

python3 -m venv panel-env
source panel-env/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your URLs
```

### 2. Run the panel

```bash
cd panel
uvicorn main:app --host 0.0.0.0 --port 8082
```

Or run as a systemd service — see [docs/systemd.md](docs/systemd.md).

### 3. Register Conductor workflows

With Conductor OSS running:

```bash
python3 workflows/register_workflows.py
```

This registers radio-themed workflows. To fire them on a schedule, add them to crontab:

```cron
*/1  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-stream-pulse","version":1,"input":{}}' > /dev/null 2>&1
*/2  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-signal-route","version":1,"input":{}}' > /dev/null 2>&1
*/3  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-freq-scan","version":1,"input":{}}' > /dev/null 2>&1
*/5  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-carrier-check","version":1,"input":{}}' > /dev/null 2>&1
*/7  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-modulation","version":1,"input":{}}' > /dev/null 2>&1
*/10 * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-storm","version":1,"input":{}}' > /dev/null 2>&1
```

### 4. Expose publicly (optional)

See [docs/apache-proxy.md](docs/apache-proxy.md) to proxy through Apache with Let's Encrypt TLS.

### 5. Continuous deployment (optional)

`deploy.sh` runs via cron every minute on the host — pulls `origin/main`, restarts the service if there are new commits, and reinstalls deps if `requirements.txt` changed.

## Configuration

All config is via environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `CONDUCTOR_URL` | `http://localhost:8888` | Conductor OSS base URL |
| `PANEL_DB` | `/home/nthmost/panel/panel.db` | SQLite database path |
| `RADIO_API_URL` | `http://localhost:8081` | Radio control API (empty string disables radio features) |
| `LIQUIDSOAP_HOST` | `localhost` | Liquidsoap telnet host |
| `LIQUIDSOAP_PORT` | `1234` | Liquidsoap telnet port |
| `ICECAST_URL` | `http://localhost:8000/status-json.xsl` | Icecast status endpoint |
| `ICECAST_STREAM_URL` | `http://localhost:8000/stream.ogg` | Icecast stream (proxied at `/stream`) |
| `WIFI_INTERFACE` | `wlo1` | Network interface for WiFi signal monitoring |

## Adapting for your station

### Radio API contract

The panel expects `GET $RADIO_API_URL/api/now-playing` to return:

```json
{
  "artist": "Daft Punk",
  "title": "Harder, Better, Faster, Stronger",
  "remaining": 183,
  "source": "AutoDJ",
  "listeners": 3,
  "next_source": "AutoDJ",
  "next_hour_fmt": "5pm",
  "genre_override": null
}
```

When `genre_override` is set (e.g. `{"genre": "Dubstep", "subgenre": "Deep"}`), the now-playing card shows a green GENRE MODE badge and the program bar displays the genre.

### Live DJ detection

The panel opens a TCP connection to Liquidsoap's telnet server and sends `input.harbor.status`. If your Liquidsoap config uses a different input name, update `dj_watcher()` in `panel/main.py`.

If you don't use Liquidsoap, remove the `dj_watcher()` call from `lifespan()` — everything else will still work.

### Theming

The panel ships with two themes:

- **Blade Runner** — dark cyan on near-black, beveled cards, scan-line overlays, neon flicker LEDs, fast 120ms blink decay
- **Steampunk** — brass on dark wood, rounded cards with rivet details, soft circular LEDs, slow 380ms blink decay

Themes are CSS custom-property files in `panel/static/themes/`. Add your own by following the same variable structure. To lock to a single theme, set `data-theme` on `<html>` and remove the switcher buttons.

### Conductor workflows

The bundled workflows in `workflows/register_workflows.py` illustrate all panel widget types:

| Workflow | Pattern | Widgets |
|---|---|---|
| `knob-stream-pulse` | Simple HTTP | Gauge, Lamp |
| `knob-radio-sync` | Simple HTTP | Gauge, Lamp |
| `knob-freq-scan` | DO_WHILE (20x) | Gauge, Lamp |
| `knob-carrier-check` | FORK_JOIN | 4x Lamp, Gauge |
| `knob-signal-route` | Sequential | 4x Lamp in sequence |
| `knob-modulation` | DO_WHILE (30x) | Gauge oscillation |
| `knob-storm` | FORK_JOIN chaos | All widgets, full-panel alert |

## API endpoints

### Read

| Endpoint | Description |
|----------|-------------|
| `GET /` | Dashboard |
| `GET /control` | Admin control panel |
| `GET /events` | SSE stream (all real-time updates) |
| `GET /api/radio` | Now-playing + DJ status + stream start time |
| `GET /api/radio/history` | Last 50 tracks |
| `GET /api/state` | All instruments + ticker log |
| `GET /api/sysmetrics` | CPU %, memory %, event velocity |
| `GET /api/entropy` | Current entropy value (0-1) |
| `GET /stream` | Icecast stream proxy (avoids mixed-content) |

### Write (instrument state)

| Endpoint | Body |
|----------|------|
| `POST /api/lamp/{id}` | `{"state": "on\|off", "color": "green\|red\|amber\|blue"}` |
| `POST /api/gauge/{id}` | `{"value": 0-100}` |
| `POST /api/knob/{id}` | `{"value": 0-100}` |
| `POST /api/switch/{id}` | `{"position": "up\|down"}` |
| `POST /api/knife/{id}` | `{"position": "open\|closed"}` |
| `POST /api/coil/{id}` | `{"state": "on\|off"}` |
| `POST /api/heartbeat/{id}` | `{"response_ms": float, "status_code": int}` |
| `POST /api/signal/{id}` | `{"quality": 0-100, "dbm": int}` |
| `POST /api/blink/{channel}` | `{"color": "blue\|green\|amber\|red"}` |
| `POST /api/ticker` | `{"message": "...", "source": "..."}` |

All instrument endpoints accept an optional `_meta` object: `{"label": "...", "section": "..."}` which controls how the widget is labelled and grouped in the UI. Instruments are auto-registered on first POST.

### Admin

| Endpoint | Description |
|----------|-------------|
| `POST /api/storm` | Trigger knob-storm workflow |
| `POST /api/reset` | Clear all state and ticker |
| `DELETE /api/instrument/{id}` | Remove an instrument |

## Project structure

```
knob-conductor-panel/
  panel/
    main.py                       # FastAPI app + background pollers
    templates/
      index.html                  # Main dashboard (SSE, canvas rendering)
      control.html                # Admin control panel
    static/
      base.css                    # Layout (no colors — all CSS variables)
      themes/
        bladerunner.css           # Cyan neon theme
        steampunk.css             # Brass/wood theme
  workflows/
    register_workflows.py         # Conductor workflow definitions
  components/
    nowplaying-card.html          # Reusable now-playing component
  docs/
    systemd.md                    # Service setup
    apache-proxy.md               # HTTPS proxy config
  deploy.sh                       # Auto-deploy (cron, git pull, restart)
  requirements.txt                # Python deps (fastapi, uvicorn, httpx)
  .env.example                    # Config template
```

## Deployment

Deployed on **beyla** (10.100.0.2, RNA Lounge at Noisebridge) as `knob-panel.service` on port 8082. Reverse-proxied via Apache at `knob.nthmost.com`.

Continuous deployment: `deploy.sh` runs every minute via cron, pulls from `origin/main`, and restarts the service on changes.

## License

MIT
