#!/usr/bin/env bash
# Run the backend (FastAPI) and frontend (Vite) together. Ctrl+C stops both.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
pids=()
cleanup() {
  echo
  echo "두 서버를 종료합니다..."
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "종료 완료."
}
trap cleanup INT TERM EXIT
if [ ! -x "$BACKEND/.venv/bin/uvicorn" ]; then
  echo "오류: 백엔드 가상환경이 없습니다. backend 폴더에서 venv 생성 후 pip install -e '.[dev]' 를 먼저 실행하세요." >&2
  exit 1
fi
if [ ! -f "$BACKEND/.env" ]; then
  echo "경고: backend/.env 가 없습니다. .env.example 을 복사해 값을 채우세요." >&2
fi
echo "백엔드 시작: http://localhost:8000  (문서: /docs)"
(
  cd "$BACKEND"
  exec .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 2>&1 | sed "s/^/[backend] /"
) &
pids+=($!)
if [ ! -d "$FRONTEND/node_modules" ]; then
  echo "프론트엔드 의존성 설치 중 (최초 1회)..."
  ( cd "$FRONTEND" && npm install )
fi
echo "프론트엔드 시작: http://localhost:5173"
(
  cd "$FRONTEND"
  exec npm run dev 2>&1 | sed "s/^/[frontend] /"
) &
pids+=($!)
echo
echo "두 서버 실행 중. 브라우저에서 http://localhost:5173 로 접속하세요. 중지는 Ctrl+C."
echo
wait
