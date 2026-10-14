#!/usr/bin/env python3
"""build-operatory-math.py — escalator-style math video: do extra operatories
justify a relocation + buildout once TI money and free rent are priced in?
Mixed beats: private-practice footage + animated stat cards (counters, cost stack).
Numbers are a conservative illustrative model from cited industry benchmarks
(production/op $300-600K; buildout $150-350/sqft; TI $50-100/sqft dental)."""
import json, pathlib, subprocess, sys, time
from typing import Any
from asset_boundary import recovery_video_assets

ASSETS = recovery_video_assets(sys.argv[1:], "versioned b-roll and audio library")
if ASSETS.context.args:
    raise SystemExit(f"build-operatory-math: unexpected argument: {ASSETS.context.args[0]}")
PIPE = pathlib.Path.home() / "Movies/CARR Video Pipeline"
BROLL = ASSETS.brand_root / "Stock" / "broll"
AUD = ASSETS.brand_root / "Audio" / "library"
FF = "/opt/homebrew/bin/ffmpeg"
RAW = PIPE / "03_Output/opmath_raw.mov"
FINAL = PIPE / "03_Output/carr_operatory_math_1080sq.mp4"

W = H = 1080
MUSIC = AUD / "music/placeit-world-01.mp3"       # rotate: not used on prior pieces
SWEEP = AUD / "sfx/fast-small-sweep-transition.mp3"  # softest transition in library
CLICK = AUD / "sfx/camera-shutter-click.mp3"
RISER = AUD / "sfx/cinematic-trailer-riser.mp3"
IMPACT = AUD / "sfx/cinematic-whoosh-deep-impact.mp3"

OPER = f"{BROLL}/dental-operatory-pan_AS493460578.mov"
RECEP = f"{BROLL}/clinic-reception-waiting_AS515592805.mov"

# ANNOTATED because the shots are deliberately heterogeneous — a stat card and a
# footage beat carry different keys — so mypy infers dict[str, object] from the
# literal and every s["dur"] becomes an object it refuses to add. The annotation
# states the shape the file already relies on rather than casting at each use.
SHOTS: list[dict[str, Any]] = [
    {"src": OPER, "dur": 5.0, "pushIn": 1.06, "line": "Out of operatories? Run this math."},
    {"type": "stat", "kind": "counter", "dur": 5.0,
     "title": "One general operatory can produce",
     "counter": {"target": 350, "prefix": "$", "suffix": "K/yr", "delay": 0.7, "dur": 1.6},
     "sub": "industry benchmarks run $300K to $600K"},
    {"type": "stat", "kind": "counter", "dur": 4.5,
     "title": "Add two operatories",
     "counter": {"target": 700, "prefix": "+$", "suffix": "K/yr", "delay": 0.6, "dur": 1.4, "orange": True},
     "sub": "in added yearly production capacity"},
    {"src": RECEP, "dur": 5.0, "pushIn": 1.07, "line": "Relocation sounds expensive. Price the offsets first."},
    {"type": "stat", "kind": "stack", "dur": 9.0,
     "title": "The real cost, after concessions",
     "rows": [
        {"label": "Buildout for the new space", "value": "$500K", "delay": 0.7},
        {"label": "Landlord TI allowance", "value": "-$180K", "delay": 2.4, "orange": True},
        {"label": "Free rent during buildout", "value": "-$60K", "delay": 4.0, "orange": True}],
     "net": {"label": "Net cost", "target": 260, "prefix": "$", "suffix": "K", "delay": 5.9}},
    {"type": "stat", "kind": "counter", "dur": 6.0,
     "title": "At a 35 percent margin, two ops add",
     "counter": {"target": 245, "prefix": "$", "suffix": "K/yr", "delay": 0.7, "dur": 1.6},
     "sub": "net cost paid back in about 13 months"},
    {"src": OPER, "dur": 5.0, "pushIn": 1.08, "line": "The right space can pay for itself. Run your numbers first."},
]
ENDCARD: dict[str, Any] = {"dur": 4.5, "name": "Joe Bookout", "title": "HEALTHCARE REAL ESTATE | CARR",
           "tagline": "Tenant and buyer representation only"}

total = sum(s["dur"] for s in SHOTS) + ENDCARD["dur"]
endcard_at = sum(s["dur"] for s in SHOTS)

job = {"compName": "CARR_OperatoryMath", "width": W, "height": H, "fps": 29.97,
       "shots": SHOTS, "endCard": ENDCARD, "outPath": str(RAW)}
(PIPE / "Scripts/stockclip-job.json").write_text(json.dumps(job, indent=2))
(PIPE / "Scripts/stockclip-log.txt").write_text("")

def ae_running():
    return subprocess.run(["pgrep", "-f", "MacOS/After Effects"], capture_output=True).returncode == 0
if not ae_running():
    subprocess.run(["open", "/Applications/Adobe After Effects 2026/Adobe After Effects 2026.app"])
    for _ in range(30):
        if ae_running(): break
        time.sleep(2)
    time.sleep(25)

print(f"Building {total:.1f}s comp in After Effects...")
osa = f'''with timeout of 1800 seconds
  tell application "Adobe After Effects 2026"
    activate
    DoScript "$.evalFile(File('{PIPE}/Scripts/carr_stock_clip.jsx'))"
  end tell
end timeout'''
subprocess.run(["osascript", "-e", osa], check=False)
log = (PIPE / "Scripts/stockclip-log.txt").read_text()
print(log)
if "DONE" not in log:
    sys.exit("AE build failed; see log above")

# ---------- audio: schedule computed FROM the beat structure ----------
sweeps, clicks = [], []
t = 0.0
for s in SHOTS:
    if t > 0:
        sweeps.append(t - 0.12)                      # sound leads picture
    if s.get("type") == "stat":
        if s["kind"] == "counter":
            clicks.append(t + s["counter"]["delay"])
        else:
            clicks += [t + r["delay"] for r in s["rows"]]
            clicks.append(t + s["net"]["delay"])
    t += s["dur"]
sweeps.append(endcard_at - 0.12)  # into the end card the riser+impact take over; keep sweep out
sweeps = sweeps[:-1]

inputs = [FF, "-y", "-nostdin", "-i", str(RAW), "-i", str(MUSIC)]
filters = [f"[1:a]atrim=0:{total},loudnorm=I=-23:TP=-3:LRA=7,"
           f"afade=t=in:d=0.7,afade=t=out:st={total-2.2}:d=2.2[mus]"]
mix = ["[mus]"]; idx = 2
def add(path, at, vol):
    global idx
    ms = round(at * 1000)
    inputs.extend(["-i", str(path)])
    filters.append(f"[{idx}:a]adelay={ms}|{ms},volume={vol}[s{idx}]")
    mix.append(f"[s{idx}]"); idx += 1
for at in sweeps: add(SWEEP, at, 0.3)                # v4: consistent AND quiet
for at in clicks: add(CLICK, at, 0.45)
add(RISER, endcard_at - 2.0, 0.5)
add(IMPACT, endcard_at - 0.12, 0.7)

fc = ";".join(filters) + f";{''.join(mix)}amix=inputs={len(mix)}:normalize=0,alimiter=limit=0.89[aout]"
cmd = inputs + ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                "-t", str(total), str(FINAL)]
print(f"Mixing ({len(sweeps)} sweeps, {len(clicks)} clicks) + encoding...")
subprocess.run(cmd, check=True, capture_output=True)

r = subprocess.run([FF, "-i", str(FINAL), "-af", "ebur128", "-f", "null", "-"],
                   capture_output=True, text=True)
loud = [l for l in r.stderr.splitlines() if "I:" in l and "LUFS" in l]
print("Integrated loudness:", loud[-1].strip() if loud else "?")
RAW.unlink(missing_ok=True)  # multi-GB intermediate; any re-mix re-renders it anyway
print(f"DONE: {FINAL}")
