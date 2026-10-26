"""전체 편집 파이프라인: 컷 → 자막/소리 → mp4 → 검수 → 자동수정 → 보고서."""
import pathlib, shutil, json, datetime
from .common import load_rules, duration
from .script_plan import parse_script, plan_cuts
from . import render, qa


def edit(footage, script, out, broll_base=None, max_fix_rounds=2):
    rules = load_rules()
    footage, script, out = pathlib.Path(footage), pathlib.Path(script), pathlib.Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = out.parent / (out.stem + "_work"); work.mkdir(exist_ok=True)
    base_dir = pathlib.Path(broll_base) if broll_base else script.parent
    units = parse_script(script)

    print("① 컷 편집: 대본에 맞는 구간 찾는 중…")
    mode = plan_cuts(units, footage, rules)
    print(f"   매칭 방식: {mode}, 대본 {len(units)}줄")

    extra, log, round_i, issues, final = [], [], 0, [], out
    while True:
        pieces = render.make_pieces(units, rules, extra)
        base = render.render_pieces(pieces, rules, work, base_dir)
        print(f"   컷 {len(pieces)}개 렌더 완료")
        print("② 자막·강조자막·효과음·배경음 입히는 중…")
        ass, sfx, total, typo_log, shown, _ = render.build_ass(units, pieces, rules, work)
        print("③ mp4 저장 중…")
        render.finalize(base.resolve(), ass, sfx, total, rules, out, work)

        print(f"④ 검수 {round_i + 1}회차…")
        ignore = [u["text"] for u in units]
        issues = qa.run_all(str(out), units, shown, pieces, rules, ignore_texts=[])
        log.append({"round": round_i + 1, "issues": issues, "typo_autofix": typo_log})
        fixable = [i for i in issues if i["type"] in ("freeze", "static")]
        pii = [i for i in issues if i["type"] == "pii"]
        if pii:
            tmp = out.with_suffix(".blur.mp4")
            qa.blur_boxes(str(out), pii, rules, str(tmp)); tmp.replace(out)
            print(f"   개인정보 {len(pii)}건 흐림 처리")
        if fixable and round_i < max_fix_rounds:
            newt = [i["t"] + i.get("dur", 3) / 2 for i in fixable]
            if all(any(abs(n - e) < 0.3 for e in extra) for n in newt):
                break
            extra += newt; round_i += 1
            print(f"   정지/무변화 {len(fixable)}곳 → 컷 분할+줌 변경 후 재렌더")
            continue
        break

    # 최종 재검사 (개인정보 흐림 확인)
    remaining = [i for i in qa.run_all(str(out), units, shown, pieces, rules) if i["type"] != "pii"]
    if rules["privacy"]["enabled"]:
        remaining += qa.scan_privacy(str(out), rules)
    from .export import export_like_ref
    export_like_ref(out)
    report = write_report(out, units, mode, pieces, total, log, remaining, rules)
    return report


def write_report(out, units, mode, pieces, total, log, remaining, rules):
    L = [f"# 편집 결과 보고서 — {out.name}", "",
         f"- 생성: {datetime.datetime.now():%Y-%m-%d %H:%M}",
         f"- 길이: {total:.1f}초 / 컷 {len(pieces)}개 / 평균 컷 {total/len(pieces):.2f}초",
         f"- 대본 매칭 방식: {mode}",
         f"- 규칙집 상태: **{rules['meta']['status']}** ({rules['meta']['note'][:60]}…)" if rules["meta"]["status"] == "provisional" else "- 규칙집 상태: 레퍼런스 실측 반영",
         "", "## 검수 이력"]
    for r in log:
        L.append(f"### {r['round']}회차")
        if r["typo_autofix"]:
            L.append("- 자막 맞춤법 자동 수정: " + ", ".join(f"{a}→{b}" for a, b in r["typo_autofix"]))
        if not r["issues"]:
            L.append("- 문제 없음")
        for i in r["issues"]:
            L.append(f"- [{i['type']}] {i['msg']}")
    L += ["", "## 최종 상태", "- 남은 문제: " + ("없음" if not remaining else "")]
    for i in remaining:
        L.append(f"  - [{i['type']}] {i['msg']}")
    p = out.with_suffix(".report.md"); p.write_text("\n".join(L), encoding="utf-8")
    return p
