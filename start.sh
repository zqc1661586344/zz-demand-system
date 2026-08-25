#!/usr/bin/env bash
# 一键启动脚本：后端 (8001) + Streamlit 前端 (8002)
# 使用：bash start.sh
#
# 开发模式默认启用限流（30 次/分钟）。高频调试时可临时关闭：
#   RATE_LIMIT_ENABLED=false bash start.sh
#
# 需要 Celery + Redis 异步文档处理时，先启动 Redis 和 worker：
#   redis-server
#   celery -A app.celery_app worker --loglevel=info
# 再运行本脚本（CELERY_BROKER_URL 默认空，回退 BackgroundTasks）：

set -e

cd "$(dirname "$0")"

echo "=== 启动后端 (端口 8001) ==="
# 限流默认启用，不想受限可 export RATE_LIMIT_ENABLED=false
RATE_LIMIT_ENABLED="${RATE_LIMIT_ENABLED:-true}" uvicorn app.main:app --port 8001 &
BACKEND_PID=$!

# 等待后端就绪
echo "等待后端就绪..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8001/api/health > /dev/null 2>&1; then
        echo "后端已就绪"
        break
    fi
    sleep 1
done

echo "=== 启动 Streamlit 前端 (端口 8002) ==="
streamlit run app/streamlit_app/app.py --server.port 8002 --server.headless true &
FRONTEND_PID=$!

echo ""
echo "=========================================="
echo "  后端: http://localhost:8001"
echo "  前端: http://localhost:8002"
echo "  API 文档: http://localhost:8001/docs"
echo "=========================================="
echo ""
echo "按 Ctrl+C 停止所有服务"

trap 'echo "停止服务..."; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit' INT TERM

wait