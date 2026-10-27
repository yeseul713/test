#!/usr/bin/env bash
# 편집기 필요 도구 설치 (Ubuntu/Debian)
set -e
(apt-get update -q && apt-get install -y -q ffmpeg fonts-nanum) || sudo sh -c "apt-get update -q && apt-get install -y -q ffmpeg fonts-nanum"
pip install -q -r "$(dirname "$0")/requirements.txt"
echo "설치 완료"
mkdir -p /usr/local/share/fonts/gowun && cp "$(dirname "$0")"/assets/fonts/*.ttf /usr/local/share/fonts/gowun/ && fc-cache -f
