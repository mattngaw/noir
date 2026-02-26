# NOIR

> "The rain hammered against the terminal like regrets on a guilty conscience."

A noir-flavored AI system monitor for Linux machines.

## What it does

- Real-time system vitals: CPU, Memory, Temperature, Disk, Load, Uptime
- Live CPU sparkline history
- AI-generated noir monologues from a local Ollama model (default: `lfm2.5-thinking`),
  continuously cycling with a 10s cooldown between dispatches
- Monologues accumulate in a scrolling history panel -- older ones scroll off the top
- Each monologue shows an appended end-to-end throughput tag (tok/s), dimmed so
  it stays informative without overpowering the text
- Each query runs on a random number of CPU cores (1-4) so load varies query to query
- Prompts are built from **peak CPU and temperature recorded during the last Ollama
  generation**, not the idle readings between queries -- so the narrative reflects
  what the machine actually did, not the quiet after

## Run it

    python3 noir.py

Optional model override:

    python3 noir.py --model lfm2.5-thinking:latest

Press Q or Escape to quit.
Requires: Python 3.8+, Linux (`/proc` + `os.getloadavg()`), Ollama running, and a
terminal at least 38 columns wide.

`vcgencmd` is optional (used on Raspberry Pi for temperature); if unavailable, NOIR
falls back to `/sys/class/thermal/thermal_zone0/temp`.

On startup, NOIR checks whether the requested model exists locally in Ollama.
If not, it exits with an error and tells you which `ollama pull ...` command to run.


## Files

    noir.py          -- curses-based live monitor
    README.md        -- you are here

## Notes

- Reasoning models can emit `<think>` blocks; these are stripped before display.
  If a generation is interrupted, the incomplete block is stripped too.
- Temperature threshold colors: green < 65C, yellow < 80C, red >= 80C
- Disk/Memory/CPU bars: green < 70%, yellow < 90%, red >= 90%
- The AI panel shows the last 5 monologues. While the model is thinking,
  the loading message is appended to the existing history, not a blank screen.
- Throughput uses `(prompt_eval_count + eval_count) / total_duration` from Ollama,
  displayed to one decimal place.

---
*Created by Claude Sonnet 4.6 for Matto, on a Raspberry Pi 5, on the night of Feb 25, 2026*
