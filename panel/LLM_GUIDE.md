# LLM_GUIDE.md — Modifying the KNOB Systems Monitor

You found this because you're trying to *modify* the KNOB Systems Panel
(not just call its API). Read this in full before changing code — there
are a couple of non-obvious deployment + state gotchas that have bitten
prior visitors.

---

## What this is

A FastAPI + SSE + SQLite control panel for KNOB 87.9 FM at Noisebridge.
It renders a real-time radio/infra dashboard, accepts external systems
pushing instrument state via `/api/<kind>/{id}`, and broadcasts changes
to connected browsers over SSE (`/events`).

- **Repo:** https://github.com/nthmost/knob-conductor-panel
- **Backend:** `panel/main.py` (single-file FastAPI app, ~1200 lines)
- **Frontend:** `panel/templates/index.html` (single-file canvas + JS)
- **Static:** `panel/static/{base.css, themes/<name>.css, og-image.svg, …}`

---

## Deploy + runtime — read this before debugging

The deployed instance lives on `beyla.local` (NB server). **The pieces
are spread across non-obvious paths and there's an orphan unit you
must not confuse with the live one:**

| What | Where on beyla |
|---|---|
| Live source code | `/home/nthmost/projects/git/knob-conductor-panel/panel/` (yes, the git clone itself — not a build artifact) |
| Live systemd unit | **`knob-panel.service`** (NOT `panel.service` — the latter is a stale orphan from a prior setup) |
| Live SQLite DB | **`/home/nthmost/panel-data/panel.db`** (NOT `/home/nthmost/panel/panel.db` — the latter is a stale leftover, same name different dir) |
| Env override | `Environment=PANEL_DB=/home/nthmost/panel-data/panel.db` in the unit |
| Continuous deploy | `~/projects/git/knob-conductor-panel/deploy.sh` runs every minute via cron — `git pull`s `origin/main`, and `systemctl restart knob-panel.service` if HEAD moved |

**Workflow for code changes:**
1. Edit, commit, push to `origin/main` of the repo
2. Within ~60s, beyla's cron pulls + restarts the service
3. If you need to test against the live DB without committing, edit
   directly on beyla and `sudo systemctl restart knob-panel.service`
   — but the next cron run will overwrite your edit if you didn't push

---

## The instrument model

Widgets on the panel are *not* statically defined anywhere in the
codebase. They are **dynamic instruments** that live in the `instruments`
table of `panel.db`. They appear in two ways:

1. **External pushers register them.** Anything POSTing to
   `/api/<kind>/{id}` with a body containing `"_meta": {"label": "...",
   "section": "..."}` will auto-create the instrument via the
   `ensure_instrument()` helper in `main.py`. Kinds include `lamp`,
   `gauge`, `knob`, `switch`, `knife`, `coil`, `heartbeat`, `signal`.
2. **Stored in `panel.db`.** Once registered, they persist across
   panel restarts because the DB persists.

**Implication for modification:** if you want to remove or relabel a
widget, you must do **both**:
- Update `panel.db` (delete row, or change `section`/`label`/`created_at`)
- Find and update the **source that registers it**, otherwise the next
  push will recreate or revert it. Sources are often Conductor workflow
  definitions or external scripts on beyla (search the network for
  `/api/<kind>/<the-id>` POSTs in journalctl).

**Sections** are display-only groupings; widget *order* within a
section is `ORDER BY created_at` in the SQL. To reorder, rewrite
`created_at` values directly.

---

## Common operations

### Add a new widget (from outside the panel)

```bash
curl -X POST http://localhost:8082/api/lamp/my-widget \
  -H 'Content-Type: application/json' \
  -d '{"state": "on", "color": "green",
       "_meta": {"label": "My Widget", "section": "NB INFRA"}}'
```

That single POST auto-registers AND sets state. The widget appears
instantly in any connected `/events` SSE client.

### Remove a widget

```bash
curl -X DELETE http://localhost:8082/api/instrument/my-widget
```

**But also stop whatever was POSTing to it** — otherwise the next push
recreates it. The Discord-channel pattern: look at recent panel
journalctl for `POST /api/.../<id>` to find the caller.

### Change a widget's section or label

The naive `UPDATE instruments SET section = …` works *until* the next
external push, which re-runs `ensure_instrument` and reverts the
section back to whatever the pusher specified. Fix: update the source
pusher (typically a Conductor workflow definition) first, then SQL the
DB to align immediately. **This pitfall is documented because it's
already cost two debugging sessions.**

### Reorder widgets within a section

`UPDATE instruments SET created_at = <new-ts> WHERE id = …` then
restart the service to push fresh state to SSE clients. Lower
`created_at` = appears earlier.

### Find what the panel is doing in real time

```bash
journalctl -u knob-panel.service -f
```

You'll see every API hit + every state change.

---

## Frontend gotchas (templates/index.html)

- **Single file**, currently ~1400+ lines. Mostly canvas drawing,
  EventSource handling, theme switching, audio meter UX.
- **Themes** live in `static/themes/<name>.css`. The default is set
  via `data-theme="..."` on `<html>` plus the matching `<link>` tag.
- **Header has a secret `◈` diamond glyph** that links to `/control`
  (interactive control panel). Don't remove it without finding a
  replacement entry point.
- **Per-instrument rendering** is data-driven from the `state` field
  of the SSE event — new instrument kinds need both backend
  (`@app.post("/api/<kind>/{id}")`) and frontend rendering branches.

---

## What NOT to touch without thinking

- **`/api` discovery endpoint and the LLM greeting in `<head>` of
  `index.html`** — these are how *other* LLMs discover this panel for
  the radio API use case. Don't degrade them.
- **`static/og-image.svg`** — used as the social-link preview thumbnail.
  Contains some legacy labels ("BITRATE", "EBS") that no longer have
  corresponding widgets; those are intentional in the marketing image
  even after the widgets were retired. Don't "fix" by deleting unless
  you also redesign the image.
- **`templates/control.html`** + `static/robots.txt` — exist in the
  repo but aren't actively deployed to beyla. Preserve them; they're
  the contract for the secret control panel.

---

## Quick-reference: the gotcha cheat sheet

| Symptom | Likely cause |
|---|---|
| You edited `~/panel/main.py` on beyla, nothing changed | That's the stale dir. Edit `~/projects/git/knob-conductor-panel/panel/main.py` (or push to repo, deploy.sh auto-deploys). |
| You changed a widget's section, it snapped back | An external pusher re-registers with the old section. Fix the pusher (Conductor workflow def or external script). |
| Widget disappeared but came back | Same as above — something is re-POSTing the widget every N seconds. |
| `panel.service` won't stay up | That's the orphan unit. Stop/disable it. The live one is `knob-panel.service`. |
| Local DB edits don't appear in API | You're editing `~/panel/panel.db` (stale). The live DB is `~/panel-data/panel.db`. |
| Bumped created_at, order didn't change | The service caches initial state per SSE client connection. Restart `knob-panel.service` to re-broadcast. |

---

## Where to find more context

- Memory notes (if you're Claude with auto-memory):
  `~/.claude/projects/-Users-nthmost-projects-nthmost-systems/memory/project_beyla_repo_map.md`
- Radio API contract (separate repo): https://github.com/nthmost/nbradio
- Conductor workflows definitions: `workflows/` in this repo + the
  `register_*` scripts in `~/projects/noisebridge-ha/` on beyla

If you do something destructive (revert, restart loop, etc.), check
`git reflog` immediately — the repo is auto-deployed, so a bad push
will land on beyla in <60s but is also easily revertable.
