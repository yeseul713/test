---
name: video-edit
description: 사용자가 "구글 드라이브에 영상 올렸어, 편집해줘"라고 하거나 영상 편집/숏폼 편집/자막 넣어줘/편집 피드백(자막 작아, 컷 느려 등)을 말할 때 사용. 대본에 맞춰 컷 편집 → 자막·강조자막·효과음·배경음 → mp4 저장 → 자체 검수 → 보고까지 끝까지 수행한다. 사용자는 편집을 모르므로 되묻지 않는다.
---

# 전용 영상 편집기 (video-edit)

프로젝트 루트(`/home/user/test`, 없으면 이 저장소)의 `ve.py` 가 엔진이다. 규칙은 `rules/rulebook.md`(문서) + `rules/rules.json`(적용값).
**사용자는 편집을 전혀 모른다. 되묻지 말고 기본값으로 끝까지 진행**하고, 결과만 쉬운 말로 보고한다.

## 처음 한 번 (도구 없을 때)
`bash setup.sh` (ffmpeg, 나눔폰트, python 패키지 설치).

## "구글 드라이브에 영상 올렸어, 편집해줘" 흐름
1. **드라이브 연결 확인**: ListConnectors 로 Google Drive 커넥터 확인.
   - 연결됨 → 커넥터로 최근 올린 영상 폴더/파일과 대본 파일(.txt/.docx/구글문서)을 찾아 `inbox/<날짜>/` 로 내려받는다.
   - 안 됨 → `videoedit/drive.py` 의 SETUP 안내(커넥터 연결 / 공유링크 / rclone)를 사용자에게 그대로 안내하고, 공유 링크가 오면 `python3 ve.py drive "<링크>" --dest inbox/<날짜>` 로 진행. 네트워크가 막혔으면 `read_documentation(environment.network)` 대로 허용 도메인(drive.google.com, *.googleusercontent.com, *.googleapis.com)을 안내.
2. **대본 확인**: 사용자가 첨부한 대본(.txt). 형식: 한 줄 = 한 컷, 강조할 단어는 `*단어*`, 자료화면은 줄 앞에 `[자료: 파일명]`, `#` 시작 줄은 메모. 강조 표시가 없으면 핵심 명사/숫자 1~2개를 직접 골라 `*…*` 로 표시한 사본을 만든다(원본은 보존).
3. **편집 실행**: `python3 ve.py edit --footage inbox/<날짜>/footage --script <대본.txt> --out output/<이름>.mp4`
   순서는 고정: ① 컷 편집 → ② 자막·강조자막·효과음·배경음 → ③ mp4 저장 → ④ 자체 검수(오타·반복·정지·개인정보) 후 자동 수정.
4. **결과 확인**: `output/<이름>.report.md` 를 읽고, 대표 프레임 몇 장(`ffmpeg -ss … -frames:v 1`)을 직접 보고 자막/개인정보 상태를 눈으로 한 번 더 확인. 자동 수정이 안 된 항목은 직접 고쳐 재실행.
5. **보고**: 길이/컷 수, 검수에서 발견·수정한 것, 남은 것(있으면), 파일 경로. 그리고 "아쉬운 점 말해주면 규칙집에 넣어 다음부터 자동 반영" 한 줄.
6. 결과 mp4 를 드라이브 커넥터로 업로드 가능하면 업로드, 아니면 경로 안내.

## 피드백 반영 ("자막이 작아요", "컷이 느려요", "효과음 시끄러워요" …)
`python3 ve.py feedback "<사용자 말 그대로>"` → rules.json 수치 조정 + rulebook.md 하단에 기록.
"자동 매핑 없음" 이 나오면 사용자 말을 해석해 `rules/rules.json` 을 직접 수정하고 rulebook.md 해당 절도 고친 뒤 커밋. 그 다음 같은 영상을 재편집할지 사용자가 원하면 재실행.

## 레퍼런스 재분석
`python3 ve.py analyze <유튜브링크…> --apply` (네트워크에서 youtube.com 허용 필요). 결과는 `reference/analysis.md`, 장면 컨택트시트 `reference/ref*/scenes.png` 를 직접 보고 rulebook.md 수치도 맞춰 갱신.

## 주의
- rules.json 의 `meta.status` 가 provisional 이면 레퍼런스 실측 전이라는 뜻 — 보고서에 한 줄 언급.
- 음성인식(faster-whisper)은 모델 다운로드가 되는 환경에서만 대본-음성 매칭을 쓰고, 안 되면 무음구간+글자수 비율로 배정한다(보고서에 방식 표시). 이 경우 촬영본이 대본 순서대로 녹화돼 있어야 정확하다.
- 변경 후엔 커밋/푸시(규칙집이 곧 학습 기록).
