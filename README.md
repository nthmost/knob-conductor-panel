# knob-conductor-panel

A real-time radio station monitoring panel built on [Conductor OSS](https://github.com/conductor-oss/conductor) and FastAPI. Designed for display on a dedicated screen at the station — no mouse or keyboard required.

Originally built for **KNOB 90.7 FM** at [Noisebridge](https://www.noisebridge.net/), San Francisco's hackerspace.

![Blade Runner theme](docs/screenshot.png)

## What it does

- **Now Playing** — live track info, artist, progress bar, source badge (AutoDJ vs live DJ), in-browser stream playback
- **Live DJ detection** — pulsing red badge when a DJ connects via Icecast/Shoutcast
- **Activity monitor** — scrolling log of track changes, DJ events, and Conductor workflow events
- **Instrument panel** — gauges, lamps, knife switches, and blinken-lights driven by Conductor workflows
- **Fake busyness** — a set of radio-themed Conductor workflows (RF scanning, carrier checks, signal routing, EBS drills) that keep the panel looking alive
- **Real data** — stream bitrate pulled live from Icecast, queue depth from your radio API, DJ status from Liquidsoap telnet

## Architecture

```
Icecast / Liquidsoap
       │
       ▼
  radio HTTP API  ──►  FastAPI panel (port 8082)  ──►  Browser (SSE)
                              │
                        SQLite (WAL)
                              │
                     Conductor OSS (port 8888)
                       workflows via cron
```

The panel backend:
- Polls the radio API every 3 seconds, broadcasts track changes over SSE
- Polls Liquidsoap telnet every 5 seconds for live DJ status
- Proxies the Icecast audio stream at `/stream` (avoids mixed-content issues when served over HTTPS)
- Receives HTTP POSTs from Conductor workflow tasks to update gauges, lamps, switches, etc.

## Requirements

- Python 3.11+
- [Conductor OSS](https://github.com/conductor-oss/conductor) (any persistence backend; MySQL/MariaDB recommended)
- Icecast 2.x streaming server
- Liquidsoap (optional — for live DJ detection via telnet)
- A radio HTTP API that returns now-playing JSON (see [API contract](#radio-api-contract))

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

This registers 8 radio-themed workflows. To fire them on a schedule, add them to crontab:

```cron
*/1  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-stream-pulse","version":1,"input":{}}' > /dev/null 2>&1
*/2  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-signal-route","version":1,"input":{}}' > /dev/null 2>&1
*/3  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-freq-scan","version":1,"input":{}}' > /dev/null 2>&1
*/5  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-carrier-check","version":1,"input":{}}' > /dev/null 2>&1
*/7  * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-modulation","version":1,"input":{}}' > /dev/null 2>&1
*/10 * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-storm","version":1,"input":{}}' > /dev/null 2>&1
*/15 * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-ebs-check","version":1,"input":{}}' > /dev/null 2>&1
1    * * * * curl -sf -X POST 'http://localhost:8888/api/workflow' -H 'Content-Type: application/json' -d '{"name":"knob-radio-sync","version":1,"input":{}}' > /dev/null 2>&1
```

### 4. Expose publicly (optional)

See [docs/apache-proxy.md](docs/apache-proxy.md) to proxy through Apache with Let's Encrypt TLS.

## Adapting for your station

### Radio API contract

The panel expects `GET $RADIO_API_URL/api/now-playing` to return:

```json
{
  "artist": "Daft Punk",
  "title": "Harder, Better, Faster, Stronger",
  "remaining": 183,
  "source": "AutoDJ",
  "next_source": "AutoDJ",
  "next_hour_fmt": "5pm"
}
```

This matches the format from [Liquidsoap's HTTP API](https://www.liquidsoap.info/) with a thin wrapper. If you use **AzuraCast**, you can adapt `radio_poller()` in `panel/main.py` to hit the AzuraCast Now Playing API instead.

### Live DJ detection

The panel opens a TCP connection to Liquidsoap's telnet server and sends `input.harbor.status`. If your Liquidsoap config uses a different input name, update `dj_watcher()` in `panel/main.py`.

If you don't use Liquidsoap, remove the `dj_watcher()` call from `lifespan()` — everything else will still work.

### Theming

The panel ships with a **Blade Runner** theme (dark cyan on near-black) and a **Steampunk** theme. Themes are CSS custom-property files in `panel/static/themes/`. You can add your own by following the same variable structure.

To lock to a single theme, set `data-theme="bladerunner"` on `<html>` in `panel/templates/index.html` and remove the theme switcher.

### Conductor workflows

The 8 bundled workflows in `workflows/register_workflows.py` are written for KNOB's setup but illustrate all the panel widget types:

| Workflow | Pattern | Widgets |
|---|---|---|
| `knob-stream-pulse` | Simple HTTP | Gauge, Lamp |
| `knob-radio-sync` | Simple HTTP | Gauge, Lamp |
| `knob-freq-scan` | DO_WHILE (20×) | Gauge, Lamp |
| `knob-carrier-check` | FORK_JOIN | 4× Lamp, Gauge |
| `knob-signal-route` | Sequential | 4× Lamp in sequence |
| `knob-modulation` | DO_WHILE (30×) | Gauge oscillation |
| `knob-ebs-check` | Sequential | Knife switch, Coil, Lamps, Ticker |
| `knob-storm` | FORK_JOIN chaos | Lamps, Gauges, Blinken, Ticker |

The panel exposes these HTTP endpoints for workflows to POST to:

| Endpoint | Body |
|---|---|
| `POST /api/lamp/{id}` | `{"state": "on\|off", "color": "green\|red\|amber\|blue"}` |
| `POST /api/gauge/{id}` | `{"value": 0-100}` |
| `POST /api/knife/{id}` | `{"position": "open\|closed"}` |
| `POST /api/coil/{id}` | `{"state": "on\|off"}` |
| `POST /api/blink/{channel}` | `{"color": "blue\|green\|amber\|red"}` |
| `POST /api/ticker` | `{"message": "...", "source": "..."}` |

All endpoints accept an optional `_meta` object: `{"label": "...", "section": "..."}` which controls how the widget is labelled and grouped in the UI.

## License

MIT
