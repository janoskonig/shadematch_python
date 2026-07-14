#!/usr/bin/env bash
# Adds a procedurally-synthesized soundtrack to the silent Reels/Stories videos.
# The audio is timed to the entrance animation in make_insta_anim.py:
#   drops fall  -> music-box plips at 1.50 / 1.95 / 2.40 s (C5 E5 G5)
#   target fills + check draws -> bell chord arpeggio at 3.00 s (C6 E6 G6)
#   soft C3/G3 pad under the whole 6.8 s
#   CTA rises -> warm two-note accent at 5.10 / 5.50 s (G5 -> C6)
# No external assets: every tone is an ffmpeg sine source with a pluck envelope,
# so there is nothing to license or download.
set -euo pipefail
cd "$(dirname "$0")"

SND="_reel_soundtrack.wav"

# --- build the soundtrack (language-independent, generated once) ---------------
ffmpeg -y -v error \
  -f lavfi -i "sine=frequency=523.25:duration=1.4" \
  -f lavfi -i "sine=frequency=659.25:duration=1.4" \
  -f lavfi -i "sine=frequency=783.99:duration=1.4" \
  -f lavfi -i "sine=frequency=1046.50:duration=2.4" \
  -f lavfi -i "sine=frequency=1318.51:duration=2.4" \
  -f lavfi -i "sine=frequency=1567.98:duration=2.4" \
  -f lavfi -i "sine=frequency=130.81:duration=6.8" \
  -f lavfi -i "sine=frequency=196.00:duration=6.8" \
  -f lavfi -i "sine=frequency=783.99:duration=1.4" \
  -f lavfi -i "sine=frequency=1046.50:duration=1.8" \
  -filter_complex "\
    [0]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=1.4:curve=exp,volume=0.55,adelay=1500[d1];\
    [1]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=1.4:curve=exp,volume=0.55,adelay=1950[d2];\
    [2]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=1.4:curve=exp,volume=0.55,adelay=2400[d3];\
    [3]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=2.4:curve=exp,volume=0.30,adelay=3000[b1];\
    [4]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=2.4:curve=exp,volume=0.28,adelay=3080[b2];\
    [5]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=2.4:curve=exp,volume=0.26,adelay=3160[b3];\
    [6]afade=t=in:st=0:d=1.2,afade=t=out:st=5.4:d=1.4,volume=0.11[p1];\
    [7]afade=t=in:st=0:d=1.2,afade=t=out:st=5.4:d=1.4,volume=0.09[p2];\
    [8]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=1.4:curve=exp,volume=0.40,adelay=5100[c1];\
    [9]afade=t=in:st=0:d=0.008,afade=t=out:st=0:d=1.8:curve=exp,volume=0.42,adelay=5500[c2];\
    [d1][d2][d3][b1][b2][b3][p1][p2][c1][c2]amix=inputs=10:normalize=0:dropout_transition=0,\
    volume=0.9,alimiter=limit=0.95,afade=t=out:st=6.6:d=0.2,aformat=channel_layouts=stereo\
  " "$SND"

echo "built $SND"

# --- mux onto each silent video ----------------------------------------------
for lang in EN HU; do
  for fmt in 916 45; do
    src="shadestudy_insta_video_${fmt}_${lang}.mp4"
    [ -f "$src" ] || continue
    out="shadestudy_reel_${fmt}_${lang}.mp4"
    ffmpeg -y -v error -i "$src" -i "$SND" \
      -map 0:v:0 -map 1:a:0 \
      -c:v copy -c:a aac -b:a 192k -ar 44100 -ac 2 -shortest "$out"
    echo "wrote $out"
  done
done
