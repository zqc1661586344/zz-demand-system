#!/usr/bin/env bash
# 一键启动脚本：后端 (8001) + Chainlit 前端 (8002)
# 使用：bash start.sh

set -e

cd "$(dirname "$0")"

echo "=== 启动后端 (端口 8001) ==="
uvicorn app.main:app --port 8001 &
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

echo "=== 启动 Chainlit 前端 (端口 8002) ==="
# 清空 DATABASE_URL 避免 Chainlit 误用 SQLite 连接串去连 asyncpg
DATABASE_URL= chainlit run app/chainlit_app/app.py --port 8002 &
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