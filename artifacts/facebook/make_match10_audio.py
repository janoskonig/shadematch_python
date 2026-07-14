#!/usr/bin/env python3
"""Synthesize an original, royalty-free 12s soundtrack for the match10 video.

No samples, no copyrighted material - sines + noise with hand-built envelopes,
timed to the animation beats:
  ~0.3s    brand chime
  1.7-5.7s ten rising pentatonic plinks (one per colour slot)
  ~6.1s    match complete: whoosh + flourish + bell
  7.9/8.6/9.25s  three escalating medal fanfares (streak = glory)
  10.2s+   major resolve
Outputs match10_audio.wav (stereo, 44.1kHz).
"""
import numpy as np, wave
from pathlib import Path

SR = 44100
DUR = 12.0
N = int(SR * DUR)
L = np.zeros(N); R = np.zeros(N)

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
    e = np.exp(-x * 7.5) * (1 - np.exp(-x * 400))
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
    swell = np.sin(np.pi * np.clip(x/dur, 0, 1))
    return amp * s / len(freqs) * swell

def whoosh(dur, amp=0.5):
    n = int(dur * SR); x = np.arange(n) / SR
    noise = np.random.default_rng(7).standard_normal(n)
    b = np.copy(noise)
    for i in range(1, n): b[i] = 0.93*b[i-1] + 0.07*noise[i]
    e = np.sin(np.pi * np.clip(x/dur, 0, 1))**2
    return amp * b / (np.max(np.abs(b))+1e-9) * e

def hit(freq, dur, amp=0.6):
    n = int(dur * SR); x = np.arange(n) / SR
    s = np.sin(2*np.pi*freq*x*np.exp(-x*3))
    e = np.exp(-x*9)
    return amp * s * e

# ---- notes (C major pentatonic) -------------------------------------------
C4,D4,E4,G4,A4 = 261.63,293.66,329.63,392.00,440.00
C5,D5,E5,G5,A5 = 523.25,587.33,659.25,783.99,880.00
C6,D6,E6,G6,A6 = 1046.50,1174.66,1318.51,1567.98,1760.00

# soft pad bed under the whole clip
add(L,R,0.2, pad([C4/2, G4/2, C4], 11.6, amp=0.13))

# intro chime (brand drop-in)
add(L,R,0.30, marimba(C5,0.9,0.40),-0.2)
add(L,R,0.65, marimba(G5,0.9,0.36), 0.2)

# ten rising plinks, one per colour slot (1.74 .. 5.63s)
run = [C5,D5,E5,G5,A5,C6,D6,E6,G6,A6]
for i,f in enumerate(run):
    ti = 12*(0.13 + i*0.036) + 0.18
    pan = -0.35 if i%2==0 else 0.35
    add(L,R, ti, marimba(f, 0.5, 0.30 + 0.018*i), pan)

# match complete ~6.1s: whoosh + bright arpeggio + bell
add(L,R, 5.85, whoosh(0.8, 0.28))
for i,f in enumerate([C5,E5,G5,C6]):
    add(L,R, 6.10 + i*0.11, marimba(f, 0.6, 0.48))
add(L,R, 6.55, bell(C6, 1.6, 0.36))

# three escalating medal fanfares (streak!)
add(L,R, 7.92, marimba(G5, 0.6, 0.42))                       # medal 1
add(L,R, 8.58, marimba(A5, 0.5, 0.40))                       # medal 2
add(L,R, 8.72, marimba(C6, 0.6, 0.46))
add(L,R, 9.24, hit(150, 0.6, 0.42))                          # medal 3: the big one
for i,f in enumerate([C6,E6,G6]):
    add(L,R, 9.28 + i*0.10, marimba(f, 0.7, 0.52))
add(L,R, 9.62, bell(G6, 1.6, 0.30), 0.25)
add(L,R, 9.75, bell(C6, 1.8, 0.34), -0.2)

# gentle resolve ~10.3s
add(L,R, 10.3, pad([C4, E4, G4, C5], 1.7, amp=0.20))
add(L,R, 10.7, marimba(C5, 1.1, 0.28))

# ---- master ----------------------------------------------------------------
def finalize(buf):
    buf = np.tanh(buf * 1.1)
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
out = Path(__file__).resolve().parent / 'match10_audio.wav'
with wave.open(str(out), 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes(inter.tobytes())
print('wrote', out)
