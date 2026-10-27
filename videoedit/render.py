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


def _cap(font, size, x, y, text, fill="&HFFFFFF&", bord=0, bcol="&H000000&", shad=0, extra=""):
    return f"{{\\an5\\pos({x},{y})\\fn{font}\\fs{size}\\c{fill}\\bord{bord}\\3c{bcol}\\shad{shad}{extra}}}{text}"


def _em_width(line):
    """글자 폭 추정(한글=1em, 영문·숫자≈0.68, 공백·부호≈0.35)."""
    return sum(1.0 if ord(ch) > 0x2E80 else 0.68 if ch.isalnum() else 0.35 for ch in line)


def _fit_lines(t, wrap, maxc, size, max_w, pad=0):
    """'|' 는 강제 줄바꿈. wrap=False 면 한 줄. 가장 긴 줄이 max_w 를 넘으면 글자 크기를 줄인다."""
    if "|" in t:
        lines = [x.strip() for x in t.split("|")]
    elif wrap:
        lines = [l for pg in _wrap(t, maxc, 9) for l in pg]
    else:
        lines = [t]
    longest = max(_em_width(l) for l in lines)
    return "\\N".join(lines), min(size, int((max_w - 2 * pad) / (longest * 0.95)))


def _caption_events(u, i, a, b, rules, ev, sfx, shown, typo_log, pop):
    """1편 실측 스타일(rules.caption_style). 훅=큰 흰 글씨 여러 줄(줄 간격 넓게) / 자막=흰색 / 분할 자막=검은 영역 안 /
    작은 문구 / 제목=흰 스티커 / 라벨=검정 둥근 상자 / 키워드=세로 쌓기. 한 블록의 문구는 순서대로, '+' 는 앞 문구와 동시에."""
    cs, W, H = rules["caption_style"], rules["canvas"]["w"], rules["canvas"]["h"]
    cx = W // 2; dur = b - a; split = "plain_y" in u
    caps = [dict(c, text=fix_typos(c["text"], rules, typo_log)) for c in u["captions"]]
    chips = [c for c in caps if c["kind"] == "chip"]
    stack = len(chips) >= cs["stack"]["min_items"]
    cta = [c for c in caps if c["kind"] == "cta"]
    over = [c for c in caps if c["kind"] in ("line", "mark", "gtext")]
    seq = [c for c in caps if c["kind"] not in ("cta", "line", "mark", "gtext") and not (stack and c["kind"] == "chip")]
    wts = [max(1.0, len(c["text"]) / 6) if c["kind"] != "big" else max(1.0, len(c["text"]) / 6, cs["min_big_sec"] / dur * len(seq)) for c in seq]
    keep = [(c, w) for c, w in zip(seq, wts) if not c.get("with_prev")]
    tw = sum(w for _, w in keep) or 1.0; acc = a; slot = {}; last = (a, b)
    for c, w in zip(seq, wts):
        if c.get("with_prev"):
            slot[id(c)] = last; continue
        slot[id(c)] = last = (acc, acc + dur * w / tw); acc += dur * w / tw
    ev_ = lambda s0, e0, t, layer=0: ev.append(f"Dialogue: {layer},{_ts(s0)},{_ts(e0)},Cap,,0,0,0,,{t}")
    for c in seq:
        s0, e0 = slot[id(c)]; t = c["text"]; k = c["kind"]
        if k == "big" and split and i != 0:
            k = "plain"  # 1편 분할 컷처럼 분할 영상 위에는 큰 글씨를 얹지 않고 아래 검은 영역에 자막으로
        if k == "plain":
            q = cs["plain"]; size0 = q["split_size"] if split else q["size"]; y = (q["split_y"] if split else q["y"])
            if cs.get("design"):  # 정돈된 디자인: 첫 줄 굵고 크게 / 둘째 줄 작고 연하게, 줄마다 반투명 검정 둥근 배경
                dz = cs["design"]; lines = [x.strip() for x in t.split("|")] if "|" in t else [t]
                cont = [l.startswith("~") for l in lines]; lines = [l.lstrip("~") for l in lines]  # '~' = 앞 줄에 이어지는 같은 크기 줄
                sizes = [dz["main"] if (n == 0 or cont[n]) else dz["sub"] for n in range(len(lines))]
                if any(cont):
                    mn = min(sz for sz, c, n in zip(sizes, cont, range(len(lines))) if n == 0 or c); sizes = [mn if (n == 0 or cont[n]) else sz for n, sz in enumerate(sizes)]
                sizes = [min(sz, int((900 - 2 * dz["pad"]) / (_em_width(l) * 0.95))) for sz, l in zip(sizes, lines)]
                hs = [int(sz * 1.18) + 2 * dz["pad"] // 2 for sz in sizes]; total = sum(hs) + dz["gap"] * (len(lines) - 1)
                top = H * y - total / 2
                for n, (l, sz) in enumerate(zip(lines, sizes)):
                    yc = int(top + sum(hs[:n]) + dz["gap"] * n + hs[n] / 2)
                    main = (n == 0 or cont[n]); f = dz["main_font"] if main else dz["sub_font"]; col = "&HFFFFFF&" if main else dz["sub_color"]
                    ev_(s0, e0, _cap(f, sz, cx, yc, l, fill=col, bord=dz["pad"], bcol="&H000000&", shad=dz.get("shadow", 0), extra=f"\\b{1 if (main and dz.get('bold', True)) else 0}\\3a{dz['alpha']}\\4a&H60&\\fax{dz.get('shear', 0)}"))
            else:
                body, size = _fit_lines(t, True, q["max_chars"], size0, 960)
                ev_(s0, e0, _cap(cs["font_plain"], size, cx, int(H * y), body, shad=q["shadow"], extra="\\4c&H000000&"))
        elif k == "small":
            q = cs["small"]; y = q["split_y"] if split else q["y"]
            body, size = _fit_lines(t, True, 24, q["size"], 960)
            ev_(s0, e0, _cap(cs["font_plain"], size, cx, int(H * y), body, shad=2, extra="\\4c&H000000&"), 3)
        elif k == "big" and i == 0:  # 훅: 줄마다 따로 놓아 1편처럼 줄 간격을 넓게
            q = cs["hook"]; lines = [x.strip() for x in t.split("|")] if "|" in t else ([t] if len(t) <= 10 else [l for pg in _wrap(t, q["max_chars"], 9) for l in pg])
            size = min(q["size"], int(960 / (max(_em_width(l) for l in lines) * 0.95)))
            for n, l in enumerate(lines):
                yy = int(H * u.get("big_y", q["y"]) + (n - (len(lines) - 1) / 2) * q["pitch"] * H)
                ev_(s0, e0, _cap(cs["font_bold"], size, cx, yy, l, shad=3, extra="\\b1\\4c&H000000&"), 2)
        elif k == "big":  # 제목: 흰 스티커(안쪽 얇은 검정 선 + 바깥 두꺼운 흰 선)
            q = cs["title"]; lines = [x.strip() for x in t.split("|")] if "|" in t else [t]
            size = min(q["size"], int((q["max_width_px"] - 2 * q["outer"]) / (max(_em_width(l) for l in lines) * 0.95)))
            for n, l in enumerate(lines):
                yy = int(H * u.get("big_y", q["y"]) + (n - (len(lines) - 1) / 2) * size)
                ev_(s0, e0, _cap(cs["font_title"], size, cx, yy, l, bord=q["outer"], bcol="&HFFFFFF&", fill="&HFFFFFF&", extra="\\b1"), 1)
                ev_(s0, e0, _cap(cs["font_title"], size, cx, yy, l, bord=q["inner"], bcol="&H000000&", fill="&HFFFFFF&", extra="\\b1"), 2)
        else:  # 라벨: 검정 둥근 상자
            q = cs["chip"]; body, size = _fit_lines(t, True, 16, q["size"], 880, q["pad"])
            y = (cs["plain"]["split_y"] + 0.07) if split else q["y"]
            ev_(s0, e0, _cap(cs["font_bold"], size, cx, int(H * y), body, bord=q["pad"], bcol="&H000000&", extra="\\b1\\3a&H20&"), 2)
        shown.append((s0, e0, t.replace("|", " ")))
    for c in over:
        t = c["text"]
        if c["kind"] == "line":  # 허리단 위치 가로선: "x시작,x끝,y[,나타나는 시점(블록 비율)]"
            v = [float(x) for x in t.split(",")]; x0, x1, yy = v[:3]; st = v[3] if len(v) > 3 else 0.5
            w_px, th = int((x1 - x0) * W), 9
            draw = f"{{\\an7\\pos({int(x0 * W)},{int(yy * H)})\\p1\\c&HFFFFFF&\\bord3\\3c&H000000&\\shad0}}m 0 0 l {w_px} 0 {w_px} {th} 0 {th}{{\\p0}}"
            ev_(a + dur * st, b, draw, 3)
        elif c["kind"] == "gtext":  # 짧게 표시하는 글자: "글자,x비율,y비율,시작비율,길이초"
            g = t.split(","); gx, gy, gst, gd = float(g[1]), float(g[2]), float(g[3]), float(g[4])
            ev_(a + dur * gst, a + dur * gst + gd, _cap(cs["font_bold"], 84, int(W * gx), int(H * gy), g[0], bord=4, bcol="&H000000&", extra="\\b1\\fad(120,160)"), 3)
        else:  # 선택 표시
            mx = int(W * (0.25 if "왼" in t else 0.75))
            ev_(a, b, _cap(cs["font_bold"], 44, mx, int(H * 0.19), "▼ 선택", shad=2, extra="\\b1\\4c&H000000&"), 3)
            shown.append((a, b, "선택"))
    for c in cta:
        q = cs["plain"]; sec = cs.get("cta_sec", 1.5)
        ev_(max(a, b - sec), b, _cap(cs["font_plain"], q["size"], cx, int(H * q["y"]), c["text"], shad=q["shadow"], extra="\\fad(250,0)\\4c&H000000&"), 3)
        shown.append((max(a, b - sec), b, c["text"]))
    if stack:
        q = cs["stack"]; step = dur / (len(chips) + 1)
        sx = {"left": int(W * 0.23), "right": int(W * 0.77)}.get(u.get("side"), cx)
        for k, c in enumerate(chips):
            s0 = a + step * (k + 1) - step * 0.5 + q["first_after"]; y = q["y"][min(k, len(q["y"]) - 1)]
            ev_(s0, b, _cap(cs["font_plain"], q["size"], sx, int(H * y), c["text"], shad=2, extra="\\b1\\4c&H000000&"), 2)
            shown.append((s0, b, c["text"]))


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
Style: Cap,NanumGothic,46,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Cta,{st['font']},{ou['size']},&H00FFFFFF,&H000000FF,{st['outline_color']},&H80000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    ev, sfx, typo_log, shown = [], [], [], []
    pop = f"{{\\fscx{em['start_scale']}\\fscy{em['start_scale']}\\t(0,{em['pop_ms']},\\fscx{em['pop_scale']}\\fscy{em['pop_scale']})\\t({em['pop_ms']},{em['pop_ms']*2},\\fscx100\\fscy100)}}"
    for i, u in enumerate(units):
        a, b = ustart[i], uend[i]; dur = b - a
        if "captions" in u:
            _caption_events(u, i, a, b, rules, ev, sfx, shown, typo_log, pop)
            if i > 0 and rules["sfx"]["unit_whoosh"]:
                sfx.append((a, "whoosh"))
            continue
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
    if ou["enabled"] and total > ou["duration"] + 1 and not units[-1].get("captions"):
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
                  + (f"loudnorm=I={au['loudnorm_i']}:TP={au['loudnorm_tp']}:LRA={au['loudnorm_lra']}," if au.get("loudnorm", True) else "") +
                  f"atrim=0:{total:.3f},afade=t=out:st={max(0,total-fade):.3f}:d={fade}[aout]")
    fc = ";".join(chains)
    # ass 는 작업 폴더 기준 상대경로로 (경로 이스케이프 문제 회피)
    vf = f"[0:v]ass=subs.ass:fontsdir={FONTS_DIR}[vout]"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"] + inputs +
        ["-filter_complex", fc + ";" + vf, "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(rules["canvas"]["fps"]),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)], cwd=str(work))
