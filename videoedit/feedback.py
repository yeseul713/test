"""사용자 피드백 → 규칙집(rules.json + rulebook.md)에 영구 반영."""
import re, datetime
from .common import load_rules, save_rules, ROOT

# (정규식, 설명, 적용 함수)
def _mul(path, f):
    def fn(r):
        d = r
        for k in path[:-1]: d = d[k]
        old = d[path[-1]]; d[path[-1]] = type(old)(round(old * f, 3)) if not isinstance(old, int) else int(round(old * f))
        return f"{'.'.join(path)}: {old} → {d[path[-1]]}"
    return fn

def _add(path, v):
    def fn(r):
        d = r
        for k in path[:-1]: d = d[k]
        old = d[path[-1]]; d[path[-1]] = round(old + v, 3)
        return f"{'.'.join(path)}: {old} → {d[path[-1]]}"
    return fn

MAP = [
    (r"자막.*(너무\s*)?(작|안\s*보)", _mul(["subtitle", "size"], 1.15)),
    (r"자막.*(너무\s*)?(크|커|큼)", _mul(["subtitle", "size"], 0.88)),
    (r"강조.*(작|약|안\s*눈에)", _mul(["emphasis", "size"], 1.15)),
    (r"강조.*(크|커|과해)", _mul(["emphasis", "size"], 0.88)),
    (r"자막.*(위|올려)", _add(["subtitle", "margin_v"], 120)),
    (r"자막.*(아래|내려)", _add(["subtitle", "margin_v"], -120)),
    (r"(컷|화면전환|전환).*(느려|느리|지루|길)", _mul(["cut", "max_shot"], 0.8)),
    (r"(컷|화면전환|전환).*(빨라|빠르|정신|짧)", _mul(["cut", "max_shot"], 1.25)),
    (r"효과음.*(크|커|시끄)", _add(["sfx", "volume_db"], -4)),
    (r"효과음.*(작|안\s*들)", _add(["sfx", "volume_db"], 4)),
    (r"(배경음|bgm|BGM|배경\s*음악).*(크|커|시끄|묻)", _add(["bgm", "volume_db"], -4)),
    (r"(배경음|bgm|BGM|배경\s*음악).*(작|안\s*들)", _add(["bgm", "volume_db"], 4)),
    (r"(줌|확대).*(과해|심|많|어지)", _mul(["hook", "zoom"], 0.97)),
]


def apply(text):
    rules = load_rules(); changes = []
    for rx, fn in MAP:
        if re.search(rx, text):
            changes.append(fn(rules))
            if "zoom" in rx:  # 줌 완화는 일반 컷 배율에도
                z = rules["cut"]["zoom_levels"]; z[1] = round(1 + (z[1] - 1) * 0.8, 3); changes.append(f"cut.zoom_levels[1] → {z[1]}")
    stamp = datetime.date.today().isoformat()
    rules["user_feedback_rules"].append({"date": stamp, "feedback": text, "applied": changes})
    save_rules(rules)
    book = ROOT / "rules" / "rulebook.md"
    with open(book, "a", encoding="utf-8") as f:
        f.write(f"\n- [{stamp}] 피드백: \"{text}\" → " + ("; ".join(changes) if changes else "**자동 매핑 없음 — Claude가 규칙집에 직접 반영 필요**") + "\n")
    return changes
