# 合规审查模块（app/compliance）代码评审 · 第四轮

## 一、问题清单（按严重性排序）

### 问题 1（致命・正确性/回归）：`human_review → END` 后风险不落库、报告不生成，而前端没有 `/resume` 入口 → 默认配置下**任何含高风险的审查永久卡死**

- **位置**：`appreview_graph.py:65-66`（`g.add_edge("human_review", END)`）｜`appruntime.py:636`（`_persist_results` **全库唯一调用点**，在 `generate_report` 内）｜`runtime.py:579-594`（`human_review` 节点只 `_persist_status(STATUS_PENDING_HUMAN)`）｜`appconfig.py:153`（`compliance_hitl_enabled: bool = True`）
- **事实链**（逐环已核验）：
  1. `should_retry`（`runtime.py:678-681`）在 `compliance_hitl_enabled=True` 且存在 high 风险时返回 `"human"`；
  2. `human_review` 节点执行后走 `add_edge("human_review", END)` → **图正常结束，`generate_report` 永不执行**；
  3. 而 `_persist_results`（写 clauses / key_info / risks / references / reports 五张表）**只在 `generate_report:636` 被调用** → 风险项、条款、报告文件**全部不产生**；
  4. 但 `review_clauses:424` 已经 `_persist_status(..., **counts)` 把三级计数写进了 review 行 → **`high_risk_count=1` 而 `risks=[]`** 的自相矛盾展示（第二轮 P1 的现象原样复活）；
  5. 前端 `_render_review_result:234` 是 `if status != "completed": return` → 风险明细、报告下载区**全部不渲染**；`:226` 是 `if auto_refresh and status not in ("completed","failed"): time.sleep(2); st.rerun()` → `pending_human` 既不是 completed 也不是 failed，**Streamlit 进入每 2 秒一次的无限 rerun 循环**；
  6. 全库检索 `resume`：**`app/streamlit_app/` 下 0 命中** → 前端没有任何按钮调用 `POST /reviews/{id}/resume`；README 也未记录该端点。用户唯一的出路是直接 curl。
- **影响**：合规审查最有价值的输出恰恰是高风险结论。当前默认配置下，**只要 Playbook 命中一条 high（例如"试用期上限违规"），审查就永久停在 `pending_human`**：法务看不到风险明细、下载不到任何报告、前端页面卡死轮询、DB 里只有一个孤零零的计数。这是比上轮"入口 404"更隐蔽的可用性事故——因为它只在**检出风险时**发生，而"检不出风险"的合同反而能正常走完（于是测试时很容易误判为"功能正常"）。
- **修复**（三处必须同时改，缺一即无效）：
  1. **落库与报告解耦**：把 `_persist_results` 从 `generate_report` 中抽出为独立节点 `persist_results`，接在 `reflect`/`compare` 之后、`should_retry` 之前（或至少在 `human_review` 节点内先落 risks/clauses，报告留到 resume）。原则：**任何进入终态（END）的路径都必须已经落库**；
  2. **前端补 resume**：`viewscompliance.py` 增加 `pending_human` 分支——渲染已落库的风险清单 + "逐条确认/修改"表单 + "✅ 人工审核完成，生成报告"按钮（调 `POST /reviews/{id}/resume`），并把 `:220` 的文案 `"等待人工审核（MVP 不阻塞）"` 改为 `"等待人工审核（已暂停，需确认后生成报告）"`；`auto_refresh` 的终止条件加入 `pending_human`；
  3. **兜底**：`should_retry` 增加超时/自动放行策略（见问题 12 的业界对照），或提供 `COMPLIANCE_HITL_MODE=block|non_block` 让不阻塞模式仍走 `human_review → generate_report`。
- **必补测试**（一条就能拦住）：`LLM_PROVIDER=test` + 一条必然命中 high 的规则 → 断言终态为 `pending_human` 时 `GET /reviews/{id}` 的 `risks` **非空**，随后 `POST /resume` → 断言 `completed` 且三个报告下载均 200。

### 问题 2（致命・正确性/Harness）：`PostgresSaver.from_conn_string()` 是上下文管理器，被当对象使用 → `setup()` 必抛 `AttributeError` → **PostgresSaver 从未生效，永远静默回退 InMemorySaver**

- **位置**：`appcheckpointer.py:67-68`
  - `saver = PostgresSaver.from_conn_string(conn_string)` → 返回 `_GeneratorContextManager`，**不是 PostgresSaver 实例**
  - `saver.setup()` → `AttributeError` → 被 `:73 except Exception` 捕获 → `:77` 回退 `InMemorySaver()`
- **权威依据**：`langgraph-checkpoint-postgres`（本仓库锁定 **3.1.2**）官方用法是 `with PostgresSaver.from_conn_string(DB_URI) as checkpointer: checkpointer.setup()`；LangGraph 文档明确"0.2.0 起 `from_conn_string` 返回上下文管理器，必须用 `with` 包裹"。手动传连接时还**必须** `PostgresSaver(conn)` 且连接带 `autocommit=True` + `row_factory=dict_row`，否则 `setup()` 不持久化、读取时 `TypeError: tuple indices must be integers`。
- **连带缺陷**：`_detect_checkpointer_type`（`runtime.py:127-136`）靠 `type(cp).__name__` 里是否含 `"Postgres"` 判断——对 `_GeneratorContextManager` 会返回 `"unknown"`；但因为 `setup()` 先抛异常，实际永远落到 `"memory"` 分支，只打一条 WARN 日志，**不进 DB、不进 `/api/health`、不阻断启动**，运维无从发现。
- **影响**：本轮 newly-built 的 HITL resume **完全依赖 checkpointer**（`resume_review:707` 用 `self.checkpointer.get_tuple(config)`）。InMemorySaver 意味着：① 进程重启 → state 全丢 → resume 返回 "state not found"（且不落库，见问题 5）；② **Celery prefork 多 worker 进程 → 每个子进程各有独立内存 → resume 任务落到别的子进程必然找不到 state，成功率约 1/N**；③ uvicorn `--workers>1` 同理。也就是说：**问题 1 修好之后，resume 依然会在生产环境随机失败**。
- **修复**：
  1. 用连接池而非上下文管理器（推荐，进程级常驻）：`from psycopg_pool import ConnectionPool; pool = ConnectionPool(conninfo=normalize_pg_dsn(url), min_size=5, max_size=20, kwargs={"autocommit": True, "row_factory": dict_row})` → `saver = PostgresSaver(pool)` → `saver.setup()`；在 FastAPI `lifespan` / Celery `worker_process_init` 里建立，进程退出时关闭；
  2. `build_checkpointer()` 失败时**不要静默降级**：生产（`APP_ENV=production` 或 `use_celery_task=True`）应 `raise`，或至少把 `checkpointer_type` 写进 `compliance_reviews` 行与 `/api/health`，并在 `resume_review` 前置校验 `checkpointer_type == "postgres"`，否则直接 422 告知"当前部署不支持人工审核恢复"；
  3. 安全加固：设置 `LANGGRAPH_STRICT_MSGPACK=true` 或显式 `allowed_msgpack_modules`，限制 checkpoint 反序列化的类型（官方安全公告要求，DB 被攻破时可导致代码执行）。

### 问题 3（致命・部署）：Celery worker 无法发现 `apptasks.py` → `USE_CELERY_TASK=true` 时审查任务成为 unregistered task，永久停在 `pending`

- **位置**：`appcelery_app.py:6-18`（`Celery(...)` + `conf.update(...)`，**无 `include=` / `imports=` / `autodiscover_tasks()`**）｜`start.sh:10`（`celery -A app.celery_app worker --loglevel=info`）｜`appreviews.py:62-64`（`from app.compliance.tasks import run_compliance_review; run_compliance_review.delay(payload)`）
- **事实**：`-A app.celery_app` 只导入 `appcelery_app.py`，而该模块不 import 任何 tasks 模块。`reviews.py` 的 import 是**函数内延迟 import，发生在 API 进程**，worker 进程永远不会执行到。于是 worker 的任务注册表里没有 `app.compliance.tasks.run_compliance_review` / `resume_compliance_review` → 消息到达后 worker 报 `Received unregistered task of type ...` 并拒绝/丢弃 → review 行永久 `status="pending"`，前端无限轮询。**同样的缺陷也存在于既有的 `apptasks.py::process_document_task`**（`documents.py:129` 同为延迟 import），说明这条路径从未被真实验证过。
- **第二个开关缺陷**：`reviews.py:61` 只判 `if settings.use_celery_task`，而 `appdocuments.py:128` 判的是 `if settings.celery_broker_url and settings.use_celery_task`。`config.py:113` 的 `celery_broker_url` 默认 `""` → `Celery(broker="")` 会退回 kombu 默认 `amqp://guest@localhost:5672` → `.delay()` 抛 `OperationalError`，而 `create_review` **没有 try/except** → HTTP 500，且 review 行已 commit（`review_service.py:112`）→ **孤儿 pending review**。
- **影响**：`.env.example:67-68` 明写"即使 redis 配置存在，也关闭 celery 任务…**生产环境改为 true**"。也就是说，按项目自己的部署指引操作，第一次上生产就会同时踩中"任务未注册"和"broker 判空缺失"两个坑，而现象都是"审查永远排队中"，极难定位。
- **修复**：
  1. `appcelery_app.py` 显式声明任务模块：`celery_app.conf.update(include=["app.rag.tasks", "app.compliance.tasks"])`（或 `celery_app.autodiscover_tasks(["app.rag", "app.compliance"])`）；并在 `start.sh` 补一行 worker 启动命令与 `--concurrency`/`-Q` 说明；
  2. `reviews.py:61` 与 `resume:172` 的条件改为 `if settings.celery_broker_url and settings.use_celery_task`，与 `documents.py` 对齐；
  3. `.delay()` 包 try/except：投递失败时**回滚或标记 review 为 `failed`（带 error_message）**，再降级 `background_tasks`，绝不留孤儿 `pending`；
  4. 补一条部署自检：启动时若 `use_celery_task=True`，用 `celery_app.control.inspect().registered()` 或直接 `ping()` 校验 broker 与任务注册，失败即 fail-fast。

### 问题 4（致命・合规风险）：RAG 补充的法规引用无条件 `verified=True` + 检索无分数阈值 + 占位法条现已可检索 → 报告会输出"【占位示例条文，真实全文待补】"并盖章"已逐字校验"

- **位置**：`runtime.py:472-486 _rag_hits_to_references()`（硬编码 `"verified": True`）｜`runtime.py:456-465`（`hits[:3]` 直接注入，无 score 过滤）｜`knowledgeretrieval.py:37-72 search()`（**无任何相似度阈值**，`vector_store.py:150` 返回 `1.0 - dist` 但从不用于过滤）｜`knowledge/seed_data/labor_contract/劳动合同法.json`
- **事实**（已核验种子文件）：`title = "中华人民共和国劳动合同法（占位）"`，仅 2 条 articles，正文以 `【占位示例条文，真实全文待补】` 结尾。上轮它因 `/seed` 不写向量而**不可达**；本轮 P11 修好后，`POST /api/compliance/knowledge/seed`（README 推荐的第一步）会把它**真实向量化**，于是 `_enrich_references_with_rag` 在"风险无引用"分支（`runtime.py:454-465`）会把它检索出来、截 top-3、标 `verified=True`、写进 `compliance_risk_references`、渲染进 HTML/Word 报告的"法规依据/法规附录"。
- **第二重缺陷（相关性）**：检索 query 是 `risk.get("description")`（`runtime.py:441`），而 `_hit_to_risk` 生成的 description 形如「条款 第三条：试用期上限违规（legality）。规则说明：<企业立场文本>」——这是**合成文本**，不是条款原文，语义检索质量差；再加上**无阈值**，top-3 里混入不相关法条是必然的，而它们一律被标记为"已校验"。整个法规库当前只有 60 条（劳动合同法 2 + 劳动法 23 + 实施条例 12 + 司法解释一 23），覆盖率极低，误检概率更高。
- **影响**：`verified` 字段的语义是"通过原文逐字校验"（`modelsreview.py:88` 注释），前端与报告据此决定是否显示"⚠ 需人工核实"。把**未经任何校验的检索结果**标成 `verified=True`，等于**主动伪造可信度**——比"不引用"危险得多。对法务用户，报告里出现占位文本或不相关法条并标注"已校验"，是可直接导致错误法律判断的缺陷。
- **修复**：
  1. `_rag_hits_to_references` 改为 `"verified": False, "needs_human_check": True, "retrieval_score": h["score"], "ref_type": "regulation_retrieved"`，或引入第三态 `provisional`（检索得到但未逐字校验）；报告分区渲染"已校验依据 / 候选参考（需核实）"；
  2. `retrieval.search()` 增加 `min_score`（建议 0.5~0.6，可配置），低于阈值一律丢弃；`_enrich_references_with_rag` 的 query 改为 `条款原文 + 风险描述` 拼接（条款内容需从 `clauses` 按 `clause_number` 反查，见问题 7）；
  3. 种子数据加 `is_placeholder: bool`，`ingest_regulation` 拒绝占位数据入生产库，或摄入时置 `status="draft"` 并在 `search_regulations` 默认过滤 `status="active"`（`vector_store.py:135` 已支持 `status` 过滤，但从未被传）；
  4. 报告页脚固定声明"法规库版本 / 条文总数 / 检索时间"，让读者知道依据面的完整度。

### 问题 5（致命・正确性）：HITL 人工决策**完全不进最终报告**——`resume_review` 从 checkpoint 取的是 AI 原始 risks

- **位置**：`runtime.py:692-731 resume_review()`（`state = tuple_result.values` → `self.generate_report(state)`）｜`runtime.py:596-611 generate_report()`（`report_ctx["risks"] = state.get("risks")`）｜`servicesreview_service.py:320-380 human_action()`（只写 DB：`risk.risk_level` / `risk.suggestion` / `risk.human_decision`）
- **事实**：人工审核通过 `human_action` 修改的是 **`compliance_risks` 表行**；而 `resume_review` 生成报告用的是 **LangGraph checkpoint 里的 state**，两者没有任何同步。`generate_report` 全程不读 DB（`report_skill.py:23-41` 也不读）。
- **影响**：法务把某条 high 改成 low（`modify_level`）、把 AI 的修改建议改成企业标准措辞（`edit_suggestion`）、把误报标记为 false（`mark_false`）之后点击"生成报告"，**下载到的报告里风险等级、建议、误报标记全部是 AI 的原始结论**，人工决策只在详情页可见、在交付给业务方/审计方的正式报告里彻底消失。这在合规产品里是不可接受的——报告是唯一具有留痕效力的产物。同时 `mark_false` 的项仍会出现在报告风险清单中并计入三级统计。
- **附带缺陷**：`human_action` 修改 `risk_level` 后**不回写 `review.high/medium/low_risk_count`** → 详情页汇总卡片与风险明细自相矛盾；`resume` 后报告用的是 state 里的旧计数，又是第三套数字。
- **修复**：
  1. `resume_review` 在调 `generate_report` 前，**用 DB 里的人工决策覆盖 state 中的 risks**（按 `sort_order` 或新增的稳定业务键对齐），过滤 `human_decision == "rejected"` 的项，并同步重算三级计数写回 review 行；
  2. 更彻底的做法：把 `human_action` 的结果写进 LangGraph state（`Command(resume={"decisions": [...]})` + `interrupt()`，见问题 12），让 `human_review` 节点的返回值就是"合并人工决策后的 risks"，报告与 DB 天然一致；
  3. 报告中为每条被人工修改的风险加标注（`【法务已确认】`/`【等级已调整：high → low，操作人 X，时间 Y】`），这才是 HITL 的审计价值所在。

### 问题 6（重要・产品风险）：`compute_reflect_quality` 新公式让"零规则/零命中"直接得 0.95 分 → 首轮即 `completed` 并输出"合同合规"，且新单测把该语义固化为期望值

- **位置**：`runtime.py:65-95 compute_reflect_quality()`｜`runtime.py:487-519 reflect()`｜`servicesreview_service.py:146-160 _load_active_rules()`｜`word_exporter.py:78`｜`teststest_core_logic.py:33-37`
- **实证复现**（用仓库真实公式，12 条款 / 0 风险）：

  | retry_count | quality | coverage_score | avg_confidence | 阈值 0.7 判定 |
  | --- | --- | --- | --- | --- |
  | 0 | **0.95** | 1.00 | 0.90 | 通过 → 直接 completed |
  | 1 | 0.80 | 1.00 | 0.90 | 通过 |
  | 2 | 0.65 | 1.00 | 0.90 | 才会 retry |

- **事实**：旧公式对"有条款但零风险"给 `coverage=0.4 / conf=0.5 → quality=0.45`（会触发重试，虽然重试是空转）；新公式改为 `coverage=1.0 / conf=0.9`。`coverage_score` 现在**只度量"是否解析出了条款"**，与"审查覆盖了多少条款/多少规则维度"毫无关系——指标名与语义彻底脱钩。而 `_load_active_rules` 本轮（正确地）删掉了"跨合同类型兜底"，改为 warning + LLM-only；但在 `LLM_PROVIDER=test` 或 LLM 不可用时，`rules=[]` → `risks=[]` → **0.95 分 → completed → 报告输出"本次审查未检出风险条款，合同合规。"**
- **加重情节**：`teststest_core_logic.py:33-37 test_clean_contract_high_quality` 断言 `q >= 0.85`，注释写"clean contract quality should be ~0.95"。**这条测试把"零规则也算高质量"的危险语义钉死成了期望行为**，将来任何人想修正都会被测试拦住。
- **影响**：系统仍然把"我没有规则 / 我没查 / LLM 挂了"表达成"这份合同合规"，而且现在**表达得更自信**（quality 0.95、零重试、直接 completed）。这是合规产品最不可接受的失败模式，已连续三轮未解决，本轮反而恶化。
- **修复**：
  1. `coverage_score` 改为真实覆盖度：`已审查条款数 / 总条款数`，并叠加"规则维度覆盖率"= `命中的规则维度数 / Playbook 声明的维度数`、"法规覆盖率"= `有法规依据的风险数 / 风险总数`；
  2. 引入三态结论与显式 `review_completeness`：`存在风险` / `未发现风险（覆盖率 X%、规则 N 条、法规库 M 部）` / **`审查不充分，无法出具结论`**；当 `rules == 0` 或法规库为空或 `coverage < 阈值` 时，**禁止输出"合规"字样**，状态置 `completed_with_warnings`（需在 `state.py` 增常量并同步前端）；
  3. `_load_active_rules` 返回空时，除 warning 外还应把 `degraded_reasons=["no_playbook_rules"]` 写入 state 与 review 行，并在报告首页醒目展示；
  4. 修正 `test_clean_contract_high_quality`：改为断言"零规则场景必须触发 `completed_with_warnings` 且报告不含'合规'"，而不是断言 0.95。

### 问题 7（重要・正确性）：`clause_id` 与 `playbook_rule_id` 仍恒为 NULL —— "风险可追溯到条款与规则"连续第三轮未达成

- **位置**：`runtime.py:262`（`clause_idx = r.get("clause_index")`，全库 `clause_index` **仅此 1 处命中，零写入方**）｜`runtime.py:245-259`（三级回退 `playbook_rule_id` → `rule_id` → `rule_name` 反查，**三个键 `_hit_to_risk` 一个都不产出**）｜`agentsengine.py:115`（hit dict **确实带 `"rule_id"`**，但转换时被丢弃）｜`schemasreview.py:79-87 RiskItem`（无 `clause_index`/`clause_id`/`playbook_rule_id` 字段）
- **影响**：`review_service.py:259` 的 `clause = clause_map.get(r.clause_id) if r.clause_id else None` 恒为 None → `GET /reviews/{id}` 每条 risk 的 `clause_number` 与 `clause_content` **仍返回 null**；`playbook_rule_id` 恒 NULL → 无法回答"这条高风险依据哪条规则判定"。表写进去了、计数对了、HITL 能查到 risk 了，但**风险 ↔ 条款 ↔ 规则三方关联依然是断的**，法务无法从风险定位条款原文，审计举证能力仍缺失。`ComplianceClause.page_number` 同理恒 NULL（源头 `parse_skill.py:40` 的自赋值空操作）。
- **另注**：`runtime.py:251-259` 用 `rule_name` 反查 `CompliancePlaybook.name` 的兜底逻辑本身是错的——`CompliancePlaybook` 一行是**规则集**（`name` 如"劳动合同默认规则"），hit 里的 `name` 是**单条规则名**（如"试用期上限违规"），层级不同，永远查不中；即便查中，把规则集 id 写进 `playbook_rule_id` 也是错的粒度。
- **修复**：
  1. `RiskItem` 增 `clause_id: Optional[str]` 与 `playbook_rule_id: Optional[str]`；`_hit_to_risk` 透传 `"playbook_rule_id": hit.get("rule_id")`、`"playbook_rule_name": hit.get("name")`；
  2. 条款 ID **在审查前生成**：`extract_clauses` 阶段就为每个 clause 分配 `clause_id` 并写入 state，`review_clauses` 把 `clause_id` 注入 `RiskSkill` 的 ctx，由 reviewer 逐条带回（这是唯一可靠的对齐方式，`clause_number` 可能重复或缺失）；
  3. 删除 `runtime.py:251-259` 的错误反查兜底；
  4. 删掉 `parse_skill.py:40` 的自赋值，页码在**切分之前**用 `load_document` 的 per-page `Document.metadata["page"]` 绑定；
  5. 补断言测试：落库后每条 risk 的 `clause_id` 与 `playbook_rule_id` 非空率 = 100%。

### 问题 8（重要・可靠性）：Celery 重试语义与图内重试混淆、同 `thread_id` 重跑幂等性未定义、无超时、无 stuck 回收

- **位置**：`tasks.py:26-33`（`max_retries=settings.compliance_max_retry`）｜`config.py:143`（`compliance_max_retry: int = 2`，`.env.example:111` 注释为"**自反思**最多重试次数"）｜`tasks.py:46-57`（`except Exception → self.retry(exc=exc)`）｜`runtime.py:762`（`self.graph.stream(initial, config)`，`config` 的 `thread_id` 固定为 `review-<id>`）｜`main.py:65`（只有 `_recover_stuck_documents`）
- **问题点**：
  1. **配置项语义冲突**：`compliance_max_retry` 同时充当"图内 reflect 重试预算"和"Celery 任务重试次数"。最坏情况 = 1 次图运行（内含最多 3 轮 review）× 3 次 Celery 尝试 = **9 轮逐条 LLM 审查**，成本与时延完全失控，且运维调一个参数会同时改变两个语义；
  2. **`except Exception` 无差别重试**：`autoretry_for=RETRY_EXCEPTIONS` 已经覆盖了网络/OpenAI 瞬时错误，但 `:51` 又对**任何**异常调 `self.retry()` → `ValueError`（payload 缺字段）、`IntegrityError`（问题 10 的 FK 冲突）、`FileNotFoundError`（文档被删）这类**永久失败**也会重试 2 次，纯浪费且掩盖真因；
  3. **同 `thread_id` 重跑幂等性未定义**：重试时 `start_review` 用同一个 `thread_id` 再次 `graph.stream(initial, config)`。若上次运行已写过 checkpoint，LangGraph 会把 `initial` 作为 state 更新**合并到既有 checkpoint 并从中断处继续**，而不是干净重跑——上一次失败留下的 `risks`/`clauses`/`status` 会污染本次运行。代码里没有任何 `thread_id` 重置或删除 checkpoint 的逻辑，也没有测试覆盖；
  4. **无 `time_limit` / `soft_time_limit`**：一次审查在 LLM 模式下是数十次串行调用 + 本轮新增的**逐风险 embedding 调用**（问题 15），单个任务可无限占用 worker；
  5. **无 stuck 回收**：`main.py` lifespan 只恢复 documents。若任务在 `reviewing` 阶段因 worker OOM 被杀，`acks_late` 会重投（好），但若 broker 不可用或走 BackgroundTasks 分支，review 永久停在该阶段，无人重置；
  6. `resume_compliance_review` 的 `max_retries=2` 与 `run_compliance_review` 的 `max_retries=compliance_max_retry` 不一致，无说明。
- **修复**：① 拆成两个配置：`compliance_reflect_max_retry`（图内）与 `compliance_task_max_retries`（队列）；② 删掉 `except Exception → self.retry()`，只保留 `autoretry_for=RETRY_EXCEPTIONS`，其余异常直接置 review 为 `failed` 并写 `error_message`；③ 重试前用**新 thread_id**（如 `review-<id>-attempt-<n>`）或显式清理旧 checkpoint，并在 `_persist_results` 前保证幂等（当前对 reports 已幂等 ✅，对 clauses 见问题 10）；④ 加 `time_limit=1800, soft_time_limit=1500`；⑤ 补 `_recover_stuck_reviews()`：把 `status in (parsing,planning,reviewing,reflecting,comparing,generating)` 且 `started_at` 超阈值的任务重置为 `pending` 并重新入队；⑥ 加 `POST /reviews/{id}/cancel`。

### 问题 9（重要・正确性）：`ReviewState` 仍未声明节点写入的键 → checkpoint 里没有这些通道，`resume_review` 拿不到；`template_deviations` 写库被静默丢弃

- **位置**：`workflowsstate.py:48-95`（本轮零改动）vs `runtime.py:512-516`（返回 `coverage_score`/`avg_confidence`）、`:571-577`（返回 `template_deviations` + 三级计数）、`:684`（读 `avg_confidence`）｜`runtime.py:567-571`（`_persist_status(..., template_deviations=deviations)`）
- **事实**：LangGraph 按 `StateGraph(ReviewState)` 的注解建通道，**未声明的键不进 channel、不写 checkpoint、不传下游**（上一轮已实测：条件边在同一 super-step 内能看到，下游节点与最终 state 看不到）。本轮新增的 `resume_review` **完全依赖 `checkpointer.get_tuple(config).values`**，于是这个老问题的危害被放大：
  - `coverage_score` / `avg_confidence` / `template_deviations` / 三级计数**都不在 checkpoint 里** → resume 后即使想按问题 6 的建议在报告中渲染覆盖率，也拿不到数据；
  - `_persist_status(..., template_deviations=deviations)` 因 `ComplianceReview` **没有该列**，被 `:151 if hasattr(row, k)` 静默忽略 → 模板偏离数既不入库也不入 state，**彻底蒸发**；
  - `state.py` 声明的 `template_diff` / `review_summary` / `report_id` / `human_decisions` 仍从未被任何节点写入（`human_decisions` 正是问题 5 需要的载体，字段早就备好了）。
- **修复**：以节点实际读写字段为准重建 `ReviewState`——补 `coverage_score`、`avg_confidence`、`template_deviations`、`high/medium/low_risk_count`、`regulation_hits`、`reflection`、`degraded_reasons`；把 `human_decisions` 真正用起来（承载问题 5 的人工决策）；删除或真正写入 `template_diff`/`review_summary`/`report_id`；需要跨轮累加的列表用 `Annotated[list, operator.add]`（`state.py:16-18` 的注释已意识到这点，四轮未落地）；`ComplianceReview` 补 `template_deviations` / `coverage_score` / `quality_score` 列（配 alembic 迁移）。

### 问题 10（重要・数据一致性）：条款仍按 `compliance_doc_id` 全删重建 → 同文档二次审查摧毁历史审查数据；`clause_id` FK 无 `ondelete` + SQLite 未开外键 → 问题 7 修好后 PG 下必炸

- **位置**：`runtime.py:186-207`（收集该 `compliance_doc_id` 下所有既有 clause id，新 id 是 fresh uuid4 所以 `discard` 是空操作，`:205-207` 全部删除）｜`servicesreview_service.py:88-99`（`comp_doc` 幂等复用 → **同一文档的所有审查共享一个 `compliance_doc_id`**）｜`modelsclause.py:21`（无 `review_id`）｜`modelsreview.py:60`（`clause_id = ForeignKey("compliance_clauses.id")`，**无 `ondelete`**）｜`review_service.py:225-230`（`get_review` 按 `compliance_doc_id` 取条款）
- **影响**：① 同一文档跑第二次审查后，第一次审查的 `GET /reviews/{id}` 会显示**第二次**的条款，历史审查的条款原文彻底消失（合规审计要求历史可复现）；② `runtime.py:167` 的 docstring 声称"CASCADE ondelete 会连带清除 risks/references"——**这是错的**：没有任何 FK 以 CASCADE 指向 `compliance_clauses`，而 `ComplianceRisk.clause_id` 指向它且**无 ondelete**（PG 默认 NO ACTION）→ 一旦问题 7 修好、`clause_id` 真的被写入，删除旧条款时旧审查的 risks 仍引用它们 → `IntegrityError` → `_persist_results` 整体回滚 → review 置 failed。**目前只因 `clause_id` 恒 NULL 才侥幸没爆，这是一个被另一个 bug 掩盖的定时炸弹**；③ `appdatabase.py` 全文无 `PRAGMA foreign_keys`，SQLite 下不强制外键 → 开发环境静默产生孤儿行，与 PG 行为不一致，问题永远测不出来。
- **修复**：① `ComplianceClause` 增 `review_id`（或建 `compliance_review_clauses` 关联表），`_persist_results` 只清理 `review_id == 当前` 的旧数据，**绝不跨审查删除**；② `ComplianceRisk.clause_id` 补 `ondelete="CASCADE"`（或 `SET NULL`）；③ `appdatabase.py` 加 `PRAGMA foreign_keys=ON` 事件监听，让开发态与 PG 一致；④ `get_review` 的条款查询改按 `review_id`；⑤ 修正 `_persist_results` 的 docstring。

### 问题 11（重要・架构）：RAG 是"生成后补引用"而非"检索后生成"，`regulation_hits` 仍未传给 reviewer → LLM 从未看到过法规原文

- **位置**：`runtime.py:409-425 review_clauses()`（先 `_risk_skill.execute(...)` 生成 risks，**再** `_enrich_references_with_rag(risks)`）｜`runtime.py:416`（`_risk_skill.execute({"clauses":..., "rules":...})`，**不传 `regulation_hits`**）｜`skillsrisk_skill.py:27`（`regulation_hits = ctx.get("regulation_hits") or {}` → 恒空）｜`agentsreviewer.py:113-117`（`build_clause_review_prompt(..., regulation_hits=regulation_hits)` → 恒 None）｜`agentsresearcher.py`（`ResearcherAgent` 仍仅被 re-export，0 调用）
- **影响**：① LLM 在**完全看不到任何法条原文**的情况下生成风险与"法规依据"，事后系统再去检索几条塞进去（并标 `verified=True`，见问题 4）——这与 RAG 的基本范式相反，幻觉率无法通过事后校验降低；② `reviewer_prompt` 里预留的"法规引用候选"占位从未被填充，prompt 工程白做；③ `_hit_to_risk`（`reviewer.py:57-62`）仍把 Playbook 的 `legal_basis_ref`（字符串线索，如"劳动合同法第23条"）当 `ref_name`、把 `standard_position`（**企业自己的立场文本**）当 `ref_content` 塞进法条原文位置，`ref_type="playbook"` → 这些内容现在会被持久化进 `compliance_risk_references` 并渲染进报告"法规附录"，**企业内规被当作法条原文**；④ 好消息是这些 playbook 引用经 `verify_references` 会被正确判为 `verified=False`（因为 `standard_position` 不可能匹配法条原文）——但结合问题 14 的算法缺陷，**所有引用都会显示"需人工核实"**，包括真正检索到的法条，告警完全失去区分度。
- **修复**：① 调整为 **retrieve → review → verify** 三段：在 `review_clauses` 之前（或新增 `research` 节点）按条款批量检索法规，把 `{clause_number: hits}` 作为 `regulation_hits` 传进 `RiskSkill` → `ReviewerAgent`，让 LLM 在 prompt 里看到真实法条并被要求"只能引用给定条文"；② 生成后再用 `verify_references` 做**逐字校验**（此时校验才有意义）；③ 把 Playbook 线索与法规引用彻底拆开：`standard_position` → `suggestion_reason`，`legal_basis_ref` → `rule_hint`（不入 `legal_references`），**无检索命中就不产出法规引用**；④ 正式接入或删除 `ResearcherAgent`。

### 问题 12（重要・算法）：`citation_verifier` 仍用 `SequenceMatcher.ratio()` + 阈值 0.95 → 实证 100% 误杀真实引用，而它本轮刚被接进主链路

- **位置**：`knowledgecitation_verifier.py:40-42 text_similarity()`、`:45-88 verify_citation()`｜`config.py:151`（`compliance_citation_similarity_threshold = 0.95`）
- **实证复现**（真实场景：法条原文 192 字，引用其中逐字一致的 27 字）：
  - `SequenceMatcher.ratio()` = **0.2466** ≪ 0.95 → `verified = False`
  - 而归一化子串包含判定 `quote in article` = **True** ← 正确结论
  - `autojunk=False` 时 ratio 仍为 0.2466（本例长度未触发 autojunk，但 >200 字的中文法条会额外失真）
  - `ratio = 2M/(len(a)+len(b))`：只要引用是原文的一部分，ratio 上限就是 `2·len(ref)/(len(ref)+len(article))`，**引用越精确越短，分数越低**——算法与目标完全反向
- **影响**：上轮它不可达，所以缺陷无实际后果；**本轮 `_enrich_references_with_rag` 把它接进了主链路**，于是：所有 playbook 引用 + 所有 LLM 产出的摘录式引用一律 `verified=False` + `needs_human_check=True` → HTML 报告恒显示"⚠ 需人工核实"、Word 恒显示"⚠️ 待核实" → 告警疲劳 → 人工直接忽略该标记 → 防幻觉机制形同虚设。若有人为"让校验通过"把阈值调到 0.3，又会放行真正的幻觉引用。`:70-73` 还对 <10 字的引用直接判 False（"第十条"这类精确短引用永远无法通过）。
- **修复**：改为**包含性 + 局部对齐**判定：① 归一化后 `ref_norm in article_norm` → 直接 `verified=True`；② 否则用 `SequenceMatcher.get_matching_blocks()` 或滑动窗口计算 **coverage = 匹配字符数 / len(ref_norm)**，阈值对 coverage 生效（而非 ratio）；③ 显式 `autojunk=False`；④ 长文本可用 `rapidfuzz.partial_ratio`；⑤ 结果分级 `verified / partial(60~95%) / unverified`，报告分别渲染；⑥ 去掉"<10 字直接 False"，改为"短引用走精确包含判定"。
- **必补测试**：逐字引用（长/短各一）必须 `verified=True`；篡改一字必须 `False`；引用为原文子串必须 `True`。现有 `test_verify_citation_match` 用的是**引用 == 原文全文**的特例，恰好绕过了这个缺陷，等于没测到真实场景。

### 问题 13（重要・契约）：`template_id` / `contract_type_override` 仍未进入初始 state；`original_filename` 仍从客户端读一个不存在的字段

- **位置**：`servicesreview_service.py:134-143 start_payload`（仍只有 9 个键，**无 `template_id`、无 `contract_type_override`**）｜`runtime.py:734-760 start_review()` 签名与 `initial` 同步缺失｜`state.py:62-63`（两个键已声明）｜`runtime.py:664-671 should_compare()`（读 `state.get("template_id")` → 恒 None）｜`runtime.py:387-390 supervise()`（`contract_type_override=state.get("contract_type_override")` → 恒 None）｜`reviews.py:48`（`getattr(req, "original_filename", None)`，`ReviewCreateRequest` 无此字段）
- **影响**：① API 已接受并存库的 `template_id`（`reviews.py:56`、`ComplianceReview.template_id`）**永远传不到图里** → `should_compare` 只能靠"规则是否配了 `standard_position`"这种间接信号决定是否走 compare 分支，用户显式指定的模板比对被忽略；② Supervisor 的"合同类型复核"永远拿不到用户意图（`doc_type` 虽在 `create_review` 阶段用于选规则 ✅，但图内无法复核）；③ `original_filename` 恒 None → 回退 `doc-{document_id}` → 报告标题继续是仓库里已提交的那种 `doc-b44ee52a-6b0c-4bd7-963c-a9100cc2268d — 合规审查报告`；④ 前端 `_start_review` 仍不发 `template_id`，UI 层也无从触发模板比对。
- **修复**：① `start_payload` 与 `start_review` 签名同步补 `template_id`、`contract_type_override`；② `original_filename` **不要信任客户端**，`create_review` 里已拿到 `biz_doc`，直接 `biz_doc.original_filename`，删掉 `reviews.py:48` 的 `getattr`；③ 给 `app/compliance/schemas/*` 所有请求模型加 `model_config = ConfigDict(extra="forbid")`——这是成本最低、收益最大的一条，能让所有此类契约错误在开发期就 422 暴露（本轮 `doc_type` → `contract_type_override` 的错位正是靠人工发现的）。

### 问题 14（重要・权限/健壮性）：`/resume` 与 `/human-review` 无 RBAC、不校验"是否真的发生过人工决策"；越权返回 400 而非 403；`list_reviews` 分页口径仍不一致

- **位置**：`reviews.py:151-181 resume_review()`（仅 `_assert_review_access`，任何角色可用；**不检查 `compliance_human_actions` 是否有记录**）｜`reviews.py:126-148 human_review()`（同样无角色要求）｜`review_service.py:76-77`（越权 `raise ValueError("forbidden: ...")` → `reviews.py:58-59` 转 **400**）｜`reviews.py:83-88`（`total` 走 superuser 分支，`items` 恒按 `current_user.id` 过滤）
- **对比**：`apiplaybooks.py` 与 `apiknowledge.py` 的写端点已正确使用 `Depends(require_roles("admin"))`，`appdependencies.py` 也提供了该工厂——**审查链路是唯一没做角色控制的**。
- **影响**：① `viewer` 角色可以篡改他人审查的风险等级（`human_review`）、触发报告生成（`resume`，消耗 LLM/DB 资源）、删除审查记录（`delete_review`）；② HITL 的"门"只是状态判断：`pending_human` 后**立即** `POST /resume` 即可生成报告，**无需任何人工决策记录** → 人在环变成可一键跳过的装饰；③ 越权返回 400（应为 403）+ "document not found" 也返回 400（应为 404）→ 违反 HTTP 语义，且 400 会让枚举 `document_id` 的攻击者拿到"该文档存在且已索引"的信号；④ superuser 看到 `total=全库数`、`items=自己的`，分页页数虚高、翻页空白，且无法履行管理视角。
- **修复**：① `human_review` / `resume` 加 `Depends(require_roles("admin","legal"))`（或项目既有的合规角色）；② `resume` 前置校验：该 review 下 `compliance_human_actions` 至少有 1 条记录，或所有 high 风险的 `human_decision != "na"`，否则 409；③ `_is_doc_accessible` 失败改为抛专用异常，API 层映射为 403（文档不存在映射 404）；④ 过滤逻辑收敛到 Service，`total` 与 `items` 复用同一 query（`service.list_reviews(db, user_id=None if is_superuser else uid)` 并返回 `(items, total)`）。

### 问题 15（重要・性能/成本）：逐风险串行 embedding 调用 + 逐条款串行 LLM 调用，无缓存、无超时、无并发闸、无成本上限

- **位置**：`runtime.py:427-470 _enrich_references_with_rag()`（**for 循环内逐条 `self._rag_skill.execute`**，每次触发一次 embedding API 调用 + 一次向量检索 + `verify_references` 的 O(refs×candidates) `SequenceMatcher`）｜`agentsreviewer.py:134-138 review_all()`（逐条款串行 `structured.invoke`）｜`runtime.py:416`（`_risk_skill.execute` 无超时）｜`reviews.py` 全端点无请求体大小限制
- **影响**：① 本轮新增的 RAG 富化让每次审查的外部调用数从 `N条款` 变成 `N条款 + N风险`，且**全部串行**——50 条款合同在 LLM 模式下约 100 次串行远程调用，无 timeout、无退避、无并发；② 同一条款的多条风险 query 高度相似却各自重新 embedding，**零缓存**；③ BackgroundTasks 分支下这些调用全部落在 Starlette 线程池（默认 40），几十个并发审查即可耗尽线程池并**连带拖死 RAG 问答**；④ 无 token/成本统计与预算上限；⑤ `reviewer.py:123-125` 异常一律 `return []` → LLM 故障与"真的没风险"不可区分（叠加问题 6 → 直接输出"合同合规"）。
- **修复**：① 条款级审查改**批量**（一次 prompt 审 N 条）或用 LangGraph `Send` API 做 map-reduce 并行 + `asyncio.Semaphore` 限流（业界标准做法，见第四节），`RiskItem` schema 改为 `RiskItemList(risks: list[RiskItem])` 以支持单条款多风险；② RAG 富化改为**按条款批量检索**（一条款一次，而非一风险一次）+ embedding LRU 缓存 + query 归一化去重；③ 统一 LLM/embedding 调用封装：timeout、指数退避（区分 429/5xx/解析失败）、失败返回 `error` 而非空列表并计入 `review.degraded_nodes`；④ 接入 token 用量统计与每审查成本上限。

### 问题 16（一般・文档漂移）：本轮新增 4 处 docstring/README 与实现不符，且仓库自带的整改看板已过期

- **逐条对照**：
  1. `workflowsreview_graph.py:7` 的模块 docstring 仍写 `human_review → generate_report`，而 `:66` 已改为 `human_review → END`；
  2. `runtime.py:579-585 human_review` docstring 仍写"**无论是否有 high 风险都继续 generate_report，不阻塞**"和"在 `compliance_human_actions` 留痕"——前者与本轮改动直接矛盾，后者从未实现（节点内无任何 DB 写入）；
  3. `runtime.py:9-11` 模块 docstring 仍写"执行方式（MVP）：由 FastAPI **BackgroundTasks** 调用 `start_review`"、"**SSE 端点**用 DB 轮询生成器推事件（stream.py 格式化）"——Celery 已引入、SSE 端点仍不存在（`harnessstream.py` 仍 0 调用）；
  4. `viewscompliance.py:220` 的状态文案仍是 `"👤 等待人工审核（MVP 不阻塞）"`，而现在是**阻塞终态**；`:302-305` 仍写"人工审核接口已就绪…前端批量操作界面可在 P1 补全"，但 resume 已成为**必需**路径；
  5. README 未更新：无 `/resume` 端点、无 Celery worker 启动说明、HITL 仍描述为"MVP 默认不阻塞自动流程"、状态机图仍画 `pending_human --> generating`、`POST /reviews` 示例仍带 schema 中不存在的 `playbook_id`、HITL 示例仍是错误路径 `/human` + `{"risk_id": ...}`（实际 `/human-review` + `{"risk_ids": [...]}`）、`/api/health` 版本号仍与 `appmain.py` 不一致；
  6. `docsreview_comments.md`（278 行改动，仓库自带的"第四轮复评报告"）**相对 HEAD 已过期**：其问题 1（IDOR）、问题 2（seed 不向量化）、问题 6（影子测试）、问题 7（action 白名单在循环内）在 `3091110` 中都已修复却仍标为未修；而"已确认修复项"表格里有两条判断是**错的**——"HITL 不阻塞 ✅ 真落地"（实际造成问题 1）、"reflect 无风险误伤 ✅ 已修复"（实际造成问题 6）。
- **影响**：合规产品的文档就是交付物的一部分。当前 README 的合规章节有 6+ 处会导致调用失败（422/404）或误判系统行为，而内部整改看板给出了错误的安全感——这正是"改完没跑过"能够连续三轮发生的组织性原因。
- **修复**：以代码为准重写 README 合规章节（用 `/docs` 导出的 OpenAPI 片段替换手写 curl）；把 `docsreview_comments.md` 每条加 `[已修复]/[部分]/[未修]` 状态并与最新一轮对齐；把"注释即契约"纳入 code review 检查项——docstring 描述的行为若与代码不符，按缺陷处理。

### 问题 17（一般・正确性）：`human_action` 的 `note` 仍被静默丢弃、留痕仍是 Python repr、`new_risk_level` 仍无枚举校验

- **位置**：`servicesreview_service.py:320-380`（`note` 形参从头到尾**没有写入 `risk.human_note`**，而 `modelsreview.py:73` 有该列）｜`:369-370`（`old_value=str(old_val)`、`new_value=str({...})` → 存的是 `{'level': 'high', 'suggestion': None}` 这种 Python repr，单引号 + `None`，**无法机读**）｜`schemasreview.py:145-152`（`action: str`、`new_risk_level: Optional[str]` 裸字符串，`risk_ids: list[str]` 无 `max_length`）｜`modelsreview.py:63`（`risk_level = String(10)`）
- **本轮进步**：`VALID_HUMAN_ACTIONS` 白名单已提到循环外校验（`:318/333-336`）✅；`review_id` 过滤已加 ✅；错误的 `db.close()` 已移除 ✅。
- **仍存在的问题**：① 法务填写的审核意见 `note` 被丢弃 → 审计时无法知道"为什么把 high 改成 low"；② 留痕非 JSON → 无法做审计报表与机器校验；③ `schemasreview.py:147` 的 description 仍宣称支持 `batch_confirm`/`resume`，但 `VALID_HUMAN_ACTIONS` 只有 4 种 → 按文档传值会得到 400；④ `new_risk_level="critical"`（>10 字符）在 PG 下抛 `StringDataRightTruncation` → 500；⑤ `risk_ids` 无上限 + `:341-349` 逐个 `db.query`（N+1）→ 单请求塞 10 万 id 可打满连接；⑥ 等级修改不回写 review 三级计数（见问题 5）。
- **修复**：`note` → `risk.human_note`；`old_value`/`new_value` → `json.dumps(..., ensure_ascii=False)`；`action` → `Literal["confirm","modify_level","edit_suggestion","mark_false"]`（与 `VALID_HUMAN_ACTIONS` 单一来源）；`new_risk_level` → `RiskLevel` 枚举；`risk_ids` → `Field(max_length=200)` 并改 `in_()` 批量查询；修改等级后同步重算并回写 `review.high/medium/low_risk_count`。

### 问题 18（一般・架构）：死代码新增 2 个，既有 10+ 个零调用符号仍未清理

- **本轮复测结果**（全库引用检索，排除定义行/`__all__`/docstring）：
  - **新增死代码**：`apiknowledge.py:234 _parse_date()`、`:243 _ingest_articles_json()` → 委托 `ingestion.py` 后**均 0 调用**（本轮 P11 修复的副产物，应一并删除）
  - 既有仍 0 调用：`servicesnodes.py::run_to_end`（`stream_updates` 仅被自身文件引用）、`parsingparser.py::dump_result/_refine_with_llm`（`build_parsing_result`/`collect_page_map` 仅被 `parsing__init__.py` 与同文件引用）
  - 仅被 re-export：`agentsresearcher.py::ResearcherAgent`
  - 实例化后零调用：`runtime.py:120 self._playbook_skill`（上轮删掉了重复调用，但字段残留）；`runtime.py:118 self.hitl = HitlManager()` → `hitl.py` 的 `should_pause`/`record_human_action` **仅出现在自身 docstring**，`build_resume_command` 仅被 `runtime.py:583` 的注释提到
- **影响**：README 宣称的"api → services → harness"分层在合规模块仍未真正生效；`HitlManager` 与 `ReviewService.human_action` 仍是两套重复实现（前者完全不可达）；大量"看起来已实现"的能力持续误导维护者与评审者——本轮 README/docstring 的多处漂移正源于此。
- **修复**：二选一并立刻执行——**接线**（`HitlManager.record_human_action` 被 `human_action` 调用、`ResearcherAgent` 成为 research 节点、要么实现 SSE 端点要么删 `stream.py`）或**删除**（`nodes.py`、`stream.py`、两个 Service、`knowledge.py` 的 2 个新死函数、`runtime.py:118/120` 两个未用字段）。建议 CI 加 `vulture` 或自定义 AST 扫描，拦截"导出但零调用"的公开符号。

### 问题 19（一般・健壮性）：法规摄入仍非原子；`_persist_status` 仍每节点开短连接并静默吞异常

- **位置**：`knowledgeingestion.py:95-105`（幂等路径：**先删旧向量 + 删旧条款 + 删旧法规行 + commit**）→ `:136`（新数据 commit）→ `:138-139`（**事务外** `add_regulations_to_store(vec_docs)`，无 tryexcept）｜`runtime.py:140-160 _persist_status()`（`SessionLocal()` per call，一次审查 10+ 次；`except Exception → logger.warning` 后继续；`hasattr`+`setattr` 无白名单）
- **影响**：① 向量化失败（embedding 限流/超时、PG 抖动、维度不符）时，法规行与条款行**已提交但没有向量** → "库里有、检索不到"的幽灵法规；重摄入同名法规时旧数据**已被删除**，新数据向量化失败 → 该法规彻底消失且无法回退；`apiknowledge.py:120-123` 会把它包成 500 返回，但数据已半写入，客户端重试又会触发删旧重建；② 幂等键是 `title` 精确匹配（`:96`），"中华人民共和国劳动合同法"与"中华人民共和国劳动合同法（占位）"会作为两部法规并存 → 重复召回、引用来源混乱；③ `_persist_status` 在 SQLite 下并发写极易 `database is locked`，异常被降级为 warning → **进度/计数静默丢失**，前端进度条卡住而流水线继续；`hasattr`+`setattr` 会把拼错的键（如问题 9 的 `template_deviations`）无声忽略。
- **修复**：① `ComplianceRegulation` 增 `index_status`（`pending/indexed/failed`）+ `indexed_at`，采用"先写新、后删旧"的蓝绿替换，向量化失败置 `failed` 并保留旧版本；提供 `POST /knowledge/reindex`（admin）补偿；② 幂等键改 `(title, version)` 或规范化标题（剥离"（占位）"等后缀）+ 唯一约束；③ 空 `content` 条款直接 422 拒绝；④ 一次审查复用一个 Session（`start_review` 开、`finally` 关），终态写入与 `_persist_results` 放进**同一事务**，可写字段收敛为显式白名单，SQLite 开 WAL + `busy_timeout`。

### 问题 20（提示・跨模块）：`conversations.py` 异常分支把错误文案写成 assistant 消息落库，且可能与 sources 分支重复保存

- **位置**：`appconversations.py:329-338`（本轮新增）
- **事实**：SSE 异常分支现在**同步**调用 `_save_messages_background(conv_id, partial_answer or "[回答生成失败: …]", [], True)`，而正常分支（`:308-314`）用的是 `background_tasks.add_task(...)`。
- **影响**：① 错误文案 `[回答生成失败: xxx]` 会作为 assistant 消息进入对话历史 → 后续轮次的"最近 20 轮历史 + 更早摘要"会把它喂回 LLM 与检索上下文，污染 RAG 输入；② 若 `sources` 事件已经触发过保存、随后在收尾 yield 阶段抛异常，同一条回答会被**保存两次**；③ 同步 DB 写入放在 SSE 生成器里与既有异步落库风格不一致（虽然 StreamingResponse 的同步生成器跑在线程池，不阻塞事件循环）。
- **修复**：错误信息不落 `messages` 表，改为落独立的 `conversation_errors`/日志并只在响应流中返回；或落库时打 `is_error=True` 标记且在组装历史时过滤；保存前用幂等键（`conv_id + turn_index`）防重复。（此项属 RAG 主干，不在 `app/compliance` 范围内，仅提示。）
