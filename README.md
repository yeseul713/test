# 전용 영상 편집기
Claude 에게 **"구글 드라이브에 영상 올렸어, 편집해줘"** 라고만 하면 `video-edit` 스킬이 순서대로 끝까지 진행합니다.

- 규칙집: `rules/rulebook.md` (적용값 `rules/rules.json`)
- 엔진: `ve.py` (`edit` / `analyze` / `feedback` / `qa` / `drive`), 설치: `bash setup.sh`
- 대본 형식: `examples/sample_script.txt` (한 줄=한 컷, `*강조*`, `[자료: 파일]`)
- 스킬: `.claude/skills/video-edit/SKILL.md`
