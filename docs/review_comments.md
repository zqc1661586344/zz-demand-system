# 代码评审报告



### 问题 3（重要・安全 / 架构）：Streamlit 前端硬编码 admin/admin123 自动登录，无登录页，RBAC 形同虚设

- **位置**：`app/streamlit_app/auth.py` → `auto_login()`；`app/streamlit_app/app.py` → `main()`；`app/main.py` → `_seed_demo_user()`
- **分析**：前端每次加载都 POST `/api/auth/login {admin/admin123}`，直接把 admin 令牌写进 session_state，**没有任何用户登录入口**（chat/documents 页面全部以 admin 身份操作）。`_seed_demo_user` 虽在 production 下不建 demo 用户，但：① 若生产库是从开发库迁移 / 复制而来，demo 用户仍存在，任何人都能以超管身份进系统；② production 下 demo 用户不存在时，`auto_login` 失败、**前端整体不可用**—— 多用户 / 多角色这套能力在 UI 上根本用不起来。
- **影响**：对 "中小型企业真实应用" 是硬伤：要么全 admin 裸奔，要么前端废掉。API 层的 `require_roles`/`get_current_user` 多租户设计因此实际未生效。
- **建议**：前端增加登录 / 注册页，把 `admin/admin123` 从代码中移除；`_seed_demo_user` 增加环境变量开关并默认关闭；生产库做一次遗留 demo 用户清理。

### 问题 7（中等・并发安全）：PGVector 单例实例跨线程共享，线程安全存疑

- **位置**：`app/rag/vector_store.py` → `get_vector_store()`（`@lru_cache` 返回单例，`connection=settings.vector_store_url`）
- **分析**：langchain-postgres 的 `PGVector` 内部按连接串创建连接，`lru_cache` 使该实例被 FastAPI 线程池所有请求共享；多用户并发查询 / 上传写入（尤其 `add_documents_to_store` 与查询并行）可能命中连接状态竞争。虽然查询有 10/min 限流，但上传管道与多 worker 下仍可能并发。
- **建议**：改用 `PGVector(connection=<sqlalchemy engine>)` 走连接池，或去 `lru_cache` 改为按请求 / 按锁串行化；至少补一个并发读写冒烟测试。


### 问题 9（中等・正确性）：非流式 query 路径未把 `free_chat` 标记落库

- **位置**：`app/api/conversations.py` → `query_conversation()`（`add_message(db, conv_id, role="assistant", content=answer, sources=sources)` 未传 `free_chat`）
- **分析**：流式路径 `_save_messages_background` 正确传了 `free_chat`；非流式路径默认 `False`。前端刷新靠 `Message.free_chat` 决定是否补 "找不到答案" 前缀，非流式 free-chat 回答刷新后会被当成正常 RAG 回答展示。上轮已指出，未修复。
- **建议**：`query_conversation()` 的 `add_message` 补传 `free_chat=free_chat`。

### 问题 10（中等・架构）：多租户粒度仅 private/shared 两级，且 UI 看不到共享文档

- **位置**：`app/rag/vector_store.py` → `_user_where()`；`app/api/documents.py` → `list_documents()`（普通用户只过滤 `Document.uploaded_by == current_user.id`）
- **分析**：① 检索侧隔离只有 "自己私有 + 全员共享" 两档，没有部门 / 团队级共享，SME 多团队场景下要么全共享、要么各自私有，权限不可控；② 普通用户的文档列表**看不到共享文档**，但检索又可能命中共享文档并作为 sources 返回，用户无法核验来源归属，体验与一致性都差；③ superuser 全量检索无任何审计记录。
- **建议**：至少把共享文档纳入 `list_documents` 并标注 `shared`；中期引入 `visibility ∈ {private, team, shared}` 或文档 - 用户授权表；对 superuser 全量检索 / 查询做操作审计日志。

