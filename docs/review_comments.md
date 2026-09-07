# 合规审查模块（app/compliance）代码评审 · 第六轮

## 一、问题清单（按严重性排序）

### 问题 1（重要・架构/正确性）：LLM 模式下 Playbook 确定性匹配仍完全旁路——红线规则无兜底层（上轮问题 6 未修）

- **位置**：`harnessruntime.py` `review_clauses()` L450（只调 `RiskSkill`）；`agentsreviewer.py` `review_clause()` L107–125
- **机理**：非 test 模式下，`match_rules_for_clauses` **只在 `review_clause` 的 structured LLM 为 None 时才被调用**（降级路径 L110–112）。正常 LLM 路径把全部规则塞进 prompt 当"线索"，LLM 结果不与确定性命中做后置融合。LLM 漏报 = 红线条款直接漏检。
- **本轮变化**：`PlaybookSkill` 死代码已删除（点赞），但**删除了死代码不等于接入了确定性层**。当前 LLM 路径仍然只靠 prompt hints，没有"确定性命中 + LLM 融合去重"的后置步骤。
- **业界对照**：合规场景的标准做法是"确定性规则层保底 + LLM 增量发现"，两层结果按 `(clause_number, playbook_rule_id)` 去重融合，规则命中永不被 LLM 否决。
- **修复方向**：在 `review_clauses` 节点内，先调 `match_rules_for_clauses` 获取确定性命中，再调 `RiskSkill`（LLM 路径），最后按 `(clause_number, playbook_rule_id)` 做 union——确定性命中始终保留，LLM 补充新发现。
- **评分影响**：Playbook 规则匹配流程 4/10（确定性引擎本身可用，但 LLM 模式绕过了它）。

### 问题 2（重要・可靠性）：reflect→review 重试仍是"空转循环"——无纠正性反馈输入（上轮问题 11 未修）

- **位置**：`runtime.py` `reflect()` L568–607、`should_retry()` L797–826、`compute_reflect_quality` L112（`decay = 0.15 * retry_count`）
- **本轮改善**：`should_retry` 增加了两个短路条件——零规则+零风险跳过 retry（空转保护）、LLM 全失败跳过 retry（必败保护），**这显著减少了无意义的空转**，是实质进步。
- **残余问题**：当 retry 确实触发时（quality < 0.7 且有部分风险），re-enter `review_clauses` 时 clauses/rules/prompt **与上一轮完全相同**，`degraded_reasons` 不回流给任何节点。确定性路径结果恒等，LLM 路径也只是原样重掷。decay 保证 quality 单调递减 → 数学上"重试直到预算耗尽"仍是必然。
- **业界对照**：Reflexion/Self-Refine 类模式的核心是"把反思结论作为下一轮的输入"。建议：
  1. 无规则 → 降级为纯 LLM 审查并放宽阈值；
  2. 无 verified 引用 → 扩大 `top_k` 重检索；
  3. 条款抽取为空 → 换解析策略；
  4. 确定性场景直接短路跳过 retry（已部分实现）。
- **评分影响**：Reflect 自反思与重试 5/10（短路保护加分，但纠正反馈仍未实现）。

### 问题 3（重要・性能/成本）：逐条款串行 LLM + 逐风险串行 embedding，无并发/超时/缓存/成本上限（上轮问题 13 未修）

- **位置**：`agentsreviewer.py` `review_all()` L127–140（for 循环逐条 invoke）；`runtime.py` `_enrich_references_with_rag()` L509–563（逐风险串行检索+校验）
- **量化**：50 条款合同 = 50 次串行 LLM 调用 + R 次串行 embedding 调用。无任何 per-call timeout、无 `asyncio.gather`/线程池并发、无相同 query 的 embedding 缓存、无 token/成本预算闸。
- **业界对照**：批量并发（受限流闸）+ 指数退避重试 + 语义缓存是 RAG/Agent 流水线标配；LangGraph 可用 `Send` API 做条款级 map-reduce 并行。
- **评分影响**：性能与成本 4/10。

### 问题 4（重要・可靠性/运维）：卡死审查无回收机制，`lifespan` 缺少 `_recover_stuck_reviews`

- **位置**：`appmain.py` lifespan（有 `_recover_stuck_documents` 却没有 compliance 对应物）；`runtime.py` `start_review` 的异常处理
- **本轮改善**：`start_review` 现在区分瞬时/永久异常并 re-raise 瞬时异常让 Celery autoretry 生效（问题 12 部分修复，点赞）。`tasks.py` 的 `run_compliance_review` 配置专业（`acks_late`、`reject_on_worker_lost`、`time_limit`、`soft_time_limit`）。
- **残余问题**：
  1. BackgroundTasks 模式（默认，CELERY_BROKER_URL 为空）下进程重启 = 任务蒸发，review 永久卡在 `parsing`/`reviewing`，无超时、无 watchdog、无启动回收；
  2. 即使有 checkpointer 存了断点，也没有"从 checkpoint 续跑卡死任务"的机制；
  3. `_persist_status` 的 `except` 吞掉一切异常 → 某些场景下 review 状态更新失败但不报错。
- **修复方向**：lifespan 增加 `_recover_stuck_reviews`（超时 > 30min 的 review 置 failed 或从 checkpoint 续跑）；resume 端点用条件 UPDATE 做乐观锁防并发重入。
- **评分影响**：任务调度 6/10（Celery 配置专业，但 BackgroundTasks 模式仍无保护）。

### 问题 5（重要・权限/契约）：超管分页口径、forbidden 状态码、legal 角色播种（上轮问题 14 部分修复）

- **位置**：
  1. `apireviews.py` `list_reviews()` L109–125：**本轮已修**——admin 时 `user_id=None` 正确传入 service，total 和 items 口径统一（点赞）；
  2. `create_review` L75–76：service 抛 `ValueError("forbidden: ...")` **仍被映射为 400**，应为 403；
  3. `require_roles("admin", "legal")`（L143/L171/L196）：全系统**"legal" 角色仍无创建入口**（只播种了 admin/viewer），人工审核事实上 admin-only；
  4. README 端点路径 `/reviews/<review-id>/human` vs 实际路由 `human-review`（L171）**仍未修正**。
- **评分影响**：API 鉴权 7.5/10（分页修复加分，但 400/403 和 legal 角色未修）。

### 问题 6（一般・正确性）：`compare_template` 逻辑已下线但函数体仍保留错误匹配代码

- **位置**：`runtime.py` `compare_template()` L628–683；`should_compare()` L791–796
- **本轮改善**：`should_compare` 恒返回 `"skip"` 并附详细注释说明下线原因（点赞），这是正确的防御性措施。
- **残余问题**：`compare_template` 函数体仍存在，其匹配逻辑（L638–648 `rule.match_pattern` 与 `cn`（clause_number）做子串互含判断）仍恒不命中。虽然当前不可达，但：
  1. 代码维护者可能误以为函数可用而取消 skip；
  2. `_persist_status(..., template_deviations=deviations)` 的 `template_deviations` 列在 `ComplianceReview` ORM 中不存在，会被 `hasattr` 静默丢弃。
- **建议**：要么把函数体改为 `raise NotImplementedError("P1: template comparison not implemented")`，要么在函数体开头加 assert False 兜底。
- **评分影响**：模板比对 2/10（诚实下线加 1 分，但死代码仍有误导风险）。

### 问题 7（一般・正确性）：引用校验短文本放行过宽 + RAG 补充引用无分数下限

- **位置**：`knowledgecitation_verifier.py` L90（`ref_norm in content_norm and len(ref_norm) >= 2`）；`runtime.py` `_enrich_references_with_rag` L528–535
- **本轮改善**：coverage 算法改用 `get_matching_blocks` 做单向覆盖率（点赞）；短文本（<15 字符归一化后）只走精确子串判定（点赞）；空库诚实降级 verified=False（点赞）。
- **残余问题**：
  1. 精确子串匹配阈值仍为 `len >= 2`（归一化后），"工资""保密"这类双字词仍会误匹配为 verified=True；
  2. RAG 补充引用 `hits[:3]` 无 score floor——低分命中仍会进入报告作为"候选法规依据"；
  3. 种子数据中的"占位示例条文"标记未被过滤。
- **修复方向**：子串放行阈值提到 ≥10 字（归一化后）；补充引用加 score floor（如 0.5）；种子入库前过滤占位标记。
- **评分影响**：法规检索与引用校验 7/10。

### 问题 8（一般・架构）：ORM 与 migration 的 `ondelete` 声明不一致

- **位置**：`modelsreport.py` L41（`ForeignKey("compliance_risks.id")`，无 `ondelete`）；migration `b2d3c4e5f6a7` L82–86（`ON DELETE SET NULL`）
- **影响**：DB 层 FK 约束有 `SET NULL`（migration 正确），但 ORM 模型未声明 `ondelete="SET NULL"`。SQLAlchemy 在 ORM 层做 `session.delete()` 时不知道级联策略，可能产生不必要的额外 DELETE 语句。同时，新开发者看 ORM 模型无法知道 DB 层的真实行为。
- **修复**：在 ORM 中补上 `ondelete="SET NULL"`：
  ```python
  risk_id = Column(String(36), ForeignKey("compliance_risks.id", ondelete="SET NULL"), nullable=True)
  ```

### 问题 9（一般・正确性）：`_persist_results` 的 delete-recreate 模式导致引用重建和 human_action risk_id 置 NULL

- **位置**：`runtime.py` `_persist_results()` L269–272（先删全部旧 risks 再重建）
- **机理**：虽然本轮修复了 risk id 保留（`r.get("id") or str(uuid4())`），但 `_persist_results` 仍然：
  1. 先 `DELETE FROM compliance_risks WHERE review_id=...`（L269–272）
  2. 因为 migration 加了 `ON DELETE SET NULL`，`compliance_human_actions.risk_id` 被置为 NULL
  3. 然后重新 INSERT 相同 id 的 risk 行
  4. 但 `human_actions.risk_id` 已经 NULL，不会自动恢复指向
- **影响**：在 `human_review` 节点调 `_persist_result`（L719–733）后又调 `resume_review` → `_merge_human_decisions` 查 DB 时，`human_actions` 行的 `risk_id` 已 NULL → 无法关联到具体风险行。不过当前 `human_action` API 直接修改 `ComplianceRisk` 行的字段（`human_decision`/`risk_level`/`suggestion`），`_merge_human_decisions` 是从 `ComplianceRisk` 行读取而非从 `human_actions` 表读取，所以**实际链路不受影响**。但 `human_actions` 留痕表的审计价值降低（risk_id 全 NULL）。
- **修复方向**：改为 upsert（按 id 更新而非 delete-recreate），或仅在 `human_review` 节点跳过 risk 的 delete-recreate（因为此时 risks 刚从 state 落库，不需要删）。

### 问题 10（一般・正确性）：Extractor 伪正则和 8000 字截断仍存在（上轮问题 18 未修）

- **位置**：`agentsextractor.py` L24（关键词 `"自.*起至"` 走 `kw in haystack` 字面子串匹配，**永远不命中**——正则当字符串用了）；`agentsextractor_prompt.py`（`max_chars=8000` 硬截断）
- **影响**：`_CLAUSE_TYPE_RULES` 中 `"自.*起至"` 这一条永远不匹配（`"自.*起至" in "自2026年1月1日起至..."` 为 False，因为 `.*` 是字面量而非正则）。长合同后半部的期限/争议解决条款必丢。
- **修复**：用 `re.search(kw, haystack)` 替换 `kw in haystack`（仅对含正则特殊字符的关键词），或把 `"自.*起至"` 改为普通关键词 `"起至"`。

### 问题 11（提示・前端联动）：Streamlit 侧无 HITL 操作与 resume 入口

- **位置**：`appcompliance.py`
- **本轮变化**：未见前端 HITL 相关改动。
- **影响**：后端 HITL 链路已修复（问题 1/2/3 已修），但前端仍无调用 `/human-review` 或 `/resume` 的代码 → 默认配置下高风险审查在产品界面上**永久停在 pending_human**，只能靠 curl。
- **评分影响**：产品可用性 4/10。

### 问题 12（提示・安全）：法规 file_path 旁路副本 + 异常透传（上轮问题 15 残余）

- **位置**：`servicesregulation_service.py` `create_regulation()` 中的 file_path 分支；`apiknowledge.py` 异常处理
- **本轮改善**：`schemasregulation.py` 的路径监狱校验（resolve + symlink 拒绝 + 后缀白名单）已完善（点赞）；知识 API 的异常处理有所改善。
- **残余问题**：
  1. `RegulationService.create_regulation` 中的 file_path 分支仍绕过 schema 校验直接调 `ingest_from_file`——当前零调用是运气不是设计；
  2. `HTTPException(500, f"ingest failed: {exc}")` 仍可能把内部异常文本透传给客户端。

---

## 二、对比业界标准的改进路线（按优先级）

**P0（立即修完）**

1. **Playbook 确定性层融合**（问题 1）：在 `review_clauses` 节点内，先 `match_rules_for_clauses` 获取确定性命中，再调 LLM，按 `(clause_number, playbook_rule_id)` union 去重。红线命中不可被 LLM 否决。
2. **前端 HITL 入口**（问题 11）：Streamlit 合规审查页增加"人工审核"操作面板（调 `/human-review`）和"确认并生成报告"按钮（调 `/resume`）。
3. **forbidden 映射 403**（问题 5.2）：`create_review` 捕获 `ValueError("forbidden:...")` 时 raise 403。

**P1（生产化）**

1. **reflect 纠正反馈**（问题 2）：degraded_reasons 回流——无规则 → 放宽阈值；无 verified 引用 → 扩大 top_k；LLM 全失败 → 降级为纯 Playbook 模式。
2. **并发 LLM 调用**（问题 3）：条款级 `asyncio.gather` + 信号量限流 + per-call timeout（30s）；RAG 引用检索改批量并发。
3. **stuck review 回收**（问题 4）：lifespan `_recover_stuck_reviews`（超时 30min → failed）；resume 端点条件 UPDATE 乐观锁。
4. **legal 角色播种**（问题 5.3）：在 `_seed_roles` 中增加 legal 角色，或改 `require_roles` 为 `admin+superuser`。
5. **引用校验收紧**（问题 7）：子串放行 ≥10 字；RAG 补充引用加 score floor ≥0.5。

**P2（持续偿还）**

9. **ORM ondelete 对齐**（问题 8）：模型声明补 `ondelete="SET NULL"`。
10. **Extractor 伪正则**（问题 10）：`"自.*起至"` 改用 `re.search` 或改为普通关键词。
11. **死代码清理**（问题 6）：`compare_template` 函数体改 raise NotImplementedError。

