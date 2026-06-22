#!/bin/bash
# Build a short, AI-narrated MP4 of the ShadeMatch pitch for Prof. Sudarat Kiat-amnuay.
# Voiceover: macOS `say` (neural TTS). Assembly: ffmpeg. Slides: demo/screenshots/.
# Usage: bash demo/build_narrated_video.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SS="$DIR/screenshots"
OUT="$DIR/voiceover"
mkdir -p "$OUT"

VOICE="${VOICE:-Samantha}"   # best US voice installed; override e.g. VOICE="Ava (Premium)"
RATE="${RATE:-168}"          # words per minute

# Chosen slides (existing 1280x720 screenshots) and their short, Sudarat-addressed lines.
slides=(01 03 05 12 13 14 16 18)
texts=(
"Professor Kiat-amnuay — this is ShadeMatch. It began as a colour-matching game and became a pigment instrument: a 327-pigment spectral catalog, Kubelka-Munk mixing, and targets that are real measured human skin. Here is the short version."
"The targets are real measured skin tones — Xiao's 2017 database, four ethnicities across four body sites — reproduced with a chosen palette under live Kubelka-Munk. Colour by physics, not averaged pixels."
"There are three surfaces. Spectral: pick a skin tone, reproduce it, and read the Delta E live — including how it drifts under daylight, incandescent, and fluorescent light, so metamerism becomes visible. Reverse-engineer: feed a measured colour and get palette-aware recipes. And the Gamut Lab."
"The solver searches every pigment subset and returns a Pareto front of recipes, scored under several illuminants. When a colour is out of the palette's reach, it says so honestly — out of gamut — instead of returning a bad match."
"The Gamut Lab draws the reachable colour gamut of a palette and overlays the human-skin hull. The question becomes literal: does your palette contain the skin tones you need to make?"
"One honest caveat: the skin targets are published means, and their spectrum is a metameric reconstruction, not a measured curve. I draw it dashed, on purpose — which is exactly why I am here."
"So, two asks. Pigment data: each silicone pigment at full strength plus dilutions, measured over black and white backings — that is what enables two-constant Kubelka-Munk. And real measured skin spectra, to replace the means. Same format: wavelength versus reflectance, 380 to 730 nanometres, in 10-nanometre steps."
"That is it. Colour by physics, aimed at human skin — foundation, cosmetics, prosthetics. The physics is built. I just need your measurements to point it at something real. Thank you."
)

echo "1/3 · Generating AI voiceover ($VOICE @ ${RATE} wpm)…"
for i in "${!slides[@]}"; do
  say -v "$VOICE" -r "$RATE" -o "$OUT/seg$i.aiff" "${texts[$i]}"
done

echo "2/3 · Rendering per-slide video segments…"
: > "$OUT/concat.txt"
for i in "${!slides[@]}"; do
  n="${slides[$i]}"
  ffmpeg -y -loop 1 -i "$SS/desktop-slide-$n.png" -i "$OUT/seg$i.aiff" \
    -c:v libx264 -tune stillimage -pix_fmt yuv420p \
    -c:a aac -b:a 192k -vf "scale=1280:720,setsar=1,fps=25" \
    -shortest "$OUT/part$i.mp4" -loglevel error
  echo "file 'part$i.mp4'" >> "$OUT/concat.txt"
done

echo "3/3 · Concatenating…"
ffmpeg -y -f concat -safe 0 -i "$OUT/concat.txt" -c copy \
  "$OUT/shadematch_sudarat_short.mp4" -loglevel error

echo "Done → $OUT/shadematch_sudarat_short.mp4"
