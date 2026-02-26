#!/usr/bin/env python3
"""
Noir System Monitor for Linux
An AI-narrated terminal dashboard. Dark alleys, hot silicon, cold facts.
"""

import curses
import threading
import time
import json
import argparse
import urllib.request
import urllib.error
import subprocess
import os
import sys
import textwrap
from collections import deque
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "lfm2.5-thinking"
AI_COOLDOWN = 10  # target seconds between query starts (adaptive post-query wait)
AI_MIN_COOLDOWN = 3  # minimum seconds to wait after each query completes
STAT_REFRESH = 1  # seconds between stat updates
CPU_HISTORY = 40  # bars in the sparkline
# ────────────────────────────────────────────────────────────

# Shared state
state = {
    "cpu": 0.0,
    "mem_used": 0.0,
    "mem_total": 0.0,
    "temp": 0.0,
    "disk_used": 0,
    "disk_total": 0,
    "uptime": "",
    "load": (0.0, 0.0, 0.0),
    "ai_text": "Initializing... The gears are turning in the dark.",
    "ai_tps": None,
    "ai_history": deque(maxlen=5),
    "ai_thinking": False,
    "ai_updated": "",
    "ai_prompt": "",
    "ai_next_at": 0.0,  # epoch time when next query will fire
    "ai_cooldown_window": AI_COOLDOWN,  # current post-query wait window (seconds)
    "cpu_history": deque([0.0] * CPU_HISTORY, maxlen=CPU_HISTORY),
    "gen_peak_cpu": 0.0,  # peak CPU % during current/last Ollama generation
    "gen_peak_temp": 0.0,  # peak temp °C during current/last Ollama generation
    "running": True,
}


def build_dynamic_prompt(cpu, mem, temp, disk, load_str, uptime, num_threads=4):
    """
    Construct a prompt whose tone, focus, and urgency reflect the actual
    system state -- not just the numbers, but the *character* of the moment.
    """

    # ── TEMPERATURE mood ──────────────────────────────────────
    if temp >= 80:
        temp_mood = "feverish, desperate"
        temp_detail = (
            f"The silicon is burning at {temp:.0f}C -- a fever that won't break. "
            "Every instruction feels like a step through fire."
        )
    elif temp >= 65:
        temp_mood = "warm, suspicious"
        temp_detail = (
            f"Temperature sits at {temp:.0f}C, the kind of warm that makes a detective sweat "
            "through his shirt and wonder who turned up the heat."
        )
    elif temp >= 55:
        temp_mood = "comfortable but watchful"
        temp_detail = (
            f"At {temp:.0f}C the machine runs easy -- not cold, not hot. Steady."
        )
    else:
        temp_mood = "cool, composed"
        temp_detail = (
            f"A cool {temp:.0f}C. The kind of cold that sharpens the mind, "
            "keeps the circuits honest."
        )

    # ── CPU mood ──────────────────────────────────────────────
    if cpu >= 85:
        cpu_mood = "frantic, overwhelmed"
        cpu_detail = (
            f"CPU hammering at {cpu:.0f}% -- every core screaming, "
            "no slack in the rope, no room to breathe."
        )
    elif cpu >= 60:
        cpu_mood = "focused, tense"
        cpu_detail = (
            f"CPU at {cpu:.0f}% -- something's working hard in the back rooms, "
            "doors closed, no witnesses."
        )
    elif cpu >= 20:
        cpu_mood = "alert, purposeful"
        cpu_detail = (
            f"CPU ticking at {cpu:.0f}% -- steady work, a detective on a live case."
        )
    else:
        cpu_mood = "quiet, brooding"
        cpu_detail = (
            f"CPU barely moves at {cpu:.0f}% -- the building's empty at this hour, "
            "just the hum of the fluorescents and me."
        )

    # ── MEMORY mood ───────────────────────────────────────────
    if mem >= 85:
        mem_detail = (
            f"Memory at {mem:.0f}% -- the filing cabinets are stuffed, "
            "papers spilling onto the floor. Can't find anything clean."
        )
    elif mem >= 60:
        mem_detail = (
            f"Memory at {mem:.0f}% -- half the drawers are open, cases piling up."
        )
    else:
        mem_detail = (
            f"Memory lean at {mem:.0f}% -- most of the office is dark and quiet, "
            "only the active case lit up."
        )

    # ── UPTIME mood ───────────────────────────────────────────
    # Parse uptime string for hours
    uptime_hours = 0
    if "h" in uptime:
        try:
            uptime_hours = int(uptime.split("h")[0])
        except ValueError:
            pass

    if uptime_hours >= 72:
        uptime_detail = (
            f"Been running {uptime} straight -- the long stakeout, "
            "cold coffee, the kind of tired that lives in the bones."
        )
    elif uptime_hours >= 24:
        uptime_detail = f"Up for {uptime}. A full day on the beat."
    elif uptime_hours >= 8:
        uptime_detail = f"Running {uptime} -- a full shift, eyes still open."
    elif uptime_hours >= 1:
        uptime_detail = f"Only {uptime} on the clock -- still finding my footing."
    else:
        uptime_detail = f"Just woke up {uptime} ago. Everything's still sharp-edged."

    # ── DISK mood ─────────────────────────────────────────────
    if disk >= 90:
        disk_detail = (
            f"Disk {disk}% full -- running out of places to hide the evidence."
        )
    elif disk >= 70:
        disk_detail = f"Disk {disk}% used -- getting crowded in the archives."
    else:
        disk_detail = f"Disk {disk}% -- plenty of room to bury a secret or two."

    # ── DOMINANT MOOD (drives the prompt framing) ─────────────
    moods = []
    if cpu >= 80 or temp >= 75:
        moods.append("urgent")
    if cpu < 10 and temp < 55 and uptime_hours >= 8:
        moods.append("weary and contemplative")
    if mem >= 80:
        moods.append("overwhelmed")
    if uptime_hours >= 48:
        moods.append("exhausted but resolute")
    if not moods:
        moods.append("steady and brooding")

    dominant = ", ".join(moods)

    prompt = (
        f"You are PATROCLUS, a noir detective AI living inside a Raspberry Pi 5. "
        f"Tone: {dominant}. "
        f"Write a 4-6 sentence hardboiled noir monologue right now. "
        f"Draw on these specific conditions: {temp_detail} {cpu_detail} {mem_detail} "
        f"{uptime_detail} {disk_detail} "
        f"Load average: {load_str}. "
        f"Running on {num_threads} of 4 cores this cycle. "
        f"Be poetic but specific -- use the real numbers. No markdown, plain text only. "
        f"Do NOT think too hard. Think briefly then write your short monologue."
    )
    return prompt


def get_cpu():
    """Read CPU usage via /proc/stat delta."""

    def read_stat():
        with open("/proc/stat") as f:
            fields = f.readline().split()
        return list(map(int, fields[1:]))

    s1 = read_stat()
    time.sleep(0.3)
    s2 = read_stat()
    idle1, idle2 = s1[3], s2[3]
    total1, total2 = sum(s1), sum(s2)
    return (1.0 - (idle2 - idle1) / (total2 - total1)) * 100


def get_mem():
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":")
            info[k.strip()] = int(v.split()[0])
    total = info["MemTotal"] / 1024 / 1024
    avail = info["MemAvailable"] / 1024 / 1024
    used = total - avail
    return used, total


def get_temp():
    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2
        )
        return float(result.stdout.strip().replace("temp=", "").replace("'C", ""))
    except Exception:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read()) / 1000
        except Exception:
            return 0.0


def get_disk():
    stat = os.statvfs("/")
    total = stat.f_blocks * stat.f_frsize / 1024**3
    free = stat.f_bavail * stat.f_frsize / 1024**3
    used = total - free
    return used, total


def get_uptime():
    with open("/proc/uptime") as f:
        secs = float(f.read().split()[0])
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def get_load():
    return os.getloadavg()


def stat_loop():
    while state["running"]:
        try:
            state["cpu"] = get_cpu()
            state["mem_used"], state["mem_total"] = get_mem()
            state["temp"] = get_temp()
            state["disk_used"], state["disk_total"] = get_disk()
            state["uptime"] = get_uptime()
            state["load"] = get_load()
            state["cpu_history"].append(state["cpu"])
            if state["ai_thinking"]:
                state["gen_peak_cpu"] = max(state["gen_peak_cpu"], state["cpu"])
                state["gen_peak_temp"] = max(state["gen_peak_temp"], state["temp"])
        except Exception:
            pass
        time.sleep(STAT_REFRESH)


def query_ollama(prompt, num_threads=4):
    data = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.85, "num_thread": num_threads},
        }
    ).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read())
    text = body.get("response", "").strip()
    # Strip thinking tokens -- handle both complete and unclosed blocks
    import re as _re

    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL)
    text = _re.sub(r"<think>.*", "", text, flags=_re.DOTALL)
    text = text.strip()

    # End-to-end throughput (prompt + generation tokens over total duration)
    tps = None
    try:
        prompt_tokens = int(body.get("prompt_eval_count", 0) or 0)
        gen_tokens = int(body.get("eval_count", 0) or 0)
        total_tokens = prompt_tokens + gen_tokens
        total_ns = int(body.get("total_duration", 0) or 0)
        if total_tokens > 0 and total_ns > 0:
            tps = total_tokens / (total_ns / 1_000_000_000)
    except Exception:
        tps = None

    return (text or "The machine speaks, but I cannot hear it tonight."), tps


def fetch_ollama_models(timeout=5):
    """Return locally available Ollama model names."""
    req = urllib.request.Request(
        "http://localhost:11434/api/tags",
        headers={"Content-Type": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_URL}. Start Ollama and try again. ({e})"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to read Ollama model list. ({e})")

    names = set()
    for model in body.get("models", []):
        for key in ("name", "model"):
            value = model.get(key)
            if value:
                names.add(value)
    return names


def ensure_ollama_model_available(model):
    """Fail fast if requested model is not present locally."""
    available = fetch_ollama_models()
    candidates = {model}
    if ":" not in model:
        candidates.add(f"{model}:latest")

    if any(name in available for name in candidates):
        return

    pull_name = model if ":" in model else f"{model}:latest"

    raise RuntimeError(
        f"Ollama model '{model}' is not available locally. "
        f"Download it first: ollama pull {pull_name}"
    )


def tps_tag(tps):
    if isinstance(tps, (int, float)) and tps > 0:
        return f"({tps:.1f} tok/s)"
    return "(-- tok/s)"


def wrap_ai_entry(text, width, tps):
    """Wrap entry text and append a subtle end-to-end throughput tag."""
    lines = wrap_ai_text(text, width)
    if not lines:
        lines = [""]

    suffix = tps_tag(tps)
    result = [{"text": ln, "dim_from": None} for ln in lines]

    if len(result[-1]["text"]) + 2 + len(suffix) <= width:
        result[-1]["text"] += f"  {suffix}"
        result[-1]["dim_from"] = len(result[-1]["text"]) - len(suffix)
    else:
        if len(suffix) <= width:
            result.append({"text": suffix, "dim_from": 0})
        else:
            for ln in wrap_ai_text(suffix, width):
                result.append({"text": ln, "dim_from": 0})

    return result


def ai_loop():
    import random

    time.sleep(3)  # let stats populate first
    while state["running"]:
        query_started_at = time.time()

        # ── Snapshot peaks from last generation, reset for this one ──
        last_peak_cpu = state["gen_peak_cpu"]
        last_peak_temp = state["gen_peak_temp"]
        state["gen_peak_cpu"] = 0.0
        state["gen_peak_temp"] = 0.0

        # ── Query phase ───────────────────────────────────────
        state["ai_thinking"] = True
        state["ai_next_at"] = 0.0  # not in cooldown
        try:
            cpu = state["cpu"]
            mem = (
                (state["mem_used"] / state["mem_total"] * 100)
                if state["mem_total"]
                else 0
            )
            disk = (
                int(state["disk_used"] / state["disk_total"] * 100)
                if state["disk_total"]
                else 0
            )
            load = (
                f"{state['load'][0]:.2f}/{state['load'][1]:.2f}/{state['load'][2]:.2f}"
            )
            temp = state["temp"]
            uptime = state["uptime"]

            # Prefer generation peaks -- they reflect actual work, not idle gaps
            if last_peak_cpu > cpu:
                cpu = last_peak_cpu
            if last_peak_temp > temp:
                temp = last_peak_temp

            num_threads = random.randint(2, 4)

            prompt = build_dynamic_prompt(
                cpu, mem, temp, disk, load, uptime, num_threads
            )
            state["ai_prompt"] = prompt

            text, tps = query_ollama(prompt, num_threads)
            state["ai_text"] = text
            state["ai_tps"] = tps
            state["ai_history"].append({"text": text, "tps": tps})
            state["ai_updated"] = datetime.now().strftime("%H:%M:%S")
        except Exception as e:
            err = f"The wire went dead. ({e})"
            state["ai_text"] = err
            state["ai_tps"] = None
            state["ai_history"].append({"text": err, "tps": None})
        finally:
            state["ai_thinking"] = False

        # ── Adaptive cooldown with enforced minimum post-query pause ─
        query_elapsed = max(0.0, time.time() - query_started_at)
        cooldown = max(AI_MIN_COOLDOWN, AI_COOLDOWN - query_elapsed)
        state["ai_cooldown_window"] = cooldown
        state["ai_next_at"] = time.time() + cooldown
        while state["running"] and time.time() < state["ai_next_at"]:
            time.sleep(0.25)


def draw_bar(used_pct, width=20, char_full="█", char_empty="░", warn=70, crit=90):
    filled = int(used_pct / 100 * width)
    bar = char_full * filled + char_empty * (width - filled)
    if used_pct >= crit:
        color = "RED"
    elif used_pct >= warn:
        color = "YELLOW"
    else:
        color = "GREEN"
    return bar, color


def sparkline(history, width=38, lo=0, hi=100):
    chars = " ▁▂▃▄▅▆▇█"
    vals = list(history)[-width:]
    line = ""
    for v in vals:
        idx = int((v - lo) / (hi - lo) * (len(chars) - 1))
        idx = max(0, min(idx, len(chars) - 1))
        line += chars[idx]
    return line.ljust(width)


def wrap_ai_text(text, width):
    """Wrap AI text into lines."""
    words = text.split()
    lines = []
    line = ""
    for word in words:
        if len(line) + len(word) + 1 <= width:
            line = (line + " " + word).strip()
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


TITLE = [
    "  ███╗   ██╗ ██████╗ ██╗██████╗ ",
    "  ████╗  ██║██╔═══██╗██║██╔══██╗",
    "  ██╔██╗ ██║██║   ██║██║██████╔╝",
    "  ██║╚██╗██║██║   ██║██║██╔══██╗",
    "  ██║ ╚████║╚██████╔╝██║██║  ██║",
    "  ╚═╝  ╚═══╝ ╚═════╝ ╚═╝╚═╝  ╚═╝",
]


def get_device_model():
    """Best-effort hardware model from Linux device tree."""
    model_paths = (
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model",
    )
    for path in model_paths:
        try:
            with open(path, "rb") as f:
                raw = f.read().replace(b"\x00", b"").strip()
            text = raw.decode("utf-8", errors="ignore")
            if text:
                return text
        except Exception:
            pass
    return "Unknown Device"


def get_subtitle():
    device = get_device_model()
    arch = os.uname().machine
    return f"{device}  //  {arch}  //  {OLLAMA_MODEL}"


def draw(stdscr, color):
    """Render one frame."""
    stdscr.erase()
    H, W = stdscr.getmaxyx()

    C = {
        "TITLE": curses.color_pair(1) | curses.A_BOLD,
        "SUB": curses.color_pair(6),
        "HEAD": curses.color_pair(5) | curses.A_BOLD,
        "BORDER": curses.color_pair(6),
        "LABEL": curses.color_pair(7),
        "GREEN": curses.color_pair(2),
        "YELLOW": curses.color_pair(3),
        "RED": curses.color_pair(4),
        "AI": curses.color_pair(8) | curses.A_ITALIC,
        "DIM": curses.color_pair(6) | curses.A_DIM,
        "BOLD": curses.A_BOLD,
        "SPARK": curses.color_pair(3),
    }

    row = 0

    # ── TITLE BLOCK ──
    if W > 38:
        for i, line in enumerate(TITLE):
            if row + i < H:
                x = max(0, (W - len(line)) // 2)
                try:
                    stdscr.addstr(row + i, x, line[: W - 1], C["TITLE"])
                except curses.error:
                    pass
        row += len(TITLE)
    else:
        # Compact title for narrow terminals
        t = "[ NOIR ]"
        x = max(0, (W - len(t)) // 2)
        try:
            stdscr.addstr(row, x, t, C["TITLE"])
        except curses.error:
            pass
        row += 1

    # Subtitle
    sub = get_subtitle()
    x = max(0, (W - len(sub)) // 2)
    try:
        stdscr.addstr(row, x, sub[: W - 1], C["SUB"])
    except curses.error:
        pass
    row += 1

    # Divider
    try:
        stdscr.addstr(row, 0, "─" * (W - 1), C["BORDER"])
    except curses.error:
        pass
    row += 1

    # ── LEFT PANEL: VITALS ──
    panel_w = min(44, W // 2)
    ai_x = panel_w + 8
    ai_w = W - ai_x - 1

    cpu = state["cpu"]
    mem_used = state["mem_used"]
    mem_tot = state["mem_total"]
    mem_pct = (mem_used / mem_tot * 100) if mem_tot else 0
    temp = state["temp"]
    disk_u = state["disk_used"]
    disk_t = state["disk_total"]
    disk_pct = (disk_u / disk_t * 100) if disk_t else 0
    uptime = state["uptime"]
    load = state["load"]

    vitals_start = row

    def add(r, x, text, attr=0):
        if r < H and x < W:
            try:
                stdscr.addstr(r, x, text[: max(0, W - x - 1)], attr)
            except curses.error:
                pass

    bar_w = 20

    # CPU
    bar, col = draw_bar(cpu, bar_w)
    add(row, 0, "  CPU  ", C["LABEL"])
    add(row, 7, f"{cpu:5.1f}%  ", C[col] | curses.A_BOLD)
    add(row, 15, "[", C["BORDER"])
    add(row, 16, bar, C[col])
    add(row, 16 + bar_w, "]", C["BORDER"])
    row += 1

    # Memory
    bar, col = draw_bar(mem_pct, bar_w)
    add(row, 0, "  MEM  ", C["LABEL"])
    add(row, 7, f"{mem_pct:5.1f}%  ", C[col] | curses.A_BOLD)
    add(row, 15, "[", C["BORDER"])
    add(row, 16, bar, C[col])
    add(row, 16 + bar_w, "]", C["BORDER"])
    add(row, 38, f"{mem_used:.1f}/{mem_tot:.0f}GB", C["DIM"])
    row += 1

    # Temp
    if temp >= 80:
        tcol = "RED"
    elif temp >= 65:
        tcol = "YELLOW"
    else:
        tcol = "GREEN"
    bar, _ = draw_bar(min(temp / 85 * 100, 100), bar_w)
    _, bcol = draw_bar(min(temp / 85 * 100, 100))
    add(row, 0, "  TEMP ", C["LABEL"])
    add(row, 7, f"{temp:5.1f}C  ", C[tcol] | curses.A_BOLD)
    add(row, 15, "[", C["BORDER"])
    add(row, 16, bar, C[tcol])
    add(row, 16 + bar_w, "]", C["BORDER"])
    row += 1

    # Disk
    bar, col = draw_bar(disk_pct, bar_w)
    add(row, 0, "  DISK ", C["LABEL"])
    add(row, 7, f"{disk_pct:5.1f}%  ", C[col] | curses.A_BOLD)
    add(row, 15, "[", C["BORDER"])
    add(row, 16, bar, C[col])
    add(row, 16 + bar_w, "]", C["BORDER"])
    add(row, 38, f"{disk_u:.0f}/{disk_t:.0f}GB", C["DIM"])
    row += 1

    row += 1
    add(row, 0, "  UPTIME  ", C["LABEL"])
    add(row, 10, uptime, C["GREEN"] | curses.A_BOLD)
    row += 1

    add(row, 0, "  LOAD    ", C["LABEL"])
    add(row, 10, f"{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}", C["YELLOW"])
    row += 1

    add(row, 0, "  TIME    ", C["LABEL"])
    add(row, 10, datetime.now().strftime("%H:%M:%S  %Y-%m-%d"), C["DIM"])
    row += 1

    row += 1
    add(row, 0, "  CPU HISTORY", C["HEAD"])
    row += 1
    spark = sparkline(state["cpu_history"], width=min(bar_w + 16, panel_w - 2))
    add(row, 2, spark, C["SPARK"])
    row += 1

    # ── RIGHT PANEL: AI DISPATCH ──
    if ai_w > 15 and H > 12:
        ai_row = vitals_start
        try:
            stdscr.addstr(ai_row, ai_x, "  MONOLOGUE", C["HEAD"])
        except curses.error:
            pass
        ai_row += 1

        try:
            stdscr.addstr(ai_row, ai_x, "─" * (ai_w - 1), C["BORDER"])
        except curses.error:
            pass
        ai_row += 1

        # Thinking / cooldown indicator
        if state["ai_thinking"]:
            spinner = "◐◓◑◒"
            spin_c = spinner[int(time.time() * 3) % 4]
            status = f"{spin_c} Consulting the oracle..."
        elif state["ai_next_at"] > 0:
            secs_left = max(0, state["ai_next_at"] - time.time())
            bar_w2 = 10
            window = max(state.get("ai_cooldown_window", AI_COOLDOWN), 0.001)
            filled2 = int((1 - secs_left / window) * bar_w2)
            bar2 = "▓" * filled2 + "░" * (bar_w2 - filled2)
            status = (
                f"↺ [{bar2}] next in {secs_left:.0f}s  (last: {state['ai_updated']})"
            )
        elif state["ai_updated"]:
            status = f"✓ Last dispatch: {state['ai_updated']}"
        else:
            status = ""

        if status:
            try:
                stdscr.addstr(ai_row, ai_x + 1, status[: ai_w - 2], C["DIM"])
            except curses.error:
                pass
            ai_row += 1

        ai_row += 1
        history = list(state["ai_history"])
        entries = (
            history if history else [{"text": state["ai_text"], "tps": state["ai_tps"]}]
        )
        if state["ai_thinking"]:
            entries = entries + [
                {"text": "...thinking, cigarette burning down...", "tps": False}
            ]

        all_lines = []
        for idx, entry in enumerate(entries):
            if idx > 0:
                all_lines.append({"text": "", "dim_from": None})

            if entry.get("tps") is False:
                for ln in wrap_ai_text(entry["text"], ai_w - 4):
                    all_lines.append({"text": ln, "dim_from": None})
            else:
                all_lines.extend(
                    wrap_ai_entry(entry["text"], ai_w - 4, entry.get("tps"))
                )

        available = max(0, H - 2 - ai_row)
        visible = all_lines[-available:] if len(all_lines) > available else all_lines
        for i, item in enumerate(visible):
            try:
                line = item["text"]
                dim_from = item["dim_from"]
                stdscr.addstr(ai_row + i, ai_x + 2, line, C["AI"])
                if isinstance(dim_from, int) and 0 <= dim_from <= len(line):
                    stdscr.addstr(
                        ai_row + i, ai_x + 2 + dim_from, line[dim_from:], C["DIM"]
                    )
            except curses.error:
                pass

    # ── FOOTER ──
    footer = f"  [Q] Quit  |  AI: adaptive {AI_COOLDOWN:g}s target, {AI_MIN_COOLDOWN:g}s minimum wait  |  NOIR v1.0  "
    if H - 1 > 0:
        try:
            stdscr.addstr(H - 2, 0, "─" * (W - 1), C["BORDER"])
            stdscr.addstr(H - 1, 0, footer[: W - 1], C["DIM"])
        except curses.error:
            pass

    stdscr.refresh()


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(500)

    # Init colors
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)  # TITLE
    curses.init_pair(2, curses.COLOR_GREEN, -1)  # GREEN
    curses.init_pair(3, curses.COLOR_YELLOW, -1)  # YELLOW
    curses.init_pair(4, curses.COLOR_RED, -1)  # RED
    curses.init_pair(5, curses.COLOR_WHITE, -1)  # HEAD
    curses.init_pair(6, curses.COLOR_BLUE, -1)  # BORDER/SUB/DIM
    curses.init_pair(7, curses.COLOR_WHITE, -1)  # LABEL
    curses.init_pair(8, curses.COLOR_MAGENTA, -1)  # AI text

    # Start background threads
    t_stat = threading.Thread(target=stat_loop, daemon=True)
    t_ai = threading.Thread(target=ai_loop, daemon=True)
    t_stat.start()
    t_ai.start()

    while state["running"]:
        try:
            draw(stdscr, curses.color_pair)
        except Exception:
            pass

        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            state["running"] = False
            break

    curses.endwin()
    print("\nNOIR signing off. The night swallows the data whole.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NOIR: noir-flavored AI system monitor"
    )
    parser.add_argument(
        "--model",
        default=OLLAMA_MODEL,
        help=f"Ollama model to query (default: {OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=AI_COOLDOWN,
        help=(
            f"Target seconds between AI query starts (default: {AI_COOLDOWN:g}). "
            f"A minimum {AI_MIN_COOLDOWN:g}s post-query wait is always enforced."
        ),
    )
    args = parser.parse_args()

    OLLAMA_MODEL = args.model
    AI_COOLDOWN = max(0.0, args.cooldown)
    state["ai_cooldown_window"] = AI_COOLDOWN

    try:
        ensure_ollama_model_available(OLLAMA_MODEL)
    except RuntimeError as e:
        print(f"\nERROR: {e}\n")
        sys.exit(1)

    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        print("\nNOIR signing off. The night swallows the data whole.\n")
