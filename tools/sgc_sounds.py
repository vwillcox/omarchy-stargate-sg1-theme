#!/usr/bin/env python3
"""Synthesise Stargate-style sounds for the stargate-sg1 theme (no samples
from the show; everything is built from sines and filtered noise).

  chevron.wav   chevron locking: hydraulic thump + ringing metal clank
  dhd.wav       a DHD-style button press
  kawoosh.wav   the unstable vortex: rising roar, boom, watery tail
  shutdown.wav  the wormhole disengaging: a falling suck-back and pop
  dial.wav      compact dial-up: seven ring spins and locks, then kawoosh

Usage: sgc_sounds.py OUT_DIR
"""
import sys
import wave

import numpy as np

SR = 48000
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
rng = np.random.default_rng(1997)   # SG-1 premiered in 1997


def t_axis(dur):
    return np.arange(int(SR * dur)) / SR


def env_exp(n, tau, attack=0.002):
    t = np.arange(n) / SR
    return np.clip(t / attack, 0, 1) * np.exp(-t / tau)


def sweep_filter(x, centre_hz, width_oct=0.9, hop=256, win=1024):
    """Time-varying band-pass over an STFT: centre_hz(t_seconds) -> Hz."""
    w = np.hanning(win)
    out = np.zeros(len(x) + win)
    norm = np.zeros(len(x) + win)
    freqs = np.fft.rfftfreq(win, 1 / SR)
    lf = np.log2(np.maximum(freqs, 1.0))
    xp = np.concatenate([x, np.zeros(win)])
    for s in range(0, len(x), hop):
        seg = xp[s:s + win] * w
        fc = centre_hz((s + win / 2) / SR)
        gain = np.exp(-0.5 * ((lf - np.log2(fc)) / width_oct) ** 2)
        y = np.fft.irfft(np.fft.rfft(seg) * gain, win) * w
        out[s:s + win] += y
        norm[s:s + win] += w * w
    return (out / np.maximum(norm, 1e-6))[:len(x)]


def clunk(dur=0.6):
    n = int(SR * dur)
    t = np.arange(n) / SR
    # hydraulic thump: falling low sine
    f = 95 * np.exp(-t / 0.09) + 52
    thump = np.sin(2 * np.pi * np.cumsum(f) / SR) * env_exp(n, 0.11, 0.003)
    # ringing metal: inharmonic partials, higher ones die faster
    metal = np.zeros(n)
    for p, a, tau in ((347, 0.5, 0.30), (523, 0.42, 0.22), (911, 0.32, 0.16),
                      (1247, 0.25, 0.12), (1873, 0.16, 0.08), (2615, 0.1, 0.05)):
        metal += a * np.sin(2 * np.pi * p * t + rng.uniform(0, 6.28)) * env_exp(n, tau, 0.001)
    # impact transient
    click = sweep_filter(rng.normal(size=n), lambda _: 2400.0, 1.2) * env_exp(n, 0.012, 0.0005)
    return 0.9 * thump + 0.35 * metal + 0.6 * click


def spin(dur):
    """The ring turning: gear-toothed rumble with a motor hum."""
    n = int(SR * dur)
    t = np.arange(n) / SR
    rumble = sweep_filter(rng.normal(size=n), lambda _: 260.0, 1.0)
    teeth = 0.55 + 0.45 * np.sign(np.sin(2 * np.pi * 34 * t)) * 0.5
    hum = 0.25 * np.sin(2 * np.pi * 58 * t) + 0.12 * np.sin(2 * np.pi * 116 * t)
    fade = np.minimum(1, np.minimum(t / 0.12, (dur - t) / 0.1))
    return (rumble * teeth * 1.6 + hum) * np.clip(fade, 0, 1)


def kawoosh(dur=2.4):
    n = int(SR * dur)
    t = np.arange(n) / SR

    def fc(s):
        if s < 0.28:
            return 180 * (2600 / 180) ** (s / 0.28)
        return 2600 * (220 / 2600) ** min((s - 0.28) / 1.6, 1)
    roar = sweep_filter(rng.normal(size=n), fc, 0.8)
    amp = np.clip(t / 0.05, 0, 1) * np.exp(-np.maximum(t - 0.3, 0) / 0.7)
    boom = np.sin(2 * np.pi * np.cumsum(38 + 30 * np.exp(-t / 0.2)) / SR) * env_exp(n, 0.6, 0.01)
    # watery tail: little filtered bubbles
    bub = np.zeros(n)
    for _ in range(70):
        s = int(rng.uniform(0.35, dur - 0.2) * SR)
        f0 = rng.uniform(500, 1500)
        m = int(0.05 * SR)
        tt = np.arange(m) / SR
        g = np.sin(2 * np.pi * np.cumsum(f0 * (1 + 2.5 * tt / 0.05)) / SR) * np.exp(-tt / 0.015)
        bub[s:s + m] += g[:max(0, min(m, n - s))] * rng.uniform(0.05, 0.15)
    bub *= np.exp(-np.maximum(t - 0.35, 0) / 0.6)
    return roar * amp * 2.2 + 0.8 * boom + bub


def shutdown(dur=1.3):
    n = int(SR * dur)
    t = np.arange(n) / SR
    suck = sweep_filter(rng.normal(size=n), lambda s: 1800 * (140 / 1800) ** min(s / 0.75, 1), 0.7)
    amp = np.clip(t / 0.25, 0, 1) ** 2 * np.clip((0.85 - t) / 0.1, 0, 1)
    pop_at = int(0.8 * SR)
    pop = np.zeros(n)
    m = n - pop_at
    tt = np.arange(m) / SR
    pop[pop_at:] = np.sin(2 * np.pi * np.cumsum(120 * np.exp(-tt / 0.05) + 40) / SR) * np.exp(-tt / 0.12)
    return suck * amp * 1.8 + 0.7 * pop


def dhd(dur=0.22):
    n = int(SR * dur)
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * 740 * t) + 0.35 * np.sin(2 * np.pi * 1480 * t)
            + 0.12 * np.sin(2 * np.pi * 2220 * t)) * env_exp(n, 0.07, 0.004)
    press = sweep_filter(rng.normal(size=n), lambda _: 3200.0, 1.0) * env_exp(n, 0.006, 0.0005)
    return 0.6 * tone + 0.4 * press


def place(buf, sig, at):
    s = int(at * SR)
    e = min(len(buf), s + len(sig))
    buf[s:e] += sig[:e - s]


def save(name, sig, peak_db=-3.0):
    sig = sig / max(np.abs(sig).max(), 1e-9) * 10 ** (peak_db / 20)
    data = (np.clip(sig, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(f"{OUT}/{name}", "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data)


if __name__ == "__main__":
    save("chevron.wav", clunk())
    save("dhd.wav", dhd(), -6.0)
    save("kawoosh.wav", kawoosh())
    save("shutdown.wav", shutdown())
    # compact dial: spins get a little longer toward the point of origin
    spins = [0.55, 0.5, 0.6, 0.5, 0.62, 0.55, 0.8]
    total = sum(spins) + 7 * 0.32 + 0.4 + 2.4
    buf = np.zeros(int(SR * total))
    at = 0.0
    for d in spins:
        place(buf, spin(d) * 0.55, at)
        at += d
        place(buf, clunk(), at)
        at += 0.32
    place(buf, kawoosh(), at + 0.4)
    save("dial.wav", buf)
