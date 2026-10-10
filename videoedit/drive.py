"""구글 드라이브에서 영상 내려받기. 우선순위: rclone(gdrive 리모트) → gdown(공유 링크) → 로컬 폴더."""
import shutil, subprocess, pathlib, sys

SETUP = """구글 드라이브가 연결돼 있지 않습니다. 아래 중 하나로 연결해 주세요.

[방법 A] Claude 커넥터 (가장 쉬움)
  claude.ai → 설정 → 커넥터 → 'Google Drive' 연결 → 이 세션을 새로 시작
  (또는 Claude Code 에서 /mcp 로 Google Drive 연결)

[방법 B] 공유 링크 (연결 없이)
  드라이브에서 영상이 든 폴더 우클릭 → 공유 → '링크가 있는 모든 사용자 - 뷰어' → 링크 복사
  → `python3 ve.py drive "<링크>"`  (gdown 사용, 폴더/파일 모두 가능)

[방법 C] rclone
  `rclone config` 로 이름이 gdrive 인 구글 드라이브 리모트를 만든 뒤
  → `python3 ve.py drive gdrive:영상폴더`

※ 클라우드 세션은 네트워크 허용 목록에 drive.google.com, *.googleusercontent.com, *.googleapis.com 이 있어야 합니다
  (환경 설정 → 네트워크 접근 → 허용 도메인 추가)."""


def fetch(source, dest):
    dest = pathlib.Path(dest); dest.mkdir(parents=True, exist_ok=True)
    if pathlib.Path(source).exists():
        for p in pathlib.Path(source).iterdir():
            shutil.copy2(p, dest / p.name)
        print("로컬 폴더 복사 완료"); return
    if ":" in source and not source.startswith("http") and shutil.which("rclone"):
        subprocess.run(["rclone", "copy", source, str(dest), "-P"], check=True); return
    if source.startswith("http"):
        try:
            import gdown
        except ImportError:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "gdown"], check=True); import gdown
        if "/folders/" in source: gdown.download_folder(source, output=str(dest), quiet=False)
        else: gdown.download(source, str(dest) + "/", fuzzy=True)
        return
    print(SETUP); raise SystemExit(2)
