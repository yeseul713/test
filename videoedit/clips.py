"""촬영본 컷 편집(목소리 나중에 입히는 버전): 블록 시간(예: '## HOOK 0-2')과 클립 매핑으로 하드컷 편집 + 자막, 무음 트랙."""
import json, re, pathlib
from .common import load_rules, ff, duration, run
from .script_plan import parse_script
from . import render, qa


def _drift_sq(drift, d, W):
    """정지한 화면이 멈춰 보이지 않도록 아주 약하게 천천히 확대(가운데 기준)."""
    if not drift:
        return ""
    return f",scale=w='iw*(1+{drift}*t/{d:.3f})':h=-2:eval=frame,crop={W}:1080:(iw-{W})/2:(ih-1080)/2"


def _piece_pair(src_a, ss_a, src_b, ss_b, d, out, rules, drift=0.0, zoomcrop=None):
    """로우|하이 같은 두 영상을 좌우로 나란히: 각 540x1080 칸, 위아래 검정(1편의 A/B 분할 컷과 같은 모양)."""
    W, H, fps = rules["canvas"]["w"], rules["canvas"]["h"], rules["canvas"]["fps"]
    half = W // 2
    fr = rules["framing"]["pair"]
    if zoomcrop:  # 하체 확대 비교: 원본(4K)에서 (cx,cy) 중심 w×h 비율로 잘라 칸(540x1080)에 채움. 오른쪽 칸은 cy2 로 허리선 높이 보정
        def mk(zc, cy):
            return (f"crop=iw*{zc['w']}:ih*{zc['h']}:iw*({zc['cx']}-{zc['w']}/2):ih*({cy}-{zc['h']}/2),"
                    f"scale={half}:1080,setsar=1,fps={fps}")
        cell, cell2 = mk(zoomcrop, zoomcrop["cy"]), mk(zoomcrop, zoomcrop.get("cy2", zoomcrop["cy"]))
    else:
        cell2 = None
    if not zoomcrop:
        cell = f"scale={fr['sw']}:{fr['sh']},crop={half}:1080:{fr['x']}:{fr['y']},setsar=1,fps={fps}"
    fc = f"[0:v]{cell}[a];[1:v]{cell2 or cell}[b];[a][b]hstack{_drift_sq(drift, d, W)},pad={W}:{H}:0:(oh-ih)/2:black,format=yuv420p[v]"
    ff(["-ss", f"{ss_a:.3f}", "-t", f"{d:.3f}", "-i", src_a, "-ss", f"{ss_b:.3f}", "-t", f"{d:.3f}", "-i", src_b,
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", fc, "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", "-t", f"{d:.3f}", out])


def _piece(src, ss, d, mode, out, rules, crop=None, drift=0.0):
    W, H, fps = rules["canvas"]["w"], rules["canvas"]["h"], rules["canvas"]["fps"]
    if mode == "split_square":  # 정사각 A/B 분할 영상: 가로 꽉 채우고 위아래 검정 (1편의 분할 컷과 같은 모양)
        vf = f"scale={W}:{W},pad={W}:{H}:0:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p"
    else:
        pre = ""
        if crop:  # 확대 크롭: 화면 비율(9:16)을 유지한 채 (cx,cy) 중심으로 zoom 배 확대
            z, cx, cy = crop["zoom"], crop["cx"], crop["cy"]
            pre = (f"crop=w=iw/{z}:h=ih/{z}:x=max(0\\,min(iw-iw/{z}\\,{cx}*iw-iw/{z}/2)):y=max(0\\,min(ih-ih/{z}\\,{cy}*ih-ih/{z}/2)),")
        dr = (f",scale=w='iw*(1+{drift}*t/{d:.3f})':h=-2:eval=frame,crop={W}:{H}:(iw-{W})/2:(ih-{H})/2" if drift else "")
        vf = pre + f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{dr},setsar=1,fps={fps},format=yuv420p"
    ff(["-ss", f"{ss:.3f}", "-t", f"{d:.3f}", "-i", src, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v", "-map", "1:a",
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", "-t", f"{d:.3f}", out])


def build(clip_dir, script, mapping, out):
    rules = load_rules()
    units = parse_script(script); mp = json.loads(pathlib.Path(mapping).read_text(encoding="utf-8"))["blocks"]
    out = pathlib.Path(out).resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / (out.stem + "_work"); work.mkdir(exist_ok=True)
    files_by = {p.stem: p for p in pathlib.Path(clip_dir).glob("*.mp4")}
    find = lambda key: next(p for k, p in files_by.items() if k.startswith(key))
    cd = pathlib.Path(clip_dir); orig_dir = cd.parent / cd.name.rstrip("p")
    find_orig = lambda key: next(p for p in sorted(orig_dir.iterdir()) if p.stem.startswith(key))
    pieces, files = [], []
    for ui, (u, blk) in enumerate(zip(units, mp)):
        a, b = map(float, re.search(r"([\d.]+)\s*-\s*([\d.]+)\s*$", u["raw"]).groups())
        ws = [c.get("w", 1.0) for c in blk]; tot = sum(ws)
        for c, w in zip(blk, ws):
            src = find_orig(c["f"]) if c.get("orig") else find(c["f"]); d = (b - a) * w / tot
            if c["ss"] + d > duration(str(src)) + 0.05:
                raise SystemExit(f"{c['f']} 의 {c['ss']}초부터 {d:.1f}초는 영상 길이를 넘습니다.")
            piece = work / f"piece_{len(files):03d}.mp4"
            if c.get("mode") == "split_pair":
                if c.get("orig"):
                    _piece_pair(str(find_orig(c["f"])), c["ss"], str(find_orig(c["f2"])), c["ss2"], d, piece, rules, 0.0, c.get("zoomcrop"))
                else:
                    _piece_pair(str(src), c["ss"], str(find(c["f2"])), c["ss2"], d, piece, rules, c.get("drift", 0.0))
                pieces.append({"unit": ui, "real_dur": duration(piece)}); files.append(piece); continue
            _piece(str(src), c["ss"], d, c.get("mode", "cover"), piece, rules,
                   {"zoom": c["zoom"], "cx": c["cx"], "cy": c["cy"]} if "zoom" in c else None, c.get("drift", 0.0))
            pieces.append({"unit": ui, "real_dur": duration(piece)}); files.append(piece)
    (work / "concat.txt").write_text("".join(f"file '{f.name}'\n" for f in files))
    base = work / "base.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", work / "concat.txt", "-c", "copy", base])
    rules["audio"]["loudnorm"] = False
    ass, sfx, total, typo, shown, _ = render.build_ass(units, pieces, rules, work)
    render.finalize(base.resolve(), ass, sfx, total, rules, out, work)
    issues = qa.check_format(str(out), rules) + qa.check_subtitles(units, shown, rules) + qa.check_freeze(str(out), rules) \
        + qa.check_repetition(str(out), pieces, rules) + qa.scan_privacy(str(out), rules)
    from .export import export_like_ref
    export_like_ref(out)  # 1편과 같은 규격의 .mov 도 함께 저장
    return out, issues, total, typo
