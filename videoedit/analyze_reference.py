"""레퍼런스 영상(유튜브 링크/파일)을 장면 단위로 분해해 수치로 뽑고 규칙 후보를 만든다.
산출물: reference/<이름>/analysis.json, scenes.png(장면 컨택트시트), reference/analysis.md, (--apply 시) rules.json 갱신"""
import re, json, pathlib, statistics, subprocess, tempfile
import numpy as np, cv2
from .common import run, ff, duration, probe_size, load_rules, save_rules, ROOT
from .qa import _get_ocr


def fetch(src, d):
    d.mkdir(parents=True, exist_ok=True)
    if re.match(r"https?://", src):
        subprocess.run(["yt-dlp", "-f", "bv*[height<=1920]+ba/b", "--merge-output-format", "mp4", "-o", str(d / "ref.%(ext)s"), src.split("&")[0]], check=True)
        return next(d.glob("ref.mp4"))
    return pathlib.Path(src)


def scene_cuts(video, thr=0.25):
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vf", f"select='gt(scene,{thr})',showinfo", "-an", "-f", "null", "-"])
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", p.stderr)]


def frame_at(video, t, w=540):
    d = pathlib.Path(tempfile.mkdtemp(prefix="fa_")); f = d / "x.png"
    ff(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={w}:-2", str(f)])
    return cv2.imread(str(f))


def zoom_scale(a, b):
    """두 프레임 사이 확대 배율(>1 이면 줌인) 추정."""
    orb = cv2.ORB_create(800)
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    ka, da = orb.detectAndCompute(ga, None); kb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return 1.0
    m = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(m) < 8: return 1.0
    M, _ = cv2.estimateAffinePartial2D(np.float32([ka[x.queryIdx].pt for x in m]), np.float32([kb[x.trainIdx].pt for x in m]))
    return round(float(np.hypot(M[0, 0], M[1, 0])), 3) if M is not None else 1.0


def dom_color(img, box):
    x0, y0, x1, y1 = [int(v) for v in box]; crop = img[max(0, y0):y1, max(0, x0):x1]
    if crop.size == 0: return "#ffffff"
    px = crop.reshape(-1, 3).astype(np.float32)
    _, lab, cen = cv2.kmeans(px, 3, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0), 3, cv2.KMEANS_PP_CENTERS)
    cnt = np.bincount(lab.flatten()); order = np.argsort(-cnt)
    # 글자색 = 가장자리(배경) 색에서 가장 멀면서 비중이 있는 군집
    edge = np.concatenate([crop[0], crop[-1]]).mean(axis=0)
    best = max(order[:3], key=lambda k: np.linalg.norm(cen[k] - edge) * (cnt[k] / cnt.sum()) ** 0.3)
    b, g, r = cen[best]; return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def subtitles(video, fps=4):
    d = pathlib.Path(tempfile.mkdtemp(prefix="rs_"))
    ff(["-i", str(video), "-vf", f"fps={fps},scale=540:-2", str(d / "f_%05d.png")])
    ocr, events = _get_ocr(), []
    for k, f in enumerate(sorted(d.glob("f_*.png"))):
        img = cv2.imread(str(f)); H = img.shape[0]
        res, _ = ocr(str(f))
        for box, text, conf in (res or []):
            if float(conf) < 0.6 or len(text.strip()) < 2: continue
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            events.append({"t": k / fps, "text": text, "h": max(ys) - min(ys), "ycenter": (min(ys) + max(ys)) / 2 / H,
                           "color": dom_color(img, (min(xs), min(ys), max(xs), max(ys)))})
    merged = []
    for e in events:  # 같은 문구가 연속되면 한 이벤트로
        for m in merged:
            if m["text"] == e["text"] and e["t"] - m["end"] <= 0.5:
                m["end"] = e["t"] + 1 / fps; break
        else:
            merged.append({**e, "end": e["t"] + 1 / fps})
    return merged


def audio_stats(video):
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-"])
    m = re.search(r"I:\s+(-?[\d.]+) LUFS", p.stderr[p.stderr.rfind("Summary"):])
    lufs = float(m.group(1)) if m else None
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    if len(x) < 16000: return {"lufs": lufs, "sfx_candidates": [], "bgm_floor_db": None}
    hop = 160
    n = len(x) // hop; fr = x[: n * hop].reshape(n, hop)
    spec = np.abs(np.fft.rfft(fr * np.hanning(hop), axis=1)); hi = spec[:, 40:].sum(axis=1)  # >4kHz
    flux = np.maximum(0, np.diff(hi, prepend=hi[0])); z = (flux - flux.mean()) / (flux.std() + 1e-9)
    cand, last = [], -1
    for i in np.where(z > 4)[0]:
        if i - last > 20: cand.append(round(i * hop / 16000, 2))
        last = i
    rms = 20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-9)
    return {"lufs": lufs, "sfx_candidates": cand, "bgm_floor_db": round(float(np.percentile(rms, 10)), 1)}


def analyze_video(video, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    dur = duration(str(video)); w, h = probe_size(str(video))
    cuts = [0.0] + scene_cuts(video) + [dur]
    scenes = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 0.15: continue
        mid = frame_at(video, (a + b) / 2)
        z = zoom_scale(frame_at(video, a + 0.05), frame_at(video, max(a + 0.1, b - 0.05))) if b - a > 0.6 else 1.0
        scenes.append({"start": round(a, 2), "end": round(b, 2), "len": round(b - a, 2), "zoom_in_scale": z, "_img": mid})
    # 컨택트시트
    imgs = [cv2.resize(s["_img"], (180, 320)) for s in scenes]
    rows = [np.hstack(imgs[i:i + 8] + [np.zeros_like(imgs[0])] * (8 - len(imgs[i:i + 8]))) for i in range(0, len(imgs), 8)]
    cv2.imwrite(str(out_dir / "scenes.png"), np.vstack(rows))
    for s in scenes: s.pop("_img")
    subs = subtitles(video)
    hs = [e["h"] for e in subs]; med = statistics.median(hs) if hs else 0
    for e in subs: e["is_emphasis"] = bool(med and e["h"] > med * 1.4)
    lens = [s["len"] for s in scenes]
    res = {"video": str(video), "size": [w, h], "duration": round(dur, 2), "scene_count": len(scenes),
           "cut_len": {"mean": round(statistics.mean(lens), 2), "median": round(statistics.median(lens), 2),
                       "p75": round(float(np.percentile(lens, 75)), 2), "min": min(lens), "max": max(lens)},
           "scenes": scenes, "subtitles": subs, "audio": audio_stats(video),
           "intro_texts": [e for e in subs if e["t"] < 1.5], "outro_texts": [e for e in subs if e["end"] > dur - 2.5],
           "median_text_h_px_at_540w": med}
    (out_dir / "analysis.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def suggest(results):
    """여러 영상의 중앙값으로 규칙 후보 산출 (영상 폭 540 기준 픽셀 → 1080 기준 2배)."""
    sug = {}
    sug["cut.max_shot"] = round(statistics.median(r["cut_len"]["p75"] for r in results), 2)
    sug["cut.min_shot"] = round(max(0.4, statistics.median(r["cut_len"]["median"] for r in results) * 0.5), 2)
    zs = [s["zoom_in_scale"] for r in results for s in r["scenes"] if s["zoom_in_scale"] > 1.03]
    if zs: sug["cut.zoom_levels[1]"] = round(statistics.median(zs), 2)
    norm = [e for r in results for e in r["subtitles"] if not e["is_emphasis"]]
    emph = [e for r in results for e in r["subtitles"] if e["is_emphasis"]]
    if norm:
        sug["subtitle.size"] = int(statistics.median(e["h"] for e in norm) * 2 * 0.95)
        sug["subtitle.margin_v"] = int(1920 * (1 - statistics.median(e["ycenter"] for e in norm)) - 40)
        sug["subtitle.color"] = statistics.mode(e["color"] for e in norm)
    if emph:
        sug["emphasis.size"] = int(statistics.median(e["h"] for e in emph) * 2 * 0.95)
        sug["emphasis.pos_y"] = int(1920 * statistics.median(e["ycenter"] for e in emph))
        sug["emphasis.color"] = statistics.mode(e["color"] for e in emph)
    ls = [r["audio"]["lufs"] for r in results if r["audio"]["lufs"]]
    if ls: sug["audio.loudnorm_i"] = round(statistics.median(ls), 1)
    return sug


def _hex_to_ass(h): return f"&H00{h[5:7]}{h[3:5]}{h[1:3]}".upper().replace("&H", "&H")


def analyze_all(sources, workdir, apply=False):
    root = pathlib.Path(workdir); results = []
    for i, s in enumerate(sources, 1):
        d = root / f"ref{i}"; v = fetch(s, d); print(f"[{i}/{len(sources)}] 분석: {s}")
        results.append(analyze_video(v, d))
    sug = suggest(results)
    md = ["# 레퍼런스 분석 요약", ""]
    for r in results:
        md.append(f"- {r['video']}: {r['duration']}초, 장면 {r['scene_count']}개, 평균 컷 {r['cut_len']['mean']}초, 자막 {len(r['subtitles'])}개, LUFS {r['audio']['lufs']}, 효과음 후보 {len(r['audio']['sfx_candidates'])}개")
    md += ["", "## 규칙 후보", "```json", json.dumps(sug, ensure_ascii=False, indent=1), "```"]
    (root / "analysis.md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    if apply:
        r = load_rules()
        for k, v in sug.items():
            if k.endswith("color"): v = _hex_to_ass(v)
            sec, key = k.split(".", 1)
            if key.startswith("zoom_levels["): r[sec]["zoom_levels"][1] = v
            else: r[sec][key] = v
        r["meta"] = {"status": "derived", "note": f"레퍼런스 {len(results)}개 실측값 반영 ({', '.join(sources)[:120]})"}
        save_rules(r); print("규칙 적용 완료 → rules/rules.json")
