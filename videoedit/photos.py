"""영상이 없을 때: 사진 + 대본 블록 시간(예: '## HOOK 0-2')으로 초안 영상을 만든다. 목소리 없음(자막·효과음·배경음만)."""
import json, re, pathlib
from .common import load_rules, ff, duration, run
from .script_plan import parse_script
from . import render, qa


def _compose(img, out, rules):
    """사진 → 1080x1920 정지 이미지(세로 사진은 꽉 채움, 그 외는 블러 배경 + 전체 보임)."""
    W, H = rules["canvas"]["w"], rules["canvas"]["h"]
    from PIL import Image
    w, h = Image.open(img).size
    if w / h > 0.7:
        fc = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bg];"
              f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    else:
        fc = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    ff(["-i", img, "-vf", fc, "-frames:v", "1", out])


def build(images_dir, script, mapping, out):
    rules = load_rules(); W, H, fps = rules["canvas"]["w"], rules["canvas"]["h"], rules["canvas"]["fps"]
    units = parse_script(script); mp = json.loads(pathlib.Path(mapping).read_text(encoding="utf-8"))["blocks"]
    out = pathlib.Path(out).resolve(); out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / (out.stem + "_work"); work.mkdir(exist_ok=True)
    imgs = {p.name[:8]: p for p in pathlib.Path(images_dir).glob("*.jp*g")}
    pieces, files, flip = [], [], 0
    for ui, (u, blk) in enumerate(zip(units, mp)):
        a, b = map(float, re.search(r"(\d+)\s*-\s*(\d+)\s*$", u["raw"]).groups())
        d = (b - a) / len(blk)
        for k, (key, (fx, fy)) in enumerate(blk):
            still = work / f"still_{ui}_{k}.png"; _compose(imgs[key], still, rules)
            n = int(d * fps); zin = flip % 2 == 0; flip += 1
            z = f"1+0.14*on/{n}" if zin else f"1.14-0.14*on/{n}"
            if ui == 0: z = f"1.18-0.10*on/{n}"  # 훅: 크게 시작해 살짝 빠지며 전신을 보여줌
            vf = (f"scale=2160:3840,zoompan=z='{z}':x='(iw-iw/zoom)*{fx}':y='(ih-ih/zoom)*{fy}':d={n}:s={W}x{H}:fps={fps},"
                  f"setsar=1,format=yuv420p")
            piece = work / f"piece_{len(files):03d}.mp4"
            ff(["-loop", "1", "-i", still, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", vf, "-t", f"{d:.3f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", piece])
            pieces.append({"unit": ui, "real_dur": duration(piece)}); files.append(piece)
    (work / "concat.txt").write_text("".join(f"file '{f.name}'\n" for f in files))
    base = work / "base.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", work / "concat.txt", "-c", "copy", base])
    rules["bgm"]["volume_db"] = -13  # 목소리가 없으니 배경음을 키움
    ass, sfx, total, typo, shown, _ = render.build_ass(units, pieces, rules, work)
    render.finalize(base.resolve(), ass, sfx, total, rules, out, work)
    issues = qa.check_format(str(out), rules) + qa.check_subtitles(units, shown, rules) + qa.check_freeze(str(out), rules) \
        + qa.check_repetition(str(out), pieces, rules) + qa.scan_privacy(str(out), rules)
    return out, issues, total, typo
