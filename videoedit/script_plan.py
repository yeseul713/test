"""대본 파싱 + 촬영본에서 대본 줄에 맞는 구간 찾기(컷 편집 계획)."""
import re, difflib, pathlib
from .common import speech_segments, list_videos, duration

EMPH = re.compile(r"\*([^*]+)\*")
BROLL = re.compile(r"\[자료\s*:\s*([^\]]+)\]")


def _parse_blocks(text):
    """'## 제목 시간' 블록 형식: 말/크게/자막/태그/메모. 자막은 말과 별개(화면용 문구)."""
    units, cur = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            cur = {"raw": line[3:], "text": "", "emph": [], "broll": None, "captions": [], "memo": ""}
            units.append(cur); continue
        m = re.match(r"(\+?)(말|크게|자막|작게|태그|선|표시|메모|CTA|위치|큰자막y)\s*:\s*(.+)", line)
        if not m or cur is None:
            continue
        plus, k, v = m.groups()
        if k == "말": cur["text"] = v
        elif k == "메모": cur["memo"] = v
        elif k == "큰자막y": cur["big_y"] = float(v)
        elif k == "위치":
            cur["side"] = "left" if "왼" in v else "right" if "오" in v else "center"
            if "아래" in v:
                cur["plain_y"] = 0.86  # 분할 영상처럼 아래가 검은 영역일 때 일반 자막을 더 아래로
            if v.startswith("위") or " 위" in v or "위," in v:
                cur["big_y"] = 0.115  # 위쪽 검은 영역에 큰 자막
        else: cur["captions"].append({"text": v, "kind": {"크게": "big", "자막": "plain", "태그": "chip", "CTA": "cta", "작게": "small", "선": "line", "표시": "mark"}[k], "with_prev": bool(plus)})
    for u in units:
        if not u["text"]:
            raise SystemExit(f"'{u['raw']}' 블록에 '말:' 이 없습니다.")
        u["expect"] = " ".join(c["text"].replace("|", " ") for c in u["captions"] if c["kind"] not in ("line", "mark"))
    return units


def parse_script(path):
    txt = pathlib.Path(path).read_text(encoding="utf-8")
    if re.search(r"^## ", txt, re.M):
        return _parse_blocks(txt)
    units = []
    for raw in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        broll = None
        m = BROLL.search(line)
        if m:
            broll = m.group(1).strip(); line = BROLL.sub("", line).strip()
        emph = EMPH.findall(line)
        plain = EMPH.sub(lambda m: m.group(1), line)
        units.append({"raw": line, "text": plain, "emph": emph, "broll": broll})
    if not units:
        raise SystemExit("대본에서 문장을 찾지 못했습니다.")
    return units


def _norm(s):
    return re.sub(r"[^0-9a-zA-Z가-힣]", "", s)


def _transcribe(files):
    """faster-whisper 가 모델을 받을 수 있을 때만 사용. 실패하면 None."""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("small", device="cpu", compute_type="int8")
    except Exception as e:
        print(f"[알림] 음성인식 모델 사용 불가({type(e).__name__}) → 무음 기반 구간 배정으로 대체")
        return None
    segs = []
    for f in files:
        res, _ = model.transcribe(str(f), language="ko", vad_filter=True)
        for s in res:
            segs.append({"file": str(f), "start": s.start, "end": s.end, "text": s.text})
    return segs


def _match_asr(units, asr):
    """줄마다 ASR 구간 묶음(최대 4개) 중 가장 비슷한 것을 고른다. 재촬영(NG 후 다시 말함)이면 마지막 것을 택한다."""
    used = set(); out = []
    for u in units:
        tgt = _norm(u["text"]); best = (0, None)
        for i in range(len(asr)):
            for j in range(i, min(i + 4, len(asr))):
                if asr[i]["file"] != asr[j]["file"] or any(k in used for k in range(i, j + 1)):
                    continue
                sc = difflib.SequenceMatcher(None, tgt, _norm("".join(a["text"] for a in asr[i:j + 1]))).ratio()
                if sc >= best[0] - 0.02 and sc > 0.5:
                    best = (max(sc, best[0]), (i, j))
        if not best[1]:
            out.append(None); continue
        i, j = best[1]; used.update(range(i, j + 1))
        out.append((asr[i]["file"], asr[i]["start"], asr[j]["end"]))
    return out


def _assign_proportional(units, segs, rules):
    """무음 기반 말구간 목록을 글자 수 비율로 대본 줄에 배정."""
    spc = rules["cut"]["sec_per_char"]
    total_speech = sum(e - s for _, s, e in segs)
    weights = [max(1, len(_norm(u["text"]))) for u in units]
    if len(segs) == len(units):
        return list(segs)
    # 말구간을 이어붙인 '말 시간선' 위에서 글자 비율로 경계를 나눈다
    cum, pos = [], 0.0
    for f, s, e in segs:
        cum.append((pos, pos + e - s, f, s)); pos += e - s
    def at(x):
        for a, b, f, s in cum:
            if x <= b + 1e-9:
                return f, s + (x - a)
        a, b, f, s = cum[-1]; return f, s + (b - a)
    out, acc, W = [], 0.0, sum(weights)
    for w in weights:
        x0, x1 = total_speech * acc / W, total_speech * (acc + w) / W
        (f0, t0), (f1, t1) = at(x0 + 1e-6), at(x1)
        out.append((f0, t0, t1) if f0 == f1 else (f0, t0, t0 + (x1 - x0)))
        acc += w
    return out


def plan_cuts(units, footage_dir, rules):
    files = list_videos(footage_dir)
    if not files:
        raise SystemExit(f"{footage_dir} 에 영상 파일이 없습니다.")
    asr = _transcribe(files)
    matched = _match_asr(units, asr) if asr else None
    if matched and sum(m is None for m in matched) <= len(units) * 0.3:
        mode = "음성인식+대본 매칭"
        fb = None
    else:
        mode, matched = "무음구간+글자비율 배정", None
    if matched is None:
        segs = [(str(f), s, e) for f in files for s, e in speech_segments(str(f), rules)]
        if not segs:
            raise SystemExit("말하는 구간을 찾지 못했습니다(소리가 없는 촬영본?).")
        matched = _assign_proportional(units, segs, rules)
    else:
        segs = None
        # 매칭 실패 줄은 앞 줄 직후로 대체
        for i, m in enumerate(matched):
            if m is None:
                prev = matched[i - 1] if i and matched[i - 1] else None
                matched[i] = (prev[0], prev[2], prev[2] + 1.5) if prev else (str(files[0]), 0, 1.5)
    c = rules["cut"]
    for u, m in zip(units, matched):
        f, s, e = m
        u["src"] = (f, max(0.0, s - c["pad_before"]), min(duration(f), e + c["pad_after"]))
    return mode
