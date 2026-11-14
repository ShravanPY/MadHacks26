# Dig In - Linux side of the UNO Q. All analysis happens here.
#
# Wrist sensor -> tremor score (3-8 Hz shaking) + bite detection (wrist tilt)
# Meal sessions -> bites, seconds per bite, tremor at rest vs while eating

import json
import math
import os
import threading
import time
from collections import deque

from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI

G = 9.80665
FS = 62.5                   # samples per second (sketch sends every 16 ms)
WINDOW = 96                 # about 1.5 seconds of data per tremor reading
STEP = 8                    # new tremor reading about 8 times a second
TREMOR_LOW_HZ, TREMOR_HIGH_HZ = 3.0, 8.0

# A tremor is rhythmic: most of the shaking sits at one frequency. Ordinary
# movement (lifting the hand, bumping the table) is spread across many.
TREMOR_MIN = 0.15           # shaking must be at least this strong (m/s^2)
RHYTHM_MIN = 0.7            # share of the shaking held by the two strongest neighbouring frequencies
ON_READINGS = 6             # this many readings in a row (about 0.8 s) to switch on
OFF_READINGS = 4            # this many calm readings in a row (about 0.5 s) to switch off

BITE_UP_DEG = 35.0          # wrist tilted this far from rest = hand going to mouth
BITE_DOWN_DEG = 20.0        # back under this = bite finished
BITE_MIN_S, BITE_MAX_S = 0.4, 8.0

CHANGE_FLAG = 0.25          # flag when 25% worse than the person's baseline

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "sessions.json")

ui = WebUI()
lock = threading.Lock()

# ---------- tremor analysis (small DFT, no extra libraries) ----------

BINS = [k for k in range(1, WINDOW // 2)
        if TREMOR_LOW_HZ <= k * FS / WINDOW <= TREMOR_HIGH_HZ]
TABLE = {k: [(math.cos(2 * math.pi * k * n / WINDOW), math.sin(2 * math.pi * k * n / WINDOW))
             for n in range(WINDOW)] for k in BINS}

buf = deque(maxlen=WINDOW)  # (ax, ay, az)
since_last = 0


def tremor_reading(samples):
    """Return (strength in m/s^2 RMS in the tremor band, peak frequency in Hz, rhythm 0-1)."""
    power = {k: 0.0 for k in BINS}
    for axis in range(3):
        xs = [s[axis] for s in samples]
        mean = sum(xs) / len(xs)
        xs = [x - mean for x in xs]
        for k in BINS:
            re = im = 0.0
            for x, (c, s) in zip(xs, TABLE[k]):
                re += x * c
                im += x * s
            amp = 2.0 * math.hypot(re, im) / WINDOW
            power[k] += amp * amp / 2.0
    peak = max(power, key=power.get)
    total = sum(power.values())
    pair = max(power[a] + power[b] for a, b in zip(BINS, BINS[1:]))
    return math.sqrt(total), peak * FS / WINDOW, (pair / total if total > 0 else 0.0)


# ---------- bite detection (wrist tilt away from the resting position) ----------

gravity = None              # slow-moving average of acceleration = which way is down
rest = None                 # gravity direction with the hand resting on the table
in_bite = False
bite_start = 0.0
bite_strengths = []

tremor_on = False
on_count = off_count = 0


def tilt_degrees():
    if gravity is None or rest is None:
        return 0.0
    dot = sum(a * b for a, b in zip(gravity, rest))
    na = math.sqrt(sum(a * a for a in gravity))
    nb = math.sqrt(sum(b * b for b in rest))
    if na == 0 or nb == 0:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / (na * nb)))))


# ---------- meal sessions ----------

meal = None
sessions = []


def load_sessions():
    global sessions
    try:
        with open(DATA_FILE) as f:
            sessions = json.load(f)
    except Exception:
        sessions = []


def save_sessions():
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(sessions, f)
    except Exception as e:
        print("could not save sessions:", e)


def start_meal(label=None):
    global meal, in_bite
    if meal is not None:
        return
    in_bite = False
    meal = {"start": time.time(), "label": label, "bites": [], "rest": [], "action": [],
            "freqs": [], "episodes": 0}


def end_meal():
    global meal
    if meal is None:
        return
    m, meal = meal, None
    duration = time.time() - m["start"]
    n = len(m["bites"])
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    sessions.append({
        "date": m["label"] or time.strftime("%b %d, %H:%M", time.localtime(m["start"])),
        "duration_s": round(duration, 1),
        "bites": n,
        "sec_per_bite": round(duration / n, 1) if n else None,
        "tremor_rest": avg(m["rest"]),
        "tremor_eating": avg(m["action"]),
        "tremor_hz": avg(m["freqs"]),
        "episodes": m["episodes"],
        "simulated": False,
    })
    save_sessions()
    send_history()


def change_flags():
    """Compare the latest meal with the baseline (the first three meals)."""
    if len(sessions) < 4:
        return []
    flags = []
    for key, name in (("sec_per_bite", "Time per bite"), ("tremor_eating", "Tremor while eating")):
        base = [s[key] for s in sessions[:3] if s.get(key)]
        latest = sessions[-1].get(key)
        if base and latest:
            change = latest / (sum(base) / len(base)) - 1.0
            if change >= CHANGE_FLAG:
                flags.append(f"{name} is {round(change * 100)}% above this person's baseline")
    return flags


def history_payload():
    return {"sessions": sessions, "flags": change_flags()}


def send_history():
    ui.send_message("history", history_payload())


# ---------- sensor stream from the sketch ----------

last_live = None
last_sample_time = 0.0
samples_this_second = 0
status = {"sensor": "no_data", "rate": 0}


def record_sensor_movement(x: float, y: float, z: float):
    global gravity, since_last, in_bite, bite_start, bite_strengths, last_live
    global last_sample_time, samples_this_second, tremor_on, on_count, off_count
    a = (x * G, y * G, z * G)
    now = time.time()
    with lock:
        last_sample_time = now
        samples_this_second += 1
        buf.append(a)
        gravity = a if gravity is None else tuple(g + 0.05 * (v - g) for g, v in zip(gravity, a))
        since_last += 1
        if len(buf) < WINDOW or since_last < STEP:
            return
        since_last = 0
        samples = list(buf)
        tilt = tilt_degrees()

        bite_done = False
        if rest is not None:
            if not in_bite and tilt > BITE_UP_DEG:
                in_bite, bite_start, bite_strengths = True, now, []
            elif in_bite and tilt < BITE_DOWN_DEG:
                in_bite = False
                bite_done = BITE_MIN_S <= now - bite_start <= BITE_MAX_S

    strength, hz, rhythm = tremor_reading(samples)
    spread = max(max(s[i] for s in samples) - min(s[i] for s in samples) for i in range(3))

    with lock:
        status["sensor"] = "flat" if spread < 1e-4 else "ok"

        started = False
        if strength >= TREMOR_MIN and rhythm >= RHYTHM_MIN:
            on_count, off_count = on_count + 1, 0
            if not tremor_on and on_count >= ON_READINGS:
                tremor_on = started = True
        else:
            off_count, on_count = off_count + 1, 0
            if tremor_on and off_count >= OFF_READINGS:
                tremor_on = False

        if in_bite:
            bite_strengths.append(strength)
        if meal is not None:
            (meal["action"] if in_bite else meal["rest"]).append(strength)
            if tremor_on:
                meal["freqs"].append(hz)
            if started:
                meal["episodes"] += 1
            if bite_done:
                avg_bite = sum(bite_strengths) / len(bite_strengths) if bite_strengths else strength
                meal["bites"].append({"t": round(now - meal["start"], 1), "tremor": round(avg_bite, 3)})
        last_live = {
            "tremor": round(strength, 3), "hz": round(hz, 1), "tilt": round(tilt, 1),
            "tremor_on": tremor_on, "rhythm": round(rhythm, 2),
            "bite_list": meal["bites"][-300:] if meal else [],
            "episodes": meal["episodes"] if meal else 0,
            "raw": [round(v, 2) for v in a],
            "in_bite": in_bite, "calibrated": rest is not None,
            "meal_active": meal is not None,
            "bites": len(meal["bites"]) if meal else 0,
            "elapsed": round(now - meal["start"]) if meal else 0,
        }
        live = last_live
    ui.send_message("live", live)


Bridge.provide("record_sensor_movement", record_sensor_movement)


# ---------- dashboard buttons ----------

def on_calibrate(sid, data):
    global rest
    with lock:
        rest = gravity


def on_start(sid, data):
    label = data.get("label") if isinstance(data, dict) else None
    with lock:
        start_meal(label)


def on_end(sid, data):
    with lock:
        end_meal()


def on_history(sid, data):
    send_history()


def on_demo_history(sid, data):
    """Clearly labeled fake past meals so the trend chart has something to show."""
    global sessions
    fake = []
    for i in range(8):
        t = i / 7.0
        fake.append({
            "date": f"{24 - i * 3} months ago",
            "duration_s": round(600 + 420 * t), "bites": 60,
            "sec_per_bite": round(10 + 7 * t, 1),
            "tremor_rest": round(0.10 + 0.25 * t, 3),
            "tremor_eating": round(0.25 + 0.70 * t, 3),
            "tremor_hz": 5.0, "episodes": round(2 + 14 * t), "simulated": True,
        })
    with lock:
        sessions = fake + [s for s in sessions if not s.get("simulated")]
    send_history()


def on_clear(sid, data):
    global sessions
    with lock:
        sessions = []
        save_sessions()
    send_history()


ui.on_message("calibrate", on_calibrate)
ui.on_message("start_meal", on_start)
ui.on_message("end_meal", on_end)
ui.on_message("get_history", on_history)
ui.on_message("demo_history", on_demo_history)
ui.on_message("clear_history", on_clear)


# The page also polls this once a second, so it keeps working if the live link drops.
def get_state():
    with lock:
        return {"live": last_live, "status": dict(status), "history": history_payload()}


ui.expose_api("GET", "/state", get_state)

# Button fallbacks for when the live link is down.
ui.expose_api("GET", "/do/calibrate", lambda: on_calibrate(None, {}) or {"ok": True})
ui.expose_api("GET", "/do/start_meal", lambda: on_start(None, {}) or {"ok": True})
ui.expose_api("GET", "/do/end_meal", lambda: on_end(None, {}) or {"ok": True})
ui.expose_api("GET", "/do/demo_history", lambda: on_demo_history(None, {}) or {"ok": True})
ui.expose_api("GET", "/do/clear_history", lambda: on_clear(None, {}) or {"ok": True})


# ---------- once a second: is the sensor actually sending? ----------

def loop():
    global samples_this_second
    time.sleep(1.0)
    with lock:
        status["rate"] = samples_this_second
        samples_this_second = 0
        if time.time() - last_sample_time > 2.0:
            status["sensor"] = "no_data"
        snapshot = dict(status)
    ui.send_message("status", snapshot)


load_sessions()
App.run(user_loop=loop)