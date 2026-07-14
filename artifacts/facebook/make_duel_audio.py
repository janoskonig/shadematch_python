#!/usr/bin/env python3
"""Synthesize an original, royalty-free 12s soundtrack for the duel video.

No samples, no copyrighted material: everything is generated from sines +
noise with hand-built envelopes, timed to the animation beats:
  ~0.3s  brand/target chime
  2.2-6.0s  mixing build (rising pentatonic marimba, drop 'plinks')
  ~6.3s  swords clash (whoosh + low hit)
  ~7.9s  victory flourish (bright arpeggio + bell)
  9.5s+  gentle major resolve
Outputs duel_audio.wav (stereo, 44.1kHz).
"""
import numpy as np, wave, struct
from pathlib import Path

SR = 44100
DUR = 12.0
N = int(SR * DUR)
t = np.arange(N) / SR
L = np.zeros(N); R = np.zeros(N)

# ---- helpers ---------------------------------------------------------------
def env_ad(n, attack, decay, sr=SR):
    e = np.zeros(n)
    a = max(1, int(attack * sr)); d = max(1, int(decay * sr))
    e[:a] = np.linspace(0, 1, a)
    rest = n - a
    if rest > 0:
        e[a:] = np.exp(-np.linspace(0, 1, rest) * (n / d) * (d / n) * 6)  # exp tail
        e[a:] = np.exp(-np.linspace(0, 6, rest))
    return e

def add(buf_l, buf_r, start, sig, pan=0.0):
    s = int(start * SR); e = s + len(sig)
    if e > N:
        sig = sig[:N - s]; e = N
    if s < 0 or s >= N: return
    gl = np.sqrt((1 - pan) / 2); gr = np.sqrt((1 + pan) / 2)
    buf_l[s:e] += sig * gl; buf_r[s:e] += sig * gr

def marimba(freq, dur, amp=0.5):
    n = int(dur * SR); x = np.arange(n) / SR
    tone = (np.sin(2*np.pi*freq*x)
            + 0.35*np.sin(2*np.pi*2*freq*x)
            + 0.12*np.sin(2*np.pi*3.01*freq*x))
    e = np.exp(-x * 7.5) * (1 - np.exp(-x * 400))     # mallet: fast attack, exp decay
    return amp * tone * e

def bell(freq, dur, amp=0.4):
    n = int(dur * SR); x = np.arange(n) / SR
    parts = [(1,1.0),(2.76,0.5),(5.4,0.28),(8.1,0.15)]
    s = sum(a*np.sin(2*np.pi*freq*p*x) for p,a in parts)
    e = np.exp(-x * 2.6) * (1 - np.exp(-x * 300))
    return amp * s * e / 1.9

def pad(freqs, dur, amp=0.16):
    n = int(dur * SR); x = np.arange(n) / SR
    s = sum(np.sin(2*np.pi*f*x) + 0.5*np.sin(2*np.pi*f*1.005*x) for f in freqs)
    swell = np.sin(np.pi * np.clip(x/dur, 0, 1))       # fade in & out
    return amp * s / len(freqs) * swell

def whoosh(dur, amp=0.5):
    n = int(dur * SR); x = np.arange(n) / SR
    noise = np.random.default_rng(7).standard_normal(n)
    # sweep a simple 1-pole bandpass feel via cumulative smoothing
    b = np.copy(noise)
    for i in range(1, n): b[i] = 0.93*b[i-1] + 0.07*noise[i]
    e = np.sin(np.pi * np.clip(x/dur, 0, 1))**2
    return amp * b / (np.max(np.abs(b))+1e-9) * e

def hit(freq, dur, amp=0.6):
    n = int(dur * SR); x = np.arange(n) / SR
    s = np.sin(2*np.pi*freq*x*np.exp(-x*3))            # pitch drop = tom
    e = np.exp(-x*9)
    return amp * s * e

# ---- notes (C major pentatonic) -------------------------------------------
C4,D4,E4,G4,A4 = 261.63,293.66,329.63,392.00,440.00
C5,D5,E5,G5,A5 = 523.25,587.33,659.25,783.99,880.00
C6,E6,G6       = 1046.50,1318.51,1567.98

# soft pad bed under the whole clip
add(L,R,0.2, pad([C4/2, G4/2, C4], 11.6, amp=0.14))

# intro chime (brand + target pop)
add(L,R,0.35, marimba(C5,0.9,0.42),-0.2)
add(L,R,0.75, marimba(G5,0.9,0.38), 0.2)
add(L,R,1.15, bell(C6,1.4,0.22))

# mixing build: alternating L/R rising run, ~eighth notes 2.2 -> 6.0
build = [C5,E5,D5,G5,E5,A5,G5,C6,A5,C6]
for i,f in enumerate(build):
    ti = 2.2 + i*0.38
    pan = -0.35 if i%2==0 else 0.35
    add(L,R, ti, marimba(f, 0.5, 0.34 + 0.012*i), pan)

# drop 'plinks' sprinkled during the mix (high, quiet)
for ti,f,pn in [(2.6,G6,-0.4),(3.3,E6,0.4),(4.0,C6,-0.4),(4.7,G6,0.4),(5.4,E6,-0.4)]:
    add(L,R, ti, marimba(f, 0.28, 0.16), pn)

# clash ~6.3s: whoosh + low hit + a held tense note
add(L,R, 6.05, whoosh(0.9, 0.30))
add(L,R, 6.30, hit(160, 0.7, 0.55))
add(L,R, 6.35, marimba(G4, 0.9, 0.22))

# victory flourish ~7.9s: bright ascending arpeggio + bell sparkle
for i,f in enumerate([C5,E5,G5,C6]):
    add(L,R, 7.85 + i*0.13, marimba(f, 0.6, 0.5))
add(L,R, 8.45, bell(C6, 1.8, 0.40))
add(L,R, 8.7,  bell(G6, 1.4, 0.20), 0.3)

# gentle resolve ~9.6s: major chord pad swell + final soft note
add(L,R, 9.5, pad([C4, E4, G4, C5], 2.4, amp=0.20))
add(L,R, 10.0, marimba(C5, 1.2, 0.30))

# ---- master: soft-clip, master fade, normalize -----------------------------
def finalize(buf):
    buf = np.tanh(buf * 1.1)                            # gentle soft clip
    fi = int(0.12*SR); fo = int(0.8*SR)
    buf[:fi] *= np.linspace(0,1,fi)
    buf[-fo:] *= np.linspace(1,0,fo)
    return buf
L = finalize(L); R = finalize(R)
peak = max(np.max(np.abs(L)), np.max(np.abs(R)), 1e-9)
g = 0.89 / peak
L *= g; R *= g

inter = np.empty(N*2, dtype=np.int16)
inter[0::2] = np.int16(np.clip(L,-1,1) * 32767)
inter[1::2] = np.int16(np.clip(R,-1,1) * 32767)
out = Path(__file__).resolve().parent / 'duel_audio.wav'
with wave.open(str(out), 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(inter.tobytes())
print('wrote', out)
