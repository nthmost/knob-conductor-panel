"""
Control Panel — FastAPI backend
SSE + SQLite state, instrument auto-registration, Conductor write-back,
Radio now-playing poller, play history, DJ takeover detection
"""
import asyncio
import hashlib
import json
import os
import sqlite3
import time
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

CONDUCTOR_URL = os.getenv("CONDUCTOR_URL", "http://localhost:8888")
DB_PATH       = os.getenv("PANEL_DB", "/home/nthmost/panel/panel.db")
PANEL_DIR     = os.path.dirname(os.path.abspath(__file__))

# Radio integration (disabled if RADIO_API_URL is empty string)
RADIO_API_URL      = os.getenv("RADIO_API_URL", "http://localhost:8081")
LIQUIDSOAP_HOST    = os.getenv("LIQUIDSOAP_HOST", "localhost")
LIQUIDSOAP_PORT    = int(os.getenv("LIQUIDSOAP_PORT", "1234"))
RADIO_HISTORY_MAX  = 50
TICKER_MAX         = 200

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def db_connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def db_init():
    with db_connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS instruments (
                id            TEXT PRIMARY KEY,
                kind          TEXT NOT NULL,
                label         TEXT,
                section       TEXT,
                conductor_ref TEXT,
                created_at    REAL DEFAULT (unixepoch())
            );
            CREATE TABLE IF NOT EXISTS state (
                instrument_id TEXT PRIMARY KEY REFERENCES instruments(id),
                value         TEXT NOT NULL,
                updated_at    REAL DEFAULT (unixepoch())
            );
            CREATE TABLE IF NOT EXISTS ticker_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                source  TEXT,
                ts      REAL DEFAULT (unixepoch())
            );
        """)

# ---------------------------------------------------------------------------
# SSE broker
# ---------------------------------------------------------------------------

class SSEBroker:
    def __init__(self):
        self._queues: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._queues.remove(q)
        except ValueError:
            pass

    async def broadcast(self, event_type: str, data: dict):
        msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
        dead = []
        for q in list(self._queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

broker = SSEBroker()

# ---------------------------------------------------------------------------
# Radio state
# ---------------------------------------------------------------------------

_radio_now: dict = {}
_radio_history: deque = deque(maxlen=RADIO_HISTORY_MAX)
_dj_state: dict = {"connected": False, "client": None}

async def _liquidsoap_cmd(cmd: str) -> str:
    """Send one command to Liquidsoap telnet, return first response line."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(LIQUIDSOAP_HOST, LIQUIDSOAP_PORT), timeout=2.0
        )
        writer.write(f"{cmd}\nquit\n".encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(2048), timeout=2.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        lines = raw.decode(errors="replace").strip().splitlines()
        return lines[0].strip() if lines else ""
    except Exception:
        return ""

async def radio_poller():
    """Poll radio API; detect track changes and source switches."""
    global _radio_now
    prev_track = None   # "artist|title" key
    prev_source = None

    async with httpx.AsyncClient() as client:
        while True:
            try:
                resp = await client.get(
                    f"{RADIO_API_URL}/api/now-playing", timeout=3.0
                )
                data = resp.json()
                _radio_now = data

                artist = data.get("artist", "")
                title  = data.get("title", "")
                source = data.get("source", "")
                track_key = f"{artist}|{title}"

                # New track started
                if prev_track is not None and track_key != prev_track:
                    pa, pt = prev_track.split("|", 1)
                    entry = {
                        "artist": pa, "title": pt,
                        "source": prev_source or "",
                        "ts": time.time(),
                    }
                    _radio_history.appendleft(entry)
                    label = f"{pa} — {pt}" if pa else pt
                    msg = f"♫  {label}"
                    with db_connect() as conn:
                        add_ticker(conn, msg, prev_source or "RADIO")
                        conn.commit()
                    await broker.broadcast("ticker", {
                        "message": msg, "source": prev_source or "RADIO",
                        "ts": time.time(),
                    })
                    await broker.broadcast("radio_track", data)

                # Source changed (e.g. AUTODJ → Pandora's Box)
                if prev_source is not None and source != prev_source:
                    msg = f"📡  {prev_source}  →  {source}"
                    with db_connect() as conn:
                        add_ticker(conn, msg, "SCHEDULE")
                        conn.commit()
                    await broker.broadcast("ticker", {
                        "message": msg, "source": "SCHEDULE", "ts": time.time(),
                    })

                prev_track  = track_key
                prev_source = source

            except Exception:
                pass

            await asyncio.sleep(3)

async def dj_watcher():
    """Watch Liquidsoap harbor for live DJ connect/disconnect."""
    global _dj_state

    while True:
        status = await _liquidsoap_cmd("input.harbor.status")
        was_connected = _dj_state["connected"]
        # Liquidsoap returns "no source client connected" when idle
        is_connected = bool(status) and status != "no source client connected"

        if is_connected and not was_connected:
            _dj_state = {"connected": True, "client": status}
            msg = "🎙  LIVE DJ on air"
            with db_connect() as conn:
                add_ticker(conn, msg, "DJ")
                conn.commit()
            await broker.broadcast("ticker", {
                "message": msg, "source": "DJ", "ts": time.time(),
            })
            await broker.broadcast("radio_dj", {"connected": True, "client": status})

        elif not is_connected and was_connected:
            _dj_state = {"connected": False, "client": None}
            msg = "🎙  Live DJ signed off — back to schedule"
            with db_connect() as conn:
                add_ticker(conn, msg, "DJ")
                conn.commit()
            await broker.broadcast("ticker", {
                "message": msg, "source": "DJ", "ts": time.time(),
            })
            await broker.broadcast("radio_dj", {"connected": False})

        await asyncio.sleep(5)

# ---------------------------------------------------------------------------
# Milestone detection
# ---------------------------------------------------------------------------

_event_log: deque = deque(maxlen=200)
_milestone_cooldowns: dict = {}

MILESTONES = [
    {
        "id": "reactor_online",
        "message": "REACTOR ONLINE — Main switch closed → Coil energized",
        "color": "red",
        "steps": [
            {"kind": "knife", "key": "position", "val": "closed"},
            {"kind": "coil",  "key": "state",    "val": "on"},
        ],
        "window_secs": 30,
        "cooldown_secs": 60,
    },
    {
        "id": "systems_nominal",
        "message": "ALL SYSTEMS NOMINAL — Switch engaged → Status lamp green",
        "color": "green",
        "steps": [
            {"kind": "switch", "key": "position", "val": "up"},
            {"kind": "lamp",   "key": "color",    "val": "green"},
        ],
        "window_secs": 20,
        "cooldown_secs": 90,
    },
    {
        "id": "pipeline_active",
        "message": "PIPELINE ACTIVE — Intake → Processing → Output streaming",
        "color": "amber",
        "steps": [
            {"kind": "lamp",  "key": "state", "val": "on"},
            {"kind": "gauge", "key": None,    "val": None},
            {"kind": "lamp",  "key": "state", "val": "on"},
        ],
        "window_secs": 10,
        "cooldown_secs": 120,
    },
]

async def _fire_milestone(ms_id: str, message: str, color: str):
    _milestone_cooldowns[ms_id] = time.time()
    await broker.broadcast("milestone", {
        "id": ms_id, "message": message, "color": color, "ts": time.time()
    })
    with db_connect() as conn:
        add_ticker(conn, f"★ {message}", "SEQUENCER")
        conn.commit()
    await broker.broadcast("ticker", {
        "message": f"★ {message}", "source": "SEQUENCER", "ts": time.time()
    })

async def check_milestones(kind: str, value: dict):
    now = time.time()
    _event_log.append({"ts": now, "kind": kind, "value": value})
    for ms in MILESTONES:
        if now - _milestone_cooldowns.get(ms["id"], 0) < ms["cooldown_secs"]:
            continue
        steps = ms["steps"]
        window_events = [e for e in _event_log if now - e["ts"] <= ms["window_secs"]]
        matched = 0
        for event in window_events:
            step = steps[matched]
            if event["kind"] == step["kind"]:
                if step["key"] is None or event["value"].get(step["key"]) == step["val"]:
                    matched += 1
            if matched == len(steps):
                break
        if matched == len(steps):
            await _fire_milestone(ms["id"], ms["message"], ms["color"])

async def check_gauge_threshold(instrument_id: str, value: dict):
    raw = value.get("value")
    if raw is None:
        return
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return
    if v >= 95:
        ms_id = f"gauge_critical_{instrument_id}"
        if time.time() - _milestone_cooldowns.get(ms_id, 0) > 120:
            await _fire_milestone(
                ms_id, f"CRITICAL LOAD: {instrument_id} at {int(v)}%", "red"
            )

# ---------------------------------------------------------------------------
# System metrics (CPU, memory, workflow velocity)
# ---------------------------------------------------------------------------

_cpu_sample: tuple = (0, 0)
_perf_log: deque = deque(maxlen=500)

def _read_cpu() -> float:
    global _cpu_sample
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
        vals = list(map(int, parts))
        idle  = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        prev_total, prev_idle = _cpu_sample
        _cpu_sample = (total, idle)
        dt = total - prev_total
        di = idle - prev_idle
        return round((1 - di / dt) * 100, 1) if dt > 0 else 0.0
    except Exception:
        return 0.0

def _read_mem() -> float:
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k.strip()] = int(v.split()[0])
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", 0)
        return round((total - avail) / total * 100, 1)
    except Exception:
        return 0.0

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    db_init()
    if RADIO_API_URL:
        asyncio.create_task(radio_poller())
        asyncio.create_task(dj_watcher())
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(PANEL_DIR, "static")), name="static")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def channel_for(name: str) -> int:
    return int(hashlib.md5(name.encode()).hexdigest(), 16) % 32

def ensure_instrument(conn, id: str, kind: str, meta: dict):
    existing = conn.execute(
        "SELECT id FROM instruments WHERE id = ?", (id,)
    ).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO instruments (id, kind, label, section, conductor_ref) VALUES (?,?,?,?,?)",
            (id, kind, meta.get("label", id), meta.get("section"), meta.get("conductor_ref"))
        )
        return True
    return False

def set_state(conn, id: str, value: dict):
    conn.execute(
        "INSERT INTO state (instrument_id, value, updated_at) VALUES (?,?,unixepoch()) "
        "ON CONFLICT(instrument_id) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (id, json.dumps(value))
    )

def get_state(conn, id: str) -> dict:
    row = conn.execute("SELECT value FROM state WHERE instrument_id = ?", (id,)).fetchone()
    return json.loads(row["value"]) if row else {}

def add_ticker(conn, message: str, source: str | None):
    conn.execute(
        "INSERT INTO ticker_log (message, source) VALUES (?,?)", (message, source)
    )
    conn.execute(
        "DELETE FROM ticker_log WHERE id NOT IN "
        "(SELECT id FROM ticker_log ORDER BY id DESC LIMIT ?)", (TICKER_MAX,)
    )

# ---------------------------------------------------------------------------
# Routes — page
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(PANEL_DIR, "templates/index.html")) as f:
        return f.read()

# ---------------------------------------------------------------------------
# Routes — SSE
# ---------------------------------------------------------------------------

@app.get("/events")
async def events(request: Request):
    q = broker.subscribe()
    async def stream():
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            broker.unsubscribe(q)
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})

# ---------------------------------------------------------------------------
# Routes — radio
# ---------------------------------------------------------------------------

@app.get("/api/radio")
async def get_radio():
    return {**_radio_now, "dj": _dj_state}

@app.get("/api/radio/history")
async def get_radio_history():
    return list(_radio_history)

# ---------------------------------------------------------------------------
# Routes — state snapshot
# ---------------------------------------------------------------------------

@app.get("/api/state")
def api_state():
    with db_connect() as conn:
        instruments = [dict(r) for r in conn.execute(
            "SELECT i.*, s.value as state_json FROM instruments i "
            "LEFT JOIN state s ON s.instrument_id = i.id "
            "ORDER BY i.created_at"
        ).fetchall()]
        for inst in instruments:
            inst["state"] = json.loads(inst.pop("state_json") or "{}")
        ticker = [dict(r) for r in conn.execute(
            "SELECT message, source, ts FROM ticker_log ORDER BY id DESC LIMIT 50"
        ).fetchall()]
    return {"instruments": instruments, "ticker": ticker}

# ---------------------------------------------------------------------------
# Routes — sysmetrics
# ---------------------------------------------------------------------------

@app.get("/api/sysmetrics")
async def sysmetrics():
    cpu = _read_cpu()
    mem = _read_mem()
    now = time.time()
    window5 = [t for t in _perf_log if now - t <= 5.0]
    velocity = round(len(window5) / 5.0, 2)
    v_now  = len([t for t in _perf_log if now - t <= 2.5]) / 2.5
    v_prev = len([t for t in _perf_log if 2.5 < now - t <= 5.0]) / 2.5
    accel  = round(v_now - v_prev, 2)
    return {"cpu": cpu, "mem": mem, "velocity": velocity, "accel": accel}

# ---------------------------------------------------------------------------
# Routes — instrument updates
# ---------------------------------------------------------------------------

async def _update(kind: str, id: str, body: dict):
    meta = body.pop("_meta", {})
    with db_connect() as conn:
        created = ensure_instrument(conn, id, kind, meta)
        set_state(conn, id, body)
        inst_row = conn.execute("SELECT * FROM instruments WHERE id = ?", (id,)).fetchone()
        label = dict(inst_row)["label"] if inst_row else id
        conn.commit()
    if created:
        await broker.broadcast("instrument_added", {
            "id": id, "kind": kind, "label": label, "section": meta.get("section"),
        })
    await broker.broadcast("state_changed", {"id": id, "kind": kind, "value": body})
    await broker.broadcast("blink", {"channel": channel_for(id), "color": body.get("color", "amber")})
    _perf_log.append(time.time())
    await check_milestones(kind, body)
    if kind == "gauge":
        await check_gauge_threshold(id, body)
    return {"ok": True, "created": created, "channel": channel_for(id)}

@app.post("/api/lamp/{id}")
async def post_lamp(id: str, request: Request):
    return await _update("lamp", id, await request.json())

@app.post("/api/gauge/{id}")
async def post_gauge(id: str, request: Request):
    return await _update("gauge", id, await request.json())

@app.post("/api/knob/{id}")
async def post_knob(id: str, request: Request):
    return await _update("knob", id, await request.json())

@app.post("/api/switch/{id}")
async def post_switch(id: str, request: Request):
    return await _update("switch", id, await request.json())

@app.post("/api/knife/{id}")
async def post_knife(id: str, request: Request):
    return await _update("knife", id, await request.json())

@app.post("/api/coil/{id}")
async def post_coil(id: str, request: Request):
    return await _update("coil", id, await request.json())

@app.post("/api/ticker")
async def post_ticker(request: Request):
    body = await request.json()
    message = body.get("message", "")
    source  = body.get("source")
    milestone = body.get("milestone", False)
    with db_connect() as conn:
        add_ticker(conn, message, source)
        conn.commit()
    await broker.broadcast("ticker", {"message": message, "source": source,
                                       "ts": time.time(), "milestone": milestone})
    return {"ok": True}

@app.post("/api/blink/{channel}")
async def post_blink(channel: int, request: Request):
    body = await request.json()
    await broker.broadcast("blink", {
        "channel": max(0, min(31, channel)),
        "color": body.get("color", "amber"),
    })
    return {"ok": True}

@app.get("/api/knob/{id}")
def get_knob(id: str):
    with db_connect() as conn:
        return get_state(conn, id) or {"value": 50}

# ---------------------------------------------------------------------------
# Routes — write-back to Conductor
# ---------------------------------------------------------------------------

@app.post("/api/action/switch/{id}")
async def action_switch(id: str, request: Request):
    body = await request.json()
    position = body.get("position", "down")
    with db_connect() as conn:
        row = conn.execute("SELECT conductor_ref FROM instruments WHERE id = ?", (id,)).fetchone()
        ref = dict(row)["conductor_ref"] if row else None
        set_state(conn, id, {"position": position})
        conn.commit()
    val = {"position": position}
    await broker.broadcast("state_changed", {"id": id, "kind": "switch", "value": val})
    await check_milestones("switch", val)
    if ref:
        action = "resume" if position == "up" else "pause"
        try:
            async with httpx.AsyncClient() as client:
                await client.put(f"{CONDUCTOR_URL}/api/scheduler/schedules/{ref}/{action}", timeout=4.0)
        except Exception as e:
            return {"ok": True, "conductor_error": str(e)}
        return {"ok": True, "conductor_ref": ref, "action": action}
    return {"ok": True, "conductor_ref": ref}

@app.post("/api/action/knife/{id}")
async def action_knife(id: str, request: Request):
    body = await request.json()
    position = body.get("position", "open")
    with db_connect() as conn:
        row = conn.execute("SELECT conductor_ref FROM instruments WHERE id = ?", (id,)).fetchone()
        ref = dict(row)["conductor_ref"] if row else None
        set_state(conn, id, {"position": position})
        conn.commit()
    val = {"position": position}
    await broker.broadcast("state_changed", {"id": id, "kind": "knife", "value": val})
    await check_milestones("knife", val)
    if ref:
        action = "resume" if position == "closed" else "pause"
        try:
            async with httpx.AsyncClient() as client:
                await client.put(f"{CONDUCTOR_URL}/api/scheduler/schedules/{ref}/{action}", timeout=4.0)
        except Exception as e:
            return {"ok": True, "conductor_error": str(e)}
        return {"ok": True, "conductor_ref": ref, "action": action}
    return {"ok": True, "conductor_ref": None}

@app.post("/api/action/trigger/{id}")
async def action_trigger(id: str, request: Request):
    body = await request.json()
    with db_connect() as conn:
        row = conn.execute("SELECT conductor_ref FROM instruments WHERE id = ?", (id,)).fetchone()
        ref = dict(row)["conductor_ref"] if row else None
    if ref:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(f"{CONDUCTOR_URL}/api/workflow/{ref}",
                                      json=body.get("input", {}), timeout=4.0)
            return {"ok": True, "workflow_id": r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "no conductor_ref set"}

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.post("/api/reset")
async def reset():
    with db_connect() as conn:
        conn.execute("DELETE FROM state")
        conn.execute("DELETE FROM ticker_log")
        conn.commit()
    await broker.broadcast("reset", {})
    return {"ok": True}

@app.delete("/api/instrument/{id}")
async def delete_instrument(id: str):
    with db_connect() as conn:
        conn.execute("DELETE FROM state WHERE instrument_id = ?", (id,))
        conn.execute("DELETE FROM instruments WHERE id = ?", (id,))
        conn.commit()
    await broker.broadcast("instrument_removed", {"id": id})
    return {"ok": True}




# ---------------------------------------------------------------------------
# Icecast stream proxy — avoids mixed-content issues when served over HTTPS
# ---------------------------------------------------------------------------
ICECAST_STREAM_URL = os.getenv("ICECAST_STREAM_URL", "http://localhost:8000/stream.ogg")

@app.get("/stream")
async def proxy_stream(request: Request):
    async def _iter(upstream):
        async for chunk in upstream.aiter_bytes(chunk_size=8192):
            yield chunk

    client = httpx.AsyncClient(timeout=None)
    upstream = await client.send(
        httpx.Request("GET", ICECAST_STREAM_URL,
                      headers={"Icy-MetaData": "0"}),
        stream=True,
    )
    media_type = upstream.headers.get("content-type", "audio/mpeg")
    return StreamingResponse(
        _iter(upstream),
        media_type=media_type,
        headers={"Cache-Control": "no-cache",
                 "X-Content-Type-Options": "nosniff"},
    )
