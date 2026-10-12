"""촬영본 컷 편집(목소리 나중에 입히는 버전): 블록 시간(예: '## HOOK 0-2')과 클립 매핑으로 하드컷 편집 + 자막, 무음 트랙."""
import json, re, pathlib
from .common import load_rules, ff, duration, run
from .script_plan import parse_script
from . import render, qa


def _piece(src, ss, d, mode, out, rules, crop=None):
    W, H, fps = rules["canvas"]["w"], rules["canvas"]["h"], rules["canvas"]["fps"]
    if mode == "split_square":  # 정사각 A/B 분할 영상: 가로 꽉 채우고 위아래 검정 (1편의 분할 컷과 같은 모양)
        vf = f"scale={W}:{W},pad={W}:{H}:0:(oh-ih)/2:black,setsar=1,fps={fps},format=yuv420p"
    else:
        pre = ""
        if crop:  # 확대 크롭: 화면 비율(9:16)을 유지한 채 (cx,cy) 중심으로 zoom 배 확대
            z, cx, cy = crop["zoom"], crop["cx"], crop["cy"]
            pre = (f"crop=w=iw/{z}:h=ih/{z}:x=max(0\\,min(iw-iw/{z}\\,{cx}*iw-iw/{z}/2)):y=max(0\\,min(ih-ih/{z}\\,{cy}*ih-ih/{z}/2)),")
        vf = pre + f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={fps},format=yuv420p"
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
        a, b = map(float, re.search(r"(\d+)\s*-\s*(\d+)\s*$", u["raw"]).groups())
        ws = [c.get("w", 1.0) for c in blk]; tot = sum(ws)
        for c, w in zip(blk, ws):
            src = find_orig(c["f"]) if c.get("orig") else find(c["f"]); d = (b - a) * w / tot
            if c["ss"] + d > duration(str(src)) + 0.05:
                raise SystemExit(f"{c['f']} 의 {c['ss']}초부터 {d:.1f}초는 영상 길이를 넘습니다.")
            piece = work / f"piece_{len(files):03d}.mp4"
            _piece(str(src), c["ss"], d, c.get("mode", "cover"), piece, rules,
                   {"zoom": c["zoom"], "cx": c["cx"], "cy": c["cy"]} if "zoom" in c else None)
            pieces.append({"unit": ui, "real_dur": duration(piece)}); files.append(piece)
    (work / "concat.txt").write_text("".join(f"file '{f.name}'\n" for f in files))
    base = work / "base.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", work / "concat.txt", "-c", "copy", base])
    rules["audio"]["loudnorm"] = False
    ass, sfx, total, typo, shown, _ = render.build_ass(units, pieces, rules, work)
    render.finalize(base.resolve(), ass, sfx, total, rules, out, work)
    issues = qa.check_format(str(out), rules) + qa.check_subtitles(units, shown, rules) + qa.check_freeze(str(out), rules) \
        + qa.check_repetition(str(out), pieces, rules) + qa.scan_privacy(str(out), rules)
    return out, issues, total, typo
