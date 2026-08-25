#!/bin/bash
# 生产模式启动脚本 — gunicorn + uvicorn workers
#
# 使用方式：
#   export DATABASE_URL=postgresql://user:pass@localhost:5432/ragdb
#   bash scripts/run_production.sh
#
# 环境变量：
#   WEB_CONCURRENCY  — worker 数量（默认 4）
#   PORT             — 监听端口（默认 8001）
#   RAG_BM25_CACHE_BYPASS — 多 worker 下绕过 BM25 内存缓存，设为 true（默认）

set -euo pipefail

export RAG_BM25_CACHE_BYPASS="${RAG_BM25_CACHE_BYPASS:-true}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-4}"
PORT="${PORT:-8001}"

exec gunicorn app.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers "${WEB_CONCURRENCY}" \
  --bind "0.0.0.0:${PORT}" \
  --timeout 120 \
  --max-requests 10000 \
  --max-requests-jitter 1000 \
  --access-logfile - \
  --error-logfile - \
  --access-logformat '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'