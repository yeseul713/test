"""효과음/배경음. assets/sfx/*.wav, assets/bgm/*.(wav|mp3) 가 있으면 그것을 우선 사용하고,
없으면 저작권 걱정 없는 합성음을 생성한다."""
import wave, pathlib
import numpy as np
from .common import ROOT

SR = 44100
GEN = ROOT / "assets" / "generated"


def _write(path, x):
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def _t(d):
    return np.arange(int(SR * d)) / SR


def make_all():
    GEN.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    # whoosh: 대역 상승 노이즈
    t = _t(0.45); n = rng.standard_normal(len(t))
    k = np.convolve(n, np.ones(8) / 8, "same")
    env = np.sin(np.pi * t / t[-1]) ** 2
    _write(GEN / "whoosh.wav", (n * 0.5 + k * 0.5) * env * 0.6)
    # pop: 짧은 하강 사인
    t = _t(0.18); f = 900 - 500 * t / t[-1]
    _write(GEN / "pop.wav", np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 22) * 0.9)
    # hit: 저음 쿵
    t = _t(0.5); f = 120 - 70 * t / t[-1]
    _write(GEN / "hit.wav", np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7) * 0.95)
    # ding: 맑은 종소리
    t = _t(0.9)
    _write(GEN / "ding.wav", (np.sin(2 * np.pi * 1318 * t) + 0.4 * np.sin(2 * np.pi * 2637 * t)) * np.exp(-t * 5) * 0.5)
    # bgm: 가벼운 로파이 코드 루프 (Am-F-C-G) 32초
    chords = [(220, 261.6, 329.6), (174.6, 220, 261.6), (261.6, 329.6, 392), (196, 246.9, 392)]
    out = []
    for _ in range(2):
        for ch in chords:
            t = _t(4.0)
            pad = sum(np.sin(2 * np.pi * f * t) for f in ch) / 3 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t))
            beat = np.zeros_like(t)
            for b in np.arange(0, 4, 0.5):
                i = int(b * SR); m = min(len(t) - i, int(0.2 * SR)); tt = np.arange(m) / SR
                beat[i:i + m] += np.sin(2 * np.pi * ch[1] * 2 * tt) * np.exp(-tt * 14) * 0.5
            seg = (pad * 0.5 + beat * 0.5) * np.minimum(1, np.minimum(t / 0.05, (4 - t) / 0.05))
            out.append(seg)
    _write(GEN / "bgm.wav", np.concatenate(out) * 0.7)


def find(kind):
    """kind: whoosh|pop|hit|ding|bgm -> 경로"""
    user_dir = ROOT / "assets" / ("bgm" if kind == "bgm" else "sfx")
    if user_dir.exists():
        for p in sorted(user_dir.iterdir()):
            if p.suffix.lower() in {".wav", ".mp3", ".m4a"} and (kind == "bgm" or p.stem == kind):
                return str(p)
    if not (GEN / f"{kind}.wav").exists():
        make_all()
    return str(GEN / f"{kind}.wav")
