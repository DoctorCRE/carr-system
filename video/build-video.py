#!/usr/bin/env python3
"""build-video.py — one-command CARR video build.
Shot list -> AE comp (grade, push-ins, Oswald text, end card) -> ffmpeg finish
with playbook audio: music bed at -23 LUFS, whoosh 120ms before each cut,
riser into the end card, impact on the card. Verifies loudness at the end.
"""
import json, pathlib, subprocess, sys
from asset_boundary import recovery_video_assets

ASSETS = recovery_video_assets(sys.argv[1:], "versioned b-roll and audio library")
if ASSETS.context.args:
    raise SystemExit(f"build-video: unexpected argument: {ASSETS.context.args[0]}")
PIPE = pathlib.Path.home() / "Movies/CARR Video Pipeline"
BROLL = ASSETS.brand_root / "Stock" / "broll"
AUD = ASSETS.brand_root / "Audio" / "library"
FF = "/opt/homebrew/bin/ffmpeg"

W, H = 1080, 1920
SHOT_DUR = 6.5
SHOTS = [
    (f"{BROLL}/lobby-blur-background_AS238299676.mov",        "Opening or relocating your practice?"),
    (f"{BROLL}/hospital-corridor_AS368549726.mov",            "Your lease will outlast most of your equipment."),
    (f"{BROLL}/glass-tower-facade_AS367005503.mov",           "The listing broker works for the landlord."),
    (f"{BROLL}/office-interior-natural-light_AS97973033.mov", "Sign without your own rep and they still collect both fees."),
    (f"{BROLL}/professionals-consult-laptop_AS518646499.mov", "CARR represents healthcare tenants and buyers. Never landlords."),
    (f"{BROLL}/lobby-blur-background_AS238299676.mov",        "Your advocate costs you nothing. The landlord pays the fee."),
]
ENDCARD_DUR = 4.5
MUSIC = AUD / "music/close-up.mp3"          # corporate-upbeat; rotate per piece, never within
# ONE transition sound per piece (Joe, Jul 22): the repeated pattern is the language.
WHOOSH = AUD / "sfx/cinematic-whoosh-fast-transition.mp3"
RISER = AUD / "sfx/cinematic-trailer-riser.mp3"
IMPACT = AUD / "sfx/cinematic-whoosh-deep-impact.mp3"
RAW = PIPE / "03_Output/demo_raw.mov"
FINAL = PIPE / "03_Output/carr_demo_vertical.mp4"

total = len(SHOTS) * SHOT_DUR + ENDCARD_DUR
endcard_at = len(SHOTS) * SHOT_DUR

job = {
    "compName": "CARR_Demo",
    "width": W, "height": H, "fps": 29.97,
    "shots": [{"src": s, "dur": SHOT_DUR, "pushIn": 1.06 + (i % 3) * 0.01, "line": l}
              for i, (s, l) in enumerate(SHOTS)],
    "endCard": {"dur": ENDCARD_DUR, "name": "Joe Bookout",
                "title": "HEALTHCARE REAL ESTATE | CARR",
                "tagline": "Tenant and buyer representation only"},
    "outPath": str(RAW),
}
(PIPE / "Scripts/stockclip-job.json").write_text(json.dumps(job, indent=2))
(PIPE / "Scripts/stockclip-log.txt").write_text("")

# AE drops AppleEvents that arrive mid-launch: make sure it's warm first
import time
def ae_running():
    return subprocess.run(["pgrep", "-f", "MacOS/After Effects"], capture_output=True).returncode == 0
if not ae_running():
    subprocess.run(["open", "/Applications/Adobe After Effects 2026/Adobe After Effects 2026.app"])
    for _ in range(30):
        if ae_running():
            break
        time.sleep(2)
    time.sleep(25)  # settle: process exists before the UI accepts events

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

# whoosh 120ms before each internal cut; riser 2s into end card; impact at card
inputs = [FF, "-y", "-nostdin", "-i", str(RAW), "-i", str(MUSIC)]
filters = [f"[1:a]atrim=0:{total},loudnorm=I=-23:TP=-3:LRA=7,"
           f"afade=t=in:d=0.7,afade=t=out:st={total-2.2}:d=2.2[mus]"]
mix = ["[mus]"]
idx = 2
for i in range(1, len(SHOTS)):           # cuts between shots — same sound, same level, every time
    t_ms = round((i * SHOT_DUR - 0.12) * 1000)
    inputs += ["-i", str(WHOOSH)]
    filters.append(f"[{idx}:a]adelay={t_ms}|{t_ms},volume=0.6[s{idx}]")
    mix.append(f"[s{idx}]"); idx += 1
r_ms = round((endcard_at - 2.0) * 1000)  # riser building into the card
inputs += ["-i", str(RISER)]
filters.append(f"[{idx}:a]atrim=0:2.1,adelay={r_ms}|{r_ms},volume=0.5[s{idx}]")
mix.append(f"[s{idx}]"); idx += 1
i_ms = round((endcard_at - 0.12) * 1000)
inputs += ["-i", str(IMPACT)]
filters.append(f"[{idx}:a]adelay={i_ms}|{i_ms},volume=0.75[s{idx}]")
mix.append(f"[s{idx}]"); idx += 1

fc = ";".join(filters) + f";{''.join(mix)}amix=inputs={len(mix)}:normalize=0,alimiter=limit=0.89[aout]"
cmd = inputs + ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                "-t", str(total), str(FINAL)]
print("Mixing + encoding...")
subprocess.run(cmd, check=True, capture_output=True)

r = subprocess.run([FF, "-i", str(FINAL), "-af", "ebur128", "-f", "null", "-"],
                   capture_output=True, text=True)
loud = [l for l in r.stderr.splitlines() if "I:" in l and "LUFS" in l]
print("Integrated loudness:", loud[-1].strip() if loud else "?")
RAW.unlink(missing_ok=True)  # multi-GB intermediate; any re-mix re-renders it anyway
print(f"DONE: {FINAL}")
