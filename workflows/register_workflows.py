#!/usr/bin/env python3
"""Register KNOB panel workflows and schedules on beyla Conductor."""
import json, time
import urllib.request, urllib.error

BASE = "http://localhost:8888"
PANEL = "http://localhost:8082"
RADIO = "http://localhost:8081"
ICECAST = "http://localhost:8000"

def api(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
          headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} on {method} {path}: {e.read().decode()[:200]}")
        return None

def http_task(ref, uri, method="POST", body=None):
    t = {"name": f"http_{ref}", "taskReferenceName": ref,
         "type": "HTTP", "inputParameters": {
             "http_request": {"uri": uri, "method": method,
                              "connectionTimeOut": 3000, "readTimeOut": 3000}}}
    if body:
        t["inputParameters"]["http_request"]["body"] = body
    return t

def lamp(ref, id, state, color, label, section):
    return http_task(ref, f"{PANEL}/api/lamp/{id}", "POST",
        {"state": state, "color": color,
         "_meta": {"label": label, "section": section}})

def gauge(ref, id, value, label, section):
    return http_task(ref, f"{PANEL}/api/gauge/{id}", "POST",
        {"value": value, "_meta": {"label": label, "section": section}})

def blink(ref, channel, color="blue"):
    return http_task(ref, f"{PANEL}/api/blink/{channel}", "POST", {"color": color})

def ticker(ref, message, source="CONDUCTOR"):
    return http_task(ref, f"{PANEL}/api/ticker", "POST",
        {"message": message, "source": source})

def knife(ref, id, position, label, section):
    return http_task(ref, f"{PANEL}/api/knife/{id}", "POST",
        {"position": position, "_meta": {"label": label, "section": section}})

def coil(ref, id, state, label, section):
    return http_task(ref, f"{PANEL}/api/coil/{id}", "POST",
        {"state": state, "_meta": {"label": label, "section": section}})

def fork(ref, branches):
    return {"name": f"fork_{ref}", "taskReferenceName": f"fork_{ref}",
            "type": "FORK_JOIN",
            "forkTasks": [[t] if not isinstance(t, list) else t for t in branches]}

def join(ref, join_on):
    return {"name": f"join_{ref}", "taskReferenceName": f"join_{ref}",
            "type": "JOIN", "joinOn": join_on}

def do_while(ref, iterations, tasks):
    return {"name": f"loop_{ref}", "taskReferenceName": f"loop_{ref}",
            "type": "DO_WHILE",
            "loopCondition": f"if ($.loop_{ref}['iteration'] < {iterations}) {{ true; }} else {{ false; }}",
            "loopOver": tasks}

def schedule(name, wf_name, cron):
    return {"name": name, "cronExpression": cron, "paused": False,
            "startWorkflowRequest": {"name": wf_name, "version": 1, "input": {}}}

# ─────────────────────────────────────────────
# 1. knob-stream-pulse — real Icecast data
# ─────────────────────────────────────────────
wf_stream_pulse = {
    "name": "knob-stream-pulse", "version": 1,
    "description": "Poll Icecast, post real bitrate + stream health to panel",
    "tasks": [
        http_task("get_icecast", f"{ICECAST}/status-json.xsl", "GET"),
        fork("stream_display", [
            gauge("post_bitrate", "stream-bitrate",
                  "${get_icecast.output.response.body.icestats.source.audio_bitrate}",
                  "Bitrate (bps)", "STREAM"),
            lamp("post_stream_lamp", "stream-status", "on", "blue",
                 "Stream Status", "STREAM"),
            blink("blink_stream", 0, "blue"),
        ]),
        join("stream_display", ["post_bitrate", "post_stream_lamp", "blink_stream"]),
    ]
}

# ─────────────────────────────────────────────
# 2. knob-radio-sync — real radio API data
# ─────────────────────────────────────────────
wf_radio_sync = {
    "name": "knob-radio-sync", "version": 1,
    "description": "Poll radio API, post source lamp",
    "tasks": [
        http_task("get_nowplaying", f"{RADIO}/api/now-playing", "GET"),
        fork("radio_display", [
            lamp("post_source_lamp", "source-active", "on", "green",
                 "Source Active", "STREAM"),
            blink("blink_radio", 1, "green"),
        ]),
        join("radio_display", ["post_source_lamp", "blink_radio"]),
    ]
}

# ─────────────────────────────────────────────
# 3. knob-freq-scan — RF frequency scanner sweep
# ─────────────────────────────────────────────
wf_freq_scan = {
    "name": "knob-freq-scan", "version": 1,
    "description": "Simulated RF frequency scan — DO_WHILE gauge sweep",
    "tasks": [
        lamp("freq_scan_start", "freq-scan-active", "on", "amber",
             "Freq Scan", "RF MONITORING"),
        do_while("freq", 20, [
            http_task("sweep_step", f"{PANEL}/api/gauge/freq-scanner", "POST",
                {"value": "${loop_freq.output.iteration * 5}",
                 "_meta": {"label": "Frequency Scanner", "section": "RF MONITORING"}}),
            blink("sweep_blink", 4, "amber"),
        ]),
        gauge("freq_peak", "freq-scanner", 100,
              "Frequency Scanner", "RF MONITORING"),
        lamp("freq_scan_done", "freq-scan-active", "off", "amber",
             "Freq Scan", "RF MONITORING"),
        ticker("freq_scan_ticker", "📡  RF SCAN COMPLETE — 88–108 MHz nominal", "RF"),
    ]
}

# ─────────────────────────────────────────────
# 4. knob-carrier-check — transmitter array check
# ─────────────────────────────────────────────
wf_carrier_check = {
    "name": "knob-carrier-check", "version": 1,
    "description": "Check all transmitters, update SNR gauge",
    "tasks": [
        ticker("carrier_start", "🔬  CARRIER CHECK INITIATED", "RF"),
        fork("tx_array", [
            lamp("tx_alpha",   "tx-alpha",   "on", "green", "TX Alpha",   "TRANSMITTERS"),
            lamp("tx_bravo",   "tx-bravo",   "on", "green", "TX Bravo",   "TRANSMITTERS"),
            lamp("tx_charlie", "tx-charlie", "on", "blue",  "TX Charlie", "TRANSMITTERS"),
            lamp("tx_delta",   "tx-delta",   "on", "blue",  "TX Delta",   "TRANSMITTERS"),
            gauge("post_snr", "snr-meter", 87,
                  "SNR", "RF MONITORING"),
            [blink("blink_tx0", 8,  "green"),
             blink("blink_tx1", 9,  "green"),
             blink("blink_tx2", 10, "blue"),
             blink("blink_tx3", 11, "blue")],
        ]),
        join("tx_array", ["tx_alpha","tx_bravo","tx_charlie","tx_delta",
                          "post_snr","blink_tx3"]),
        ticker("carrier_done", "✅  CARRIER LOCKED — all transmitters nominal", "RF"),
    ]
}

# ─────────────────────────────────────────────
# 5. knob-signal-route — audio routing sequence
# ─────────────────────────────────────────────
wf_signal_route = {
    "name": "knob-signal-route", "version": 1,
    "description": "Simulated audio signal routing through node chain",
    "tasks": [
        lamp("route_input", "route-input", "on", "amber",
             "Input", "AUDIO ROUTING"),
        fork("route_nodes", [
            lamp("route_node_a", "route-node-a", "on", "blue",
                 "Node A", "AUDIO ROUTING"),
            lamp("route_node_b", "route-node-b", "on", "blue",
                 "Node B", "AUDIO ROUTING"),
        ]),
        join("route_nodes", ["route_node_a", "route_node_b"]),
        lamp("route_output", "route-output", "on", "green",
             "Output", "AUDIO ROUTING"),
        fork("route_blinks", [
            blink("rb0", 16, "amber"),
            blink("rb1", 17, "blue"),
            blink("rb2", 18, "blue"),
            blink("rb3", 19, "green"),
        ]),
        join("route_blinks", ["rb0","rb1","rb2","rb3"]),
        gauge("post_modulation", "modulation-idx", 73,
              "Modulation Index", "RF MONITORING"),
    ]
}

# ─────────────────────────────────────────────
# 6. knob-modulation — modulation index oscillation
# ─────────────────────────────────────────────
wf_modulation = {
    "name": "knob-modulation", "version": 1,
    "description": "Modulation index sweep and analysis",
    "tasks": [
        ticker("mod_start", "📊  MODULATION ANALYSIS STARTED", "RF"),
        do_while("mod", 30, [
            http_task("mod_step", f"{PANEL}/api/gauge/modulation-idx", "POST",
                {"value": "${loop_mod.output.iteration * 3 + 10}",
                 "_meta": {"label": "Modulation Index", "section": "RF MONITORING"}}),
        ]),
        gauge("mod_settle", "modulation-idx", 78,
              "Modulation Index", "RF MONITORING"),
        ticker("mod_done", "📊  MODULATION NOMINAL — 78% index", "RF"),
    ]
}

# ─────────────────────────────────────────────
# 7. knob-storm — big visual chaos burst
# ─────────────────────────────────────────────
wf_storm = {
    "name": "knob-storm", "version": 1,
    "description": "Full-panel chaos burst — all channels fire",
    "tasks": [
        fork("storm_burst", [
            # RF instruments
            gauge("storm_freq",   "freq-scanner",  95, "Frequency Scanner", "RF MONITORING"),
            gauge("storm_snr",    "snr-meter",      42, "SNR",              "RF MONITORING"),
            gauge("storm_mod",    "modulation-idx", 99, "Modulation Index", "RF MONITORING"),
            # transmitters
            lamp("storm_tx_a", "tx-alpha",   "on", "red",   "TX Alpha",   "TRANSMITTERS"),
            lamp("storm_tx_b", "tx-bravo",   "on", "red",   "TX Bravo",   "TRANSMITTERS"),
            lamp("storm_tx_c", "tx-charlie", "on", "amber", "TX Charlie", "TRANSMITTERS"),
            lamp("storm_tx_d", "tx-delta",   "on", "amber", "TX Delta",   "TRANSMITTERS"),
            # routing
            lamp("storm_ri",  "route-input",  "on", "red", "Input",  "AUDIO ROUTING"),
            lamp("storm_rna", "route-node-a", "on", "red", "Node A", "AUDIO ROUTING"),
            lamp("storm_rnb", "route-node-b", "on", "red", "Node B", "AUDIO ROUTING"),
            lamp("storm_ro",  "route-output", "on", "red", "Output", "AUDIO ROUTING"),
            # blink storm across all channels
            [blink("bs0",  20, "red"),  blink("bs1",  21, "red"),
             blink("bs2",  22, "amber"),blink("bs3",  23, "amber"),
             blink("bs4",  28, "blue"), blink("bs5",  29, "blue"),
             blink("bs6",  30, "red"),  blink("bs7",  31, "amber")],
        ]),
        join("storm_burst", [
            "storm_freq","storm_snr","storm_mod",
            "storm_tx_a","storm_tx_b","storm_tx_c","storm_tx_d",
            "storm_ri","storm_rna","storm_rnb","storm_ro","bs7"
        ]),
        ticker("storm_ticker", "⚡  SIGNAL ANOMALY DETECTED — SYSTEMS RECOVERING", "ALERT"),
        # recover transmitters to nominal
        fork("storm_recover", [
            lamp("rec_tx_a", "tx-alpha",   "on", "green", "TX Alpha",   "TRANSMITTERS"),
            lamp("rec_tx_b", "tx-bravo",   "on", "green", "TX Bravo",   "TRANSMITTERS"),
            lamp("rec_tx_c", "tx-charlie", "on", "blue",  "TX Charlie", "TRANSMITTERS"),
            lamp("rec_tx_d", "tx-delta",   "on", "blue",  "TX Delta",   "TRANSMITTERS"),
            lamp("rec_ri",  "route-input",  "on", "amber", "Input",  "AUDIO ROUTING"),
            lamp("rec_ro",  "route-output", "on", "green", "Output", "AUDIO ROUTING"),
            gauge("rec_snr",  "snr-meter",      85, "SNR",              "RF MONITORING"),
            gauge("rec_mod",  "modulation-idx", 75, "Modulation Index", "RF MONITORING"),
        ]),
        join("storm_recover", ["rec_tx_a","rec_tx_b","rec_tx_c","rec_tx_d",
                               "rec_ri","rec_ro","rec_snr","rec_mod"]),
        ticker("storm_recover_ticker", "✅  SYSTEMS NOMINAL — BROADCAST RESTORED", "ALERT"),
    ]
}

# ─────────────────────────────────────────────
# Register everything
# ─────────────────────────────────────────────
workflows = [
    wf_stream_pulse, wf_radio_sync, wf_freq_scan, wf_carrier_check,
    wf_signal_route, wf_modulation, wf_storm
]

schedules = [
    schedule("knob-stream-pulse-30s",  "knob-stream-pulse",  "0/30 * * * * ?"),
    schedule("knob-radio-sync-1m",     "knob-radio-sync",    "15 * * * * ?"),
    schedule("knob-freq-scan-3m",      "knob-freq-scan",     "0 */3 * * * ?"),
    schedule("knob-carrier-check-5m",  "knob-carrier-check", "0 */5 * * * ?"),
    schedule("knob-signal-route-2m",   "knob-signal-route",  "30 */2 * * * ?"),
    schedule("knob-modulation-7m",     "knob-modulation",    "0 */7 * * * ?"),
    schedule("knob-storm-10m",         "knob-storm",         "0 */10 * * * ?"),
]

print("=== Registering workflows ===")
for wf in workflows:
    wf.setdefault("ownerEmail", "knob@noisebridge.net")
    r = api("POST", "/api/metadata/workflow", wf)
    status = "OK" if r is not None else "FAIL"
    print(f"  [{status}] {wf['name']}")
    time.sleep(0.2)

print("\nDone (schedules handled by system cron).")
