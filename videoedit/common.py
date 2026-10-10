"""공용 도구: 규칙 로드/저장, ffmpeg 실행, 파일 정보."""
import json, subprocess, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RULES_PATH = ROOT / "rules" / "rules.json"
FONTS_DIR = "/usr/share/fonts/truetype/nanum"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def load_rules():
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def save_rules(r):
    RULES_PATH.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


def run(cmd, capture=True, check=True, cwd=None):
    p = subprocess.run([str(c) for c in cmd], capture_output=capture, text=True, cwd=cwd)
    if check and p.returncode != 0:
        sys.stderr.write((p.stderr or "")[-3000:] + "\n")
        raise RuntimeError("명령 실패: " + " ".join(str(c) for c in cmd[:6]))
    return p


def ff(args, **kw):
    return run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + args, **kw)


def duration(path):
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path])
    return float(p.stdout.strip())


def probe_size(path):
    p = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", path])
    w, h = p.stdout.strip().split("x")
    return int(w), int(h)


def has_audio(path):
    p = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path])
    return bool(p.stdout.strip())


def list_videos(folder):
    folder = pathlib.Path(folder)
    return sorted([p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXT])


def speech_segments(path, rules):
    """무음 감지로 말하는 구간 [(start,end)] 반환."""
    c = rules["cut"]
    d = duration(path)
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
             f"silencedetect=noise={c['silence_db']}dB:d={c['min_silence']}", "-f", "null", "-"])
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    sil = list(zip(starts, ends + [d] * (len(starts) - len(ends))))
    segs, cur = [], 0.0
    for s, e in sil:
        if s - cur >= c["min_speech"]:
            segs.append((cur, s))
        cur = e
    if d - cur >= c["min_speech"]:
        segs.append((cur, d))
    return segs
