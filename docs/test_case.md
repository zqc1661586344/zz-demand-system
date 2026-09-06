### 问题：测试仍只有 2 组纯逻辑用例，本轮 3 个致命缺陷全部在"一条冒烟测试就能拦住"的范围内

- **位置**：`teststest_core_logic.py`（109 行，`TestReflectQualityScoring` + `TestCitationVerifier`）｜`tests/compliance/` **缺 `__init__.py`**（而 `tests/`、`tests/test_api/`、`tests/test_rag/`、`tests/test_services/` 都有）｜`test_e2e.py` 中 "compliance" 命中 **0 次**
- **仍缺失**（按拦截价值排序）：
  1. **HITL 端到端**（拦问题 1、5）：高风险合同 → `pending_human` 时 `risks` 已落库 → `human-review` 改等级 → `resume` → `completed` → 报告中的等级**等于人工修改后的值**；
  2. **入口冒烟**（拦上轮 P1 类回归）：自己的 indexed 文档 → `POST /reviews` 返回 **200**；他人 private → **403**；他人 shared → **200**；不存在 → **404**；
  3. **checkpointer 类型断言**（拦问题 2）：配置 PG DSN 时 `harness.checkpointer_type == "postgres"`；
  4. **Celery 任务注册断言**（拦问题 3）：`"app.compliance.tasks.run_compliance_review" in celery_app.tasks`；
  5. **三路报告一致性**（拦问题 4、6）：同一 `report_data` 下 HTML/Word/PDF 三级计数完全相等；`total>0` 时 Word 文本禁止出现"合同合规"；报告中禁止出现"占位"字样；三个下载链接均 200；
  6. **引用校验真实场景**（拦问题 12）：引用为原文**子串**时必须 `verified=True`；
  7. **落库关联**（拦问题 7）：每条 risk 的 `clause_id`/`playbook_rule_id` 非空率 100%；
  8. **二次审查隔离**（拦问题 10）：同文档连续两次审查，两次的条款与风险互不覆盖。
- **修复**：把上述 8 条作为本轮 P0 修复的**验收门**（先写、让它红，再改、让它绿）；补 `tests__init__.py`；CI 加 `pytest --cov=app/compliance` 并设阈值。