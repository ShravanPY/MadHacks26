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

# Camera: the object detection Brick watches for a person at the table.
USE_CAMERA = True           # set to False to run without the camera and its Brick
PERSON_CONFIDENCE = 0.5
PRESENT_HOLD_S = 3.0        # "at the table" lasts this long after the last sighting
AUTO_MEALS = True           # sitting down starts the meal, getting up ends it
SIT_S = 5.0                 # in view this long -> resting position is recorded and the meal starts
AUTO_END_AWAY_S = 5.0       # out of view this long -> the meal ends
MIN_MEAL_S = 10.0           # shorter meals with no bites are thrown away

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
tremor_started = 0.0        # when the current tremor began
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


clock_skew = 0.0            # browser clock minus board clock (the board's clock is often wrong)
tz_offset_min = 0           # browser's minutes behind UTC


def local_label():
    t = time.gmtime(time.time() + clock_skew - tz_offset_min * 60)
    return time.strftime("%b %d, %I:%M %p", t).replace(" 0", " ")


def start_meal(label=None):
    global meal, in_bite
    if meal is not None:
        return
    in_bite = False
    label = label or local_label()
    meal = {"start": time.time(), "label": label, "bites": [], "rest": [], "action": [],
            "freqs": [], "episodes": 0, "spans": [],   # spans: [start_s, end_s or None]
            "away": 0.0, "away_run": 0.0, "seen_person": False}


def end_meal(trim=0.0):
    """trim: seconds to drop from the end (the wait before an automatic end)."""
    global meal
    if meal is None:
        return
    m, meal = meal, None
    duration = max(1.0, time.time() - m["start"] - trim)
    if m["spans"] and m["spans"][-1][1] is None:
        m["spans"][-1][1] = round(duration, 1)
    n = len(m["bites"])
    if n == 0 and duration < MIN_MEAL_S:
        return                                  # someone just walked past: not a meal
    active = max(1.0, duration - m["away"])     # time away from the table doesn't count
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    sessions.append({
        "date": m["label"] or time.strftime("%b %d, %H:%M", time.localtime(m["start"])),
        "duration_s": round(duration, 1),
        "bites": n,
        "sec_per_bite": round(active / n, 1) if n else None,
        "away_s": round(m["away"]),
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
    global last_sample_time, samples_this_second, tremor_on, tremor_started, on_count, off_count
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

        started = stopped = False
        if strength >= TREMOR_MIN and rhythm >= RHYTHM_MIN:
            on_count, off_count = on_count + 1, 0
            if not tremor_on and on_count >= ON_READINGS:
                tremor_on = started = True
                tremor_started = now
        else:
            off_count, on_count = off_count + 1, 0
            if tremor_on and off_count >= OFF_READINGS:
                tremor_on = False
                stopped = True

        if in_bite:
            bite_strengths.append(strength)
        if meal is not None:
            (meal["action"] if in_bite else meal["rest"]).append(strength)
            if tremor_on:
                meal["freqs"].append(hz)
            elapsed_now = round(now - meal["start"], 1)
            if started:
                meal["episodes"] += 1
                meal["spans"].append([elapsed_now, None])
            elif tremor_on and not meal["spans"]:
                meal["spans"].append([0.0, None])      # tremor was already going when the meal began
                meal["episodes"] += 1
            if stopped and meal["spans"] and meal["spans"][-1][1] is None:
                meal["spans"][-1][1] = elapsed_now
            if bite_done:
                avg_bite = sum(bite_strengths) / len(bite_strengths) if bite_strengths else strength
                meal["bites"].append({"t": round(now - meal["start"], 1), "tremor": round(avg_bite, 3)})
        last_live = {
            "tremor": round(strength, 3), "hz": round(hz, 1), "tilt": round(tilt, 1),
            "tremor_on": tremor_on, "rhythm": round(rhythm, 2),
            "bite_list": meal["bites"][-300:] if meal else [],
            "episodes": meal["episodes"] if meal else 0,
            "episode_list": [[a, b if b is not None else round(now - meal["start"], 1)]
                             for a, b in meal["spans"][-200:]] if meal else [],
            "tremor_for": round(now - tremor_started, 1) if tremor_on else 0,
            "raw": [round(v, 2) for v in a],
            "in_bite": in_bite, "calibrated": rest is not None,
            "meal_active": meal is not None,
            "bites": len(meal["bites"]) if meal else 0,
            "elapsed": round(now - meal["start"]) if meal else 0,
            "away": round(meal["away"]) if meal else 0,
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
    """Manual start: the hand's current position becomes the resting position."""
    global rest
    label = data.get("label") if isinstance(data, dict) else None
    with lock:
        if meal is None and gravity is not None:
            rest = gravity
        start_meal(label)


def on_end(sid, data):
    with lock:
        end_meal()


def on_history(sid, data):
    send_history()


def on_clock(sid, data):
    """The page tells the board what time it really is."""
    global clock_skew, tz_offset_min
    try:
        clock_skew = float(data["now"]) - time.time()
        tz_offset_min = int(data["offset_min"])
    except Exception:
        pass


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
ui.on_message("clock", on_clock)
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


# ---------- camera: the object detection Brick says whether someone is at the table ----------

camera = {"state": "off", "detail": "", "present": False, "labels": [], "reports": 0}
person_last_seen = 0.0
person_since = 0.0          # start of the current unbroken sighting
last_report = 0.0


def labels_in(detections):
    """The Brick's result format has changed between versions, so accept dicts or lists."""
    found = {}
    if isinstance(detections, dict):
        items = detections.get("detection", detections)
    else:
        items = detections
    if isinstance(items, dict):
        for label, value in items.items():
            conf = value.get("confidence", 1.0) if isinstance(value, dict) else value
            found[str(label).lower()] = conf
    elif isinstance(items, (list, tuple)):
        for d in items:
            if isinstance(d, dict):
                label = d.get("class_name") or d.get("label") or d.get("content")
                if label:
                    found[str(label).lower()] = d.get("confidence", 1.0)
    out = {}
    for label, conf in found.items():
        try:
            out[label] = float(conf)
        except (TypeError, ValueError):
            out[label] = 1.0
    return out


def on_detections(detections):
    global person_last_seen, person_since, last_report
    try:
        found = labels_in(detections)
        conf = found.get("person", 0.0)
        if conf > 1.0:
            conf /= 100.0           # some versions report percent
        now = time.time()
        with lock:
            if camera["reports"] == 0:
                print("camera: first report from the Brick:", repr(detections)[:300])
            camera["reports"] += 1
            camera["state"] = "watching"
            camera["detail"] = ""
            camera["labels"] = sorted(found)[:6]
            last_report = now
            if conf >= PERSON_CONFIDENCE:
                if now - person_last_seen > PRESENT_HOLD_S:
                    person_since = now
                person_last_seen = now
    except Exception as e:
        camera["detail"] = f"{type(e).__name__}: {e}"[:200]
        print("camera: could not read a report:", e, repr(detections)[:300])


if USE_CAMERA:
    camera["state"] = "starting"
    try:
        from arduino.app_bricks.video_objectdetection import VideoObjectDetection
        detector = VideoObjectDetection(confidence=0.3, debounce_sec=0.0)
        detector.on_detect_all(on_detections)
    except Exception as e:
        camera.update(state="unavailable", detail=f"{type(e).__name__}: {e}"[:200])
        print("camera unavailable:", e)


# ---------- once a second: is the sensor actually sending? ----------

def loop():
    global samples_this_second
    time.sleep(1.0)
    with lock:
        status["rate"] = samples_this_second
        samples_this_second = 0
        now = time.time()
        if now - last_sample_time > 2.0:
            status["sensor"] = "no_data"

        global rest
        ended = False
        camera["present"] = camera["state"] == "watching" and now - person_last_seen <= PRESENT_HOLD_S
        if (AUTO_MEALS and meal is None and camera["present"] and gravity is not None
                and now - person_since >= SIT_S):
            rest = gravity                      # the hand's position right now is "resting"
            start_meal()
            meal["seen_person"] = True
        if meal is not None and camera["state"] == "watching":
            if camera["present"]:
                meal["seen_person"] = True
                meal["away_run"] = 0.0
            elif meal["seen_person"]:       # only count absence once the camera has seen them
                meal["away"] += 1.0
                meal["away_run"] += 1.0
                if meal["away_run"] >= AUTO_END_AWAY_S:
                    meal["away"] -= meal["away_run"]
                    end_meal(trim=meal["away_run"])
                    ended = True
        starts_in = None
        if AUTO_MEALS and meal is None and camera["present"]:
            starts_in = max(0, math.ceil(SIT_S - (now - person_since)))
        status["camera"] = dict(camera, away_run=round(meal["away_run"]) if meal else 0,
                                starts_in=starts_in, sit_s=SIT_S, end_s=AUTO_END_AWAY_S,
                                age=round(now - last_report) if last_report else None)
        snapshot = dict(status)
    ui.send_message("status", snapshot)
    if ended and last_live:
        ui.send_message("live", dict(last_live, meal_active=False))


load_sessions()
App.run(user_loop=loop)