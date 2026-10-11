#!/usr/bin/env python3
"""영상 편집기 CLI.
  ve.py edit --footage 폴더 --script 대본.txt --out 결과.mp4
  ve.py analyze URL또는파일 ... [--apply]
  ve.py feedback "자막이 너무 작아요"
  ve.py qa 영상.mp4
  ve.py drive 링크또는폴더 --dest 폴더
"""
import argparse, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])

def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("edit"); e.add_argument("--footage", required=True); e.add_argument("--script", required=True)
    e.add_argument("--out", default="output/result.mp4"); e.add_argument("--broll-dir")
    a = sub.add_parser("analyze"); a.add_argument("sources", nargs="+"); a.add_argument("--apply", action="store_true"); a.add_argument("--workdir", default="reference")
    ph = sub.add_parser("photos"); ph.add_argument("--images", required=True); ph.add_argument("--script", required=True)
    ph.add_argument("--map", required=True); ph.add_argument("--out", default="output/draft.mp4")
    cl = sub.add_parser("clips"); cl.add_argument("--clips", required=True); cl.add_argument("--script", required=True)
    cl.add_argument("--map", required=True); cl.add_argument("--out", default="output/draft.mp4")
    f = sub.add_parser("feedback"); f.add_argument("text", nargs="+")
    q = sub.add_parser("qa"); q.add_argument("video")
    d = sub.add_parser("drive"); d.add_argument("source"); d.add_argument("--dest", default="inbox")
    n = ap.parse_args()
    if n.cmd == "edit":
        from videoedit.pipeline import edit
        print("보고서:", edit(n.footage, n.script, n.out, n.broll_dir))
    elif n.cmd == "clips":
        from videoedit.clips import build
        o, iss, tot, typo = build(n.clips, n.script, n.map, n.out)
        print(f"{o} ({tot:.1f}초)"); print("\n".join(f"[{i['type']}] {i['msg']}" for i in iss) or "검수: 문제 없음")
    elif n.cmd == "photos":
        from videoedit.photos import build
        o, iss, tot, typo = build(n.images, n.script, n.map, n.out)
        print(f"{o} ({tot:.1f}초)"); print("\n".join(f"[{i['type']}] {i['msg']}" for i in iss) or "검수: 문제 없음")
    elif n.cmd == "analyze":
        from videoedit.analyze_reference import analyze_all
        analyze_all(n.sources, n.workdir, n.apply)
    elif n.cmd == "feedback":
        from videoedit.feedback import apply
        ch = apply(" ".join(n.text)); print("반영:", ch or "자동 매핑 없음(기록만 됨)")
    elif n.cmd == "qa":
        from videoedit import qa; from videoedit.common import load_rules
        r = load_rules(); iss = qa.check_format(n.video, r) + qa.check_freeze(n.video, r) + qa.scan_privacy(n.video, r)
        print("\n".join(i["msg"] for i in iss) or "문제 없음")
    elif n.cmd == "drive":
        from videoedit.drive import fetch
        fetch(n.source, n.dest)
main()
