"""자체 검수: 자막 오타 / 지루한 반복 / 화면 정지 / 개인정보 노출 / 규격."""
import re, tempfile, pathlib, json
import numpy as np, cv2
from .common import run, ff, duration, probe_size

PII = [
    ("전화번호", re.compile(r"01[016789][\s\-.]?\d{3,4}[\s\-.]?\d{4}")),
    ("전화번호", re.compile(r"0\d{1,2}[\s\-.]\d{3,4}[\s\-.]\d{4}")),
    ("주민등록번호", re.compile(r"\d{6}[\s\-]?[1-4]\d{6}")),
    ("카드번호", re.compile(r"(?:\d{4}[\s\-]){3}\d{4}")),
    ("계좌번호", re.compile(r"\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,4})?")),
    ("이메일", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("비밀키/토큰", re.compile(r"(?i)(?:sk-[A-Za-z0-9]{16,}|api[_-]?key|password|passwd|비밀번호|secret|token)")),
    ("주소", re.compile(r"[가-힣]+(?:시|도)\s*[가-힣]+(?:구|군|시)\s*[가-힣0-9]+(?:로|길|동)\s*\d+")),
]


def reduce_fix(text, rules):
    for b, g in rules["typo_fixes"].items():
        text = text.replace(b, g)
    return text


def check_subtitles(units, shown, rules):
    """대본 문장 ↔ 실제 자막 비교 + 흔한 맞춤법/형식 오류."""
    issues, joined = [], " ".join(s[2] for s in shown)
    jn = re.sub(r"\s+", "", joined)
    for i, u in enumerate(units):
        # 블록형 대본은 문구별로, 간단형은 문장 전체로 자막에 있는지 확인(표시 순서는 시간순이라 달라질 수 있음)
        targets = [c["text"].replace("|", " ") for c in u["captions"]] if "captions" in u else [u["text"]]
        for tgt in targets:
            cand = [tgt] + [reduce_fix(tgt, rules)]
            if not any(re.sub(r"\s+", "", c) in jn for c in cand):
                issues.append({"type": "typo", "msg": f"{i+1}번째 블록 문구가 자막에 온전히 없음: {tgt}"})
    for t, _, text in shown:
        if re.search(r"[ㄱ-ㅎㅏ-ㅣ]{1}(?![ㄱ-ㅎㅏ-ㅣ])", text) and not re.search(r"[ㅋㅎㅠㅜ]", text):
            issues.append({"type": "typo", "msg": f"{t:.1f}s 자모 단독 문자: {text}"})
        if re.search(r"(\S+)\s+\1\b", text):
            issues.append({"type": "typo", "msg": f"{t:.1f}s 같은 단어 연속 반복: {text}"})
        if "  " in text or re.search(r"\s[,.!?]", text):
            issues.append({"type": "typo", "msg": f"{t:.1f}s 띄어쓰기/문장부호 이상: {text}"})
        for bad in rules["typo_fixes"]:
            if bad in text:
                issues.append({"type": "typo", "msg": f"{t:.1f}s 맞춤법({bad}→{rules['typo_fixes'][bad]}): {text}"})
    return issues


def check_freeze(video, rules):
    q = rules["qa"]
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", video, "-vf", f"freezedetect=n={q['freeze_noise_db']}dB:d={q['freeze_min_sec']}",
             "-an", "-f", "null", "-"])
    st = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", p.stderr)]
    du = [float(x) for x in re.findall(r"freeze_duration: ([\d.]+)", p.stderr)]
    return [{"type": "freeze", "t": s, "dur": d, "msg": f"{s:.1f}s부터 {d:.1f}초 화면 정지"} for s, d in zip(st, du)]


def _frames(video, fps):
    d = pathlib.Path(tempfile.mkdtemp(prefix="qah_"))
    ff(["-i", video, "-vf", f"fps={fps},scale=32:56", "-pix_fmt", "gray", str(d / "f_%05d.png")])
    return np.array([cv2.imread(str(f), cv2.IMREAD_GRAYSCALE).astype(np.float32) for f in sorted(d.glob("f_*.png"))])


def check_repetition(video, pieces, rules):
    q = rules["qa"]; issues = []; fps = q["qa_fps"]
    fr = _frames(video, fps); n = len(fr)
    diff = np.array([np.abs(fr[i] - fr[i - 1]).mean() for i in range(1, n)])  # diff[i-1]: i-1 → i
    # (a) 오래 거의 변화 없는 화면 (컷·줌 전환이 있으면 diff 가 커져 구간이 끊긴다)
    start = 0
    for i in range(1, n + 1):
        if i == n or diff[i - 1] > q["static_diff"]:
            if (i - start) / fps >= q["static_run_sec"]:
                issues.append({"type": "static", "t": start / fps, "dur": (i - start) / fps,
                               "msg": f"{start/fps:.1f}s부터 {(i-start)/fps:.1f}초 동안 화면 변화 거의 없음"})
            start = i
    # (b) 같은 장면이 비인접 위치에서 다시 나옴 (1초 간격 샘플 윈도우 비교)
    step = fps; w = int(q["dup_window_sec"]); sm = fr[::step]; m = len(sm)
    for i in range(0, m - w):
        if sm[i:i + w].std(axis=0).mean() < 2:  # 정지 장면은 (a)에서 처리
            continue
        for j in range(i + w * 2, m - w):
            if np.abs(sm[i:i + w] - sm[j:j + w]).mean() < q["dup_diff"]:
                issues.append({"type": "repeat", "t": float(j), "msg": f"{i}s 장면이 {j}s에 거의 똑같이 반복"}); break
    # (c) 컷 길이가 기계적으로 똑같음
    durs = [round(p["real_dur"], 1) for p in pieces]; mm = q["monotone_shots"]
    for i in range(len(durs) - mm + 1):
        if max(durs[i:i + mm]) - min(durs[i:i + mm]) <= 0.1:
            issues.append({"type": "monotone", "msg": f"컷 {i+1}~{i+mm} 길이가 모두 {durs[i]}초로 단조로움"}); break
    seen, out = set(), []
    for it in issues:
        k = (it["type"], round(it.get("t", 0)))
        if k not in seen: seen.add(k); out.append(it)
    return out


_ocr = None
def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


def scan_privacy(video, rules, ignore_texts=()):
    """프레임을 샘플링해 OCR → 개인정보 패턴이 보이는 (시간, 박스) 반환."""
    pv = rules["privacy"]; fps = pv["sample_fps"]
    d = pathlib.Path(tempfile.mkdtemp(prefix="qap_"))
    ff(["-i", video, "-vf", f"fps={fps}", str(d / "f_%05d.png")])
    ocr, hits = _get_ocr(), []
    for k, f in enumerate(sorted(d.glob("f_*.png"))):
        res, _ = ocr(str(f))
        for box, text, conf in (res or []):
            t = re.sub(r"[Oo]", "0", text) if re.search(r"\d", text) else text
            for label, rx in PII:
                if (rx.search(text) or rx.search(t)) and not any(text.strip() in ig for ig in ignore_texts):
                    xs = [p[0] for p in box]; ys = [p[1] for p in box]
                    hits.append({"type": "pii", "label": label, "t": k / fps, "text": text,
                                 "box": (min(xs), min(ys), max(xs), max(ys)),
                                 "msg": f"{k/fps:.1f}s {label} 노출: '{text}'"})
                    break
    return hits


def blur_boxes(video, hits, rules, out):
    """개인정보 박스를 흐림 처리한 새 mp4 를 만든다."""
    pv = rules["privacy"]; W, H = probe_size(video)
    merged = {}
    for h in hits:  # 같은 위치의 시간 구간 병합
        x0, y0, x1, y1 = h["box"]; key = (round(x0 / 40), round(y0 / 40))
        m = merged.setdefault(key, {"box": [x0, y0, x1, y1], "t0": h["t"], "t1": h["t"]})
        m["box"] = [min(m["box"][0], x0), min(m["box"][1], y0), max(m["box"][2], x1), max(m["box"][3], y1)]
        m["t0"] = min(m["t0"], h["t"]); m["t1"] = max(m["t1"], h["t"])
    chain, last = [], "[0:v]"
    for i, m in enumerate(merged.values()):
        x0, y0, x1, y1 = m["box"]; pad = pv["pad_px"]
        x0 = max(0, int(x0 - pad)); y0 = max(0, int(y0 - pad))
        w = min(W - x0, int(x1 - x0 + 2 * pad)); h = min(H - y0, int(y1 - y0 + 2 * pad))
        w -= w % 2; h -= h % 2
        t0 = max(0, m["t0"] - pv["pad_sec"]); t1 = m["t1"] + 1 / pv["sample_fps"] + pv["pad_sec"]
        chain.append(f"{last}split[a{i}][b{i}];[b{i}]crop={w}:{h}:{x0}:{y0},boxblur=luma_radius=min(min(w\\,h)/2-1\\,{pv['blur']}):luma_power=3:chroma_radius=min(min(cw\\,ch)/2-1\\,{pv['blur']}):chroma_power=3[c{i}];"
                     f"[a{i}][c{i}]overlay={x0}:{y0}:enable='between(t,{t0:.2f},{t1:.2f})'[v{i}]")
        last = f"[v{i}]"
    ff(["-i", video, "-filter_complex", ";".join(chain), "-map", last, "-map", "0:a", "-c:v", "libx264", "-preset", "medium",
        "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", out])


def check_format(video, rules):
    c = rules["canvas"]; issues = []
    w, h = probe_size(video); d = duration(video)
    if (w, h) != (c["w"], c["h"]):
        issues.append({"type": "format", "msg": f"해상도 {w}x{h} (기대 {c['w']}x{c['h']})"})
    if d > rules["qa"]["max_total_sec"]:
        issues.append({"type": "length", "msg": f"길이 {d:.1f}초 (권장 {rules['qa']['max_total_sec']}초 이하)"})
    return issues


def run_all(video, units, shown, pieces, rules, ignore_texts=()):
    issues = check_format(video, rules) + check_subtitles(units, shown, rules) + check_freeze(video, rules) \
        + check_repetition(video, pieces, rules)
    if rules["privacy"]["enabled"]:
        issues += scan_privacy(video, rules, ignore_texts)
    return issues
