"""① 컷 편집(조각 렌더) → ② 자막/강조자막/효과음/배경음 → ③ mp4 저장."""
import pathlib, re, math
from .common import ff, run, duration, has_audio, probe_size, FONTS_DIR
from . import assets


def _even(x):
    return int(round(x / 2) * 2)


# ───────────── ① 컷 편집 ─────────────
def make_pieces(units, rules, extra_splits=()):
    c, hk = rules["cut"], rules["hook"]
    zl = c["zoom_levels"]
    pieces, flip = [], 0
    for i, u in enumerate(units):
        f, s, e = u["src"]; d = e - s
        if u.get("broll"):
            n = 1
        else:
            n = max(1, round(d / c["max_shot"]))
            while n > 1 and d / n < c["min_shot"]:
                n -= 1
        step = d / n
        for k in range(n):
            z = zl[flip % len(zl)]; flip += 1
            if i == 0 and k == 0 and hk["enabled"]:
                z = hk["zoom"]
            pieces.append({"unit": i, "file": f, "start": s + k * step, "dur": step, "zoom": z,
                           "broll": u.get("broll") if k == 0 else None})
    # 정지/지루 구간 수정: 해당 출력 시각을 포함한 조각을 둘로 쪼개고 뒤쪽은 다른 배율로
    for t in sorted(extra_splits, reverse=True):
        acc = 0.0
        for idx, p in enumerate(pieces):
            if acc <= t < acc + p["dur"] and p["dur"] > 1.0:
                h = p["dur"] / 2
                a = dict(p, dur=h)
                b = dict(p, start=p["start"] + h, dur=h, broll=None,
                         zoom=zl[1] if p["zoom"] == zl[0] else zl[0] if p["zoom"] != zl[0] else zl[1])
                if b["zoom"] == p["zoom"]:
                    b["zoom"] = round(p["zoom"] * 1.1, 3)
                pieces[idx:idx + 1] = [a, b]
                break
            acc += p["dur"]
    return pieces


def _vchain(src_label, src_size, z, rules):
    """원본 → 1080x1920. 세로 영상은 꽉 채워 자르고, 가로 영상은 블러 배경 위에 전체가 보이게 얹는다. z=확대 배율."""
    W, H, fps = rules["canvas"]["w"], rules["canvas"]["h"], rules["canvas"]["fps"]
    sw, sh = src_size
    if sw > sh and rules["canvas"].get("landscape_mode") == "fit_blur":
        return (f"{src_label}split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:5[bg];"
                f"[b]scale={_even(W*z)}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p[vout]")
    return (f"{src_label}scale={_even(W*z)}:{_even(H*z)}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"setsar=1,fps={fps},format=yuv420p[vout]")


def render_pieces(pieces, rules, work, base_dir):
    files = []
    for i, p in enumerate(pieces):
        out = work / f"piece_{i:03d}.mp4"
        d = p["dur"]
        af = f"aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d=0.015,afade=t=out:st={max(0,d-0.03):.3f}:d=0.03"
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "160k", "-t", f"{d:.3f}", out]
        ss = ["-ss", f"{p['start']:.3f}", "-t", f"{d:.3f}", "-i", p["file"]]
        if p["broll"]:
            bp = pathlib.Path(p["broll"])
            if not bp.is_absolute():
                bp = base_dir / bp
            fc = _vchain("[1:v]", probe_size(bp), rules["broll"]["zoom_push"], rules)
            args = ss + ["-stream_loop", "-1", "-i", bp, "-filter_complex", fc, "-map", "[vout]", "-map", "0:a", "-af", af] + enc
        elif has_audio(p["file"]):
            fc = _vchain("[0:v]", probe_size(p["file"]), p["zoom"], rules)
            args = ss + ["-filter_complex", fc, "-map", "[vout]", "-map", "0:a", "-af", af] + enc
        else:
            fc = _vchain("[0:v]", probe_size(p["file"]), p["zoom"], rules)
            args = ss + ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex", fc, "-map", "[vout]", "-map", "1:a"] + enc
        ff(args)
        p["real_dur"] = duration(out)
        files.append(out)
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{f.name}'\n" for f in files))
    base = work / "base.mp4"
    ff(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", base])
    return base


# ───────────── ② 자막 ─────────────
def _wrap(text, maxc, maxl):
    words, lines, cur = text.split(), [], ""
    for w in words:
        while len(w) > maxc:  # 공백 없는 긴 단어는 강제 분할
            if cur: lines.append(cur); cur = ""
            lines.append(w[:maxc]); w = w[maxc:]
        if len(cur) + len(w) + (1 if cur else 0) <= maxc:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur); cur = w
    if cur: lines.append(cur)
    return [lines[i:i + maxl] for i in range(0, len(lines), maxl)]


def _ts(t):
    t = max(0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def fix_typos(text, rules, log):
    for bad, good in rules["typo_fixes"].items():
        if bad in text:
            log.append((bad, good)); text = text.replace(bad, good)
    return text


def build_ass(units, pieces, rules, work):
    W, H = rules["canvas"]["w"], rules["canvas"]["h"]
    st, em, hk, ou = rules["subtitle"], rules["emphasis"], rules["hook"], rules["outro"]
    # 유닛별 출력 시각
    ustart, uend, t = {}, {}, 0.0
    for p in pieces:
        ustart.setdefault(p["unit"], t); t += p["real_dur"]; uend[p["unit"]] = t
    total = t
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Sub,{st['font']},{st['size']},{st['color']},&H000000FF,{st['outline_color']},&H80000000,{-1 if st['bold'] else 0},0,0,0,100,100,0,0,1,{st['outline']},{st['shadow']},2,60,60,{st['margin_v']},1
Style: Emph,{st['font']},{em['size']},{em['color']},&H000000FF,{st['outline_color']},&H80000000,-1,0,0,0,100,100,0,0,1,{em['outline']},3,5,40,40,0,1
Style: Hook,{st['font']},{hk['size']},{em['color']},&H000000FF,{st['outline_color']},&H80000000,-1,0,0,0,100,100,0,0,1,{em['outline']},3,8,60,60,{hk['pos_y']},1
Style: Cta,{st['font']},{ou['size']},&H00FFFFFF,&H000000FF,{st['outline_color']},&H80000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    ev, sfx, typo_log, shown = [], [], [], []
    pop = f"{{\\fscx{em['start_scale']}\\fscy{em['start_scale']}\\t(0,{em['pop_ms']},\\fscx{em['pop_scale']}\\fscy{em['pop_scale']})\\t({em['pop_ms']},{em['pop_ms']*2},\\fscx100\\fscy100)}}"
    for i, u in enumerate(units):
        a, b = ustart[i], uend[i]; dur = b - a
        text = fix_typos(u["text"], rules, typo_log)
        emph = [fix_typos(x, rules, typo_log) for x in u["emph"]]
        hook_end = a + min(hk["duration"], dur) if (i == 0 and hk["enabled"]) else a
        if i == 0 and hk["enabled"]:
            hl = "\\N".join(l for pg in _wrap(text, hk["max_chars_per_line"], 9) for l in pg)
            ev.append(f"Dialogue: 1,{_ts(a)},{_ts(hook_end)},Hook,,0,0,0,,{pop}{hl}")
            shown.append((a, hook_end, text))
            if rules["sfx"]["hook_hit"]:
                sfx.append((a, "hit"))
        # 일반 자막 (강조어는 노란색)
        pages = _wrap(text, st["max_chars_per_line"], st["max_lines"])
        weights = [max(1, sum(len(l) for l in pg)) for pg in pages]; tw = sum(weights); acc = a
        for pg, w in zip(pages, weights):
            s0, e0 = acc, acc + dur * w / tw; acc = e0
            if e0 <= hook_end: continue
            s0 = max(s0, hook_end)
            body = "\\N".join(pg)
            for ew in emph:
                body = body.replace(ew, f"{{\\c{st['highlight_color']}}}{ew}{{\\r}}")
            ev.append(f"Dialogue: 0,{_ts(s0)},{_ts(e0)},Sub,,0,0,0,,{body}")
            shown.append((s0, e0, " ".join(pg)))
        # 강조 자막: 단어가 말해지는 순간 크게 팝
        for ew in emph:
            pos = text.find(ew.replace("*", "")) if ew in text else text.find(ew)
            frac = max(0, pos) / max(1, len(text))
            s0 = a + dur * frac
            if i == 0 and hk["enabled"] and s0 < hook_end:
                continue  # 훅 구간엔 훅 자막이 이미 강조 역할
            e0 = min(s0 + em["hold"], b + 0.2)
            ev.append(f"Dialogue: 2,{_ts(s0)},{_ts(e0)},Emph,,0,0,0,,{{\\pos({em['pos_x']},{em['pos_y']})}}{pop}{ew}")
            shown.append((s0, e0, ew))
            if rules["sfx"]["emphasis_pop"]:
                sfx.append((s0, "pop"))
        if i > 0 and rules["sfx"]["unit_whoosh"]:
            sfx.append((a, "whoosh"))
    # 아웃트로 CTA
    if ou["enabled"] and total > ou["duration"] + 1:
        s0 = total - ou["duration"]
        ev.append(f"Dialogue: 3,{_ts(s0)},{_ts(total)},Cta,,0,0,0,,{{\\pos(540,{ou['pos_y']})\\fad(250,250)}}{ou['cta_text']}")
        shown.append((s0, total, ou["cta_text"]))
        if rules["sfx"]["outro_ding"]:
            sfx.append((s0, "ding"))
    path = work / "subs.ass"
    path.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")
    return path, sorted(sfx), total, typo_log, shown, ustart


# ───────────── ② 소리 + 자막 합성 → ③ mp4 ─────────────
def finalize(base, ass, sfx, total, rules, out, work):
    s, b, au = rules["sfx"], rules["bgm"], rules["audio"]
    inputs = ["-i", str(base)]
    chains = ["[0:a]aformat=sample_rates=48000:channel_layouts=stereo[vo]"]
    mix = ["[vo]"]
    idx = 1
    if b["enabled"]:
        inputs += ["-stream_loop", "-1", "-i", assets.find("bgm")]
        fi = b["fade_in"]
        chains.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={b['volume_db']}dB,afade=t=in:d={fi}[bg]")
        if b["duck"]:
            chains.append("[vo]asplit=2[vo][sc]")
            chains.append("[bg][sc]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=400[bgd]")
            mix.append("[bgd]")
        else:
            mix.append("[bg]")
        idx += 1
    for k, (t, kind) in enumerate(sfx):
        inputs += ["-i", assets.find(kind)]
        ms = max(0, int((t - s["lead"]) * 1000))
        chains.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={ms}|{ms},volume={s['volume_db']}dB[s{k}]")
        mix.append(f"[s{k}]"); idx += 1
    fade = rules["outro"]["audio_fade"]
    chains.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first:dropout_transition=0,"
                  f"loudnorm=I={au['loudnorm_i']}:TP={au['loudnorm_tp']}:LRA={au['loudnorm_lra']},"
                  f"atrim=0:{total:.3f},afade=t=out:st={max(0,total-fade):.3f}:d={fade}[aout]")
    fc = ";".join(chains)
    # ass 는 작업 폴더 기준 상대경로로 (경로 이스케이프 문제 회피)
    vf = f"[0:v]ass=subs.ass:fontsdir={FONTS_DIR}[vout]"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
        ["-filter_complex", fc + ";" + vf, "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(rules["canvas"]["fps"]),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)], cwd=str(work))
