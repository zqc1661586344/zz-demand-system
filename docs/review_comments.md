# 代码评审报告

### 问题 1（重要・安全 / 架构・遗留）：Streamlit 前端硬编码 admin/admin123 自动登录，无登录页

- **位置**：`app/streamlit_app/auth.py` → `auto_login()`；`app/streamlit_app/app.py` → `main()`；`app/main.py` → `_seed_demo_user()`
- **分析**：前端每次加载都用写死的 `admin/admin123` 直接换令牌进系统，无任何登录入口。开发库迁移到生产后任何人访问 UI 即得超管权限；反之 demo 用户不存在时前端整体不可用。API 层 `get_current_user`/`require_roles` 的多租户设计在 UI 上没有入口，RBAC 形同虚设。
- **建议**：新增登录 / 注册页，删除硬编码凭据；`_seed_demo_user` 加开关默认关；生产库清理遗留 demo 用户。