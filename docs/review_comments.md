# 合规审查模块（app/compliance）代码评审 · 第五轮

## 一、问题清单（按严重性排序）

### 问题 1（致命・正确性）：`resume_review` 读取 checkpoint 用了不存在的 `.values` 属性 → HITL resume 100% 崩溃

- **位置**：`appruntime.py` L815–L822，`ComplianceHarness.resume_review()`
- **实证**：本项目锁定 langgraph-checkpoint 4.2.0（uv.lock），其 `CheckpointTuple` 是 NamedTuple，字段为 `(config, checkpoint, metadata, parent_config, pending_writes)`，**没有 `.values` 属性**（已解包 wheel 核验源码 L139–146）。`tuple_result.values` 必抛 `AttributeError`，被外层 `except Exception` 吞掉 → `_persist_status(review_id, STATUS_FAILED)` → 返回 error。
- **影响**：默认配置 `compliance_hitl_enabled=True`（`appconfig.py` L165）下，任何含高风险的审查走到 `pending_human` 后，调用 `POST /reviews/{id}/resume` **必然把 review 打成 failed**，正式报告永远无法生成。正确写法是 `tuple_result.checkpoint["channel_values"]`。
- **为何测试没拦住**：`teststest_test_case_items.py` 的 `TestHitlEndToEnd` 只测了参数校验和 checkpointer 类型告警，**没有任何一条测试真正跑过 resume 路径**。
- **评分影响**：HITL 流程 1/10。

### 问题 2（致命・正确性）：人工决策按 id 匹配永远失配 → 即使修好问题 1，`mark_false`/改级/改写建议也全部被静默丢弃

- **位置**：`runtime.py` `_persist_results()` L299–311 vs `_merge_human_decisions()` L845–942
- **机理**：`review_clauses` 给 state 中每条 risk 分配了 `id`（L464 `risk.setdefault("id", uuid4())`），前端与人工审核使用的却是 **DB 行 id**——而 `_persist_results` 落库时为每条 risk **重新生成 UUID**（L300 `id=str(_uuid.uuid4())`），完全忽略 `r.get("id")`。resume 时 `_merge_human_decisions` 用 state 里的 id 去 `db_by_id.get(rid)` 匹配 → 恒为 None → 所有风险原样保留：**被法务标记为误报（rejected）的高风险会在最终报告里复活，改级/改写全部回退为 LLM 原始输出**。
- 这是上一轮问题 5 的第四轮复发：加了 merge 函数，但 id 对齐这个前提没修。附带伤害：resume 后二次 `_persist_results` 再次重建全部 risk 行（新 UUID、`human_decision` 重置为 "na"），**风险行上的人工审核字段被抹掉**，只剩 `compliance_human_actions` 留痕孤证。
- **修复方向**：`_persist_results` 复用 state 的 `r["id"]` 作为主键（upsert 而非 delete-recreate），或给 ComplianceRisk 增加 `state_risk_id` 列供 merge 匹配；merge 后回写时保留 `human_decision/human_note/human_reviewed_by`。

### 问题 3（致命・数据一致性）：`compliance_human_actions.risk_id` 外键无 ON DELETE，SQLite 又开启了外键强制 → resume 时删除旧风险行必炸 IntegrityError

- **位置**：`appreport.py` L41（`ForeignKey("compliance_risks.id")`，无 ondelete）；`alembica1c0mp1i4nce_add_compliance_tables.py` L250（同样无 ondelete，对比 L162 的 risk_references 有 CASCADE）；`appdatabase.py` L30–36（SQLite `PRAGMA foreign_keys=ON`）；触发点 `runtime.py` `_persist_results` L270–272 的 bulk delete。
- **影响**：HITL 场景下必然已存在 human_action 行引用旧 risk 行 → `DELETE FROM compliance_risks WHERE review_id=...` 在 **SQLite 和 PG 下都会外键违例** → 回滚 → review 置 failed。与问题 1、2 叠加，HITL 链路三重死。
- **修复**：迁移为 `ondelete="SET NULL"`（留痕表保留、risk_id 置空），或改为 upsert 不删行（与问题 2 的修复合并考虑）。

### 问题 4（致命・正确性）：`compare_template` 拿规则关键词去匹配"条款号"而非条款内容，且 `template_id` 从头到尾没有加载过模板 → 模板比对/红线升级全失效

- **位置**：`runtime.py` `compare_template()` L618–630
- **机理**：L619 `cn = risk.get("clause_number")`，L623–624 用 `rule.match_pattern`（如"试用期,试用期工资"）与 `cn`（如"第三条"）做子串互含判断 → **恒不命中** → `best_rule` 恒 None → `template_deviation`、`suggested_clause` 补全、`red_line` 升级为 high 三个功能全部不可达。
- 更根本的：`template_id` 只出现在 `should_compare` 的路由条件（L775）和落库字段里，**全链路没有任何代码按 template_id 加载模板文档做偏离比对**——"企业模板比对"是空壳。同时 `_persist_status(..., template_deviations=deviations)`（L658–662）因 `ComplianceReview` 无此列被 `hasattr` 检查静默丢弃（上一轮问题 9 残留）。
- **评分影响**：模板比对流程 1/10。

### 问题 5（重要・正确性）：风险→条款（clause_id）与风险→规则（playbook_rule_id）追溯连续第四轮恒为 NULL

- **位置**：三处断链叠加——
  1. `parsingclause_splitter.py` L76 输出的条款只有 `clause_number/title/content`，**没有 clause_id**；`runtime.py` L453 `clauses_by_number` 的 value 全是 None，L466–467 的"回填"等于没填；
  2. `_persist_results` L294–296 只认 `r.get("clause_index")`，**全链路没有任何生产者写过 clause_index**（`_hit_to_risk`、RiskItem schema 均无此字段）；L228/L245 精心构建的 `clause_id_by_number` 是**从未被使用的死代码**（本来能救回这条链）；
  3. `agentsreviewer.py` `_hit_to_risk()` L47–64 丢弃了 playbook 引擎命中里的 `rule_id`；`runtime.py` L277–292 的 fallback 找 `r.get("rule_id")` / `r.get("rule_name")`，而 `RiskItem.model_dump()` 的字段名是 `playbook_rule_id` / `playbook_rule_name` → 全对不上；L454–455 `rules_by_category` 用 `r.get("risk_category") or r.get("category")` 索引，但 `_load_active_rules`（review_service.py L171–189）产出的规则字典**没有这两个键** → 全部落到 "" 桶。
- **影响**：DB 中 `ComplianceRisk.clause_id`、`playbook_rule_id` 恒 NULL → `GET /reviews/{id}` 的每条风险 `clause_number/clause_content` 均为 null（review_service.get_review L271–279 依赖 clause_map），报告无法定位条款原文，规则命中统计/审计不可行。
- **修复**：parse 阶段为每条 clause 生成稳定 id 并全程透传；`_hit_to_risk` 补 `"playbook_rule_id": hit.get("rule_id")`；`_persist_results` 用已构建的 `clause_id_by_number` 按 `r["clause_number"]` 兜底。

### 问题 6（重要・架构/正确性）：LLM 模式下 Playbook 确定性匹配完全旁路——红线规则没有关键词层兜底

- **位置**：`runtime.py` `review_clauses()` L450（只调 RiskSkill）；`agentsreviewer.py` `review_clause()` L106–125
- **机理**：非 test 模式下 `match_rules_for_clauses` **根本不会被调用**——全部活跃规则被整体塞进每一条条款的 prompt 当"线索"（L114–117 `playbook_hints=rules`，注意是全部规则而非本条款命中项），LLM 结果不与确定性命中做后置融合。LLM 漏报 = 红线条款直接漏检，`red_line` 规则形同虚设（唯一升级入口 compare_template 又是坏的，见问题 4）。docstring 宣称的"结构化 LLM + Playbook 命中线索融合"未实现。
- 连带：`PlaybookSkill` 在 harness 实例化（runtime L149）但**全仓库零调用**；README 流程图把 PlaybookSkill 画成独立图节点，与实现不符。
- **业界对照**：合规场景的标准做法是"确定性规则层保底 + LLM 增量发现"，两层结果按 (clause, rule) 去重融合，规则命中永不被 LLM 否决。

### 问题 7（重要・正确性）：结构化输出绑定单对象 `RiskItem`，prompt 却要求"输出列表/无风险输出空列表" → 强制幻觉或静默失败二选一

- **位置**：`agentsreviewer.py` L107（`get_structured_llm(RiskItem)`）+ `agentsreviewer_prompt.py` 输出要求段 + L118–122 的 isinstance 兼容分支
- **机理**：`with_structured_output(RiskItem)` 的 function-calling schema 强制 LLM 产出**恰好一个** RiskItem（所有必填字段必须填）。条款无风险时 LLM 无法表达"空列表"，只能编造一条低质量风险（假阳性）或触发校验异常 → except → `return []`（假阴性）。一条条款多个风险时也只能返回一个。
- **修复**：定义 `class RiskItemList(BaseModel): risks: list[RiskItem]` 绑定它；业界（instructor / OpenAI structured outputs）均推荐"列表必须包一层容器 schema"。

### 问题 8（重要・可靠性）：LLM 逐条款失败被静默吞掉 → 全量失败时产出 quality 0.9 的"未检出风险，审查质量良好"报告

- **位置**：`agentsreviewer.py` L123–125（except → return []）；`runtime.py` `compute_reflect_quality` L104–110
- **机理**：每条款 LLM 异常只 log 不计数，state 无 `llm_error_count`。LLM 全挂时：clauses 有、risks 空、has_rules 真 → coverage=0.9、avg_conf=0.9 → **quality=0.9 ≥ 0.7 首轮即 completed**，`should_retry` 的 low_conf 分支（要求 avg_conf<0.6）也拦不住。报告结论"未检出风险项"+"审查质量良好"——这是合规产品最危险的假阴性形态。`_extract_with_llm` 失败同样静默降级。
- **修复**：失败条款计数进 state，reflect 将 `llm_failure_ratio` 纳入 degraded_reasons 并封顶 quality；失败率超阈值 → status=failed 或报告显著降级标注，绝不允许输出"干净"结论。

### 问题 9（重要・正确性）：初始 state 键名错位——写入 `contract_type`，声明和读取都是 `contract_type_override`

- **位置**：`runtime.py` `start_review()` L983（`"contract_type": contract_type_override`）vs `workflowsstate.py` L63（声明 `contract_type_override`）vs `runtime.py` `supervise()` L421（读 `state.get("contract_type_override")` 恒 None）
- **影响**：用户指定的合同类型只影响 service 层规则加载，从未进入图内；Supervisor 的类型复核永远拿不到 override；未声明的 `contract_type` 键不是合法 channel，被 LangGraph 丢弃。上一轮问题 13 修了一半、键名修错了。

### 问题 10（重要・数据一致性）：同一文档二次审查交叉污染——clauses/key_info 按 `compliance_doc_id` 查询与删除，而 comp_doc 跨 review 复用

- **位置**：`review_service.py` `create_review()` L92–105（复用 comp_doc）、`get_review()` L236–249（按 compliance_doc_id 查 clauseskey_info）；`runtime.py` `_persist_results` L251–253（按 compliance_doc_id **全删** KeyInfo）
- **影响**：第二次审查同一文档 → 第一次审查的详情里混入第二次的条款（重复展示），且第一次的 key_info 被删除。上一轮问题 10 只在 clause 插入侧补了 review_id，查询侧与 KeyInfo 删除侧没改。
- **修复**：clauses/key_info 的查询与删除一律按 `review_id` 收敛（ComplianceKeyInfo 需要加 review_id 列）。

### 问题 11（重要・架构）：reflect→review 重试是"空转循环"——无纠正性反馈输入，衰减项反而保证重试必败

- **位置**：`runtime.py` `reflect()` L568–607、`should_retry()` L781–796、`compute_reflect_quality` L112（`decay = 0.15 * retry_count`）
- **机理**：retry 边回到 review 节点时，clauses/rules/prompt 与上一轮**完全相同**，`degraded_reasons` 不回流给任何节点——确定性路径结果恒等，LLM 路径也只是原样重掷。quality 随 retry 单调衰减，意味着"重试直到预算耗尽"是数学必然：零规则场景 quality 封顶 0.5 < 阈值 0.7 → **每次审查固定空跑 4 轮完整 review**（LLM 模式 = 4×N 条款次调用的纯浪费）。
- **业界对照**：Reflexion/Self-Refine 类模式的核心是"把反思结论作为下一轮的输入"（如：无规则→降级为纯 LLM 审查并放宽阈值；无 verified 引用→扩大 top_k 重检索；条款抽取为空→换解析策略）。当前实现只有"反思打分"没有"反思改进"，建议要么注入纠正动作，要么在确定性场景直接跳过 retry。

### 问题 12（重要・可靠性/运维）：任务不可恢复、Celery 重试被业务层短路、无卡死回收

- **位置**：`runtime.py` `start_review()` L1003–1010（吞掉一切异常返回 status dict）；`tasks.py` L31–42（autoretry_for 配置专业但永不触发）；`appmain.py` lifespan（有 `_recover_stuck_documents` 却没有 compliance 对应物）
- **机理**：`start_review` 内部 try/except 把网络/LLM 瞬时错误全部转成 `{"status":"failed"}` 正常返回 → Celery 看到的是成功结果，`autoretry_for=(ConnectionError, TimeoutError, openai.*)` **一次都不会触发**，瞬时故障直接终结审查。BackgroundTasks 模式（默认，CELERY_BROKER_URL 为空）下进程重启 = 任务蒸发，review 永久卡在 parsing/reviewing，无超时、无 watchdog、无启动回收。checkpointer 明明存了断点，却没有任何"从 checkpoint 续跑卡死任务"的机制。
- **修复**：瞬时异常在 harness 层重新抛出（或返回可判别标记）让 Celery 重试生效；lifespan 增加 `_recover_stuck_reviews`（超时置 failed 或从 checkpoint 续跑）。

### 问题 13（重要・性能/成本）：逐条款串行 LLM + 逐风险串行 embedding，无并发/超时/缓存/成本上限（上一轮问题 15 原样未动）

- **位置**：`agentsreviewer.py` `review_all()` L127–140（for 循环逐条 invoke）；`runtime.py` `_enrich_references_with_rag()` L491–543（逐风险串行检索+校验）
- **量化**：50 条款合同 = 50 次串行 LLM 调用 + R 次串行 embedding 调用，再乘以问题 11 的空转重试（×4）。无任何 per-call timeout、无 asyncio.gather/线程池并发、无相同 query 的 embedding 缓存、无 token/成本预算闸。
- **业界对照**：批量并发（受限流闸）+ 指数退避重试 + 语义缓存是 RAG/Agent 流水线标配；LangGraph 可用 Send API 做条款级 map-reduce 并行。

### 问题 14（重要・权限/契约）：超管列表分页口径错乱、越权返回 400、"legal" 角色从未播种、README 端点路径错误

- **位置**：
  1. `apireviews.py` `list_reviews()` L109–115：superuser 的 `total` 按全表统计，`items` 却走 `service.list_reviews(user_id=current_user.id)` **恒按 created_by 过滤** → 超管看到 total=全表、items=仅自己创建，翻页逻辑错乱（service 的 `user_id=None` 分支成了死代码）；
  2. `create_review` L75–76：service 抛 `ValueError("forbidden: ...")` 被映射为 **400**，应为 403（上一轮问题 14 残留）；
  3. `require_roles("admin", "legal")`（L139/156/187）：全系统只播种 admindatabase.py` `_seed_roles` L64–68），**"legal" 角色不存在任何创建入口**，人工审核事实上 admin-only，与"法务团队"产品定位矛盾；且 demo admin 绑定的是 viewer 角色（main.py `_seed_demo_user`）；
  4. README L376 写 `/reviews/<review-id>/human`，实际路由是 `human-review`（reviews.py L150）——照 README 的 curl 必 404。

### 问题 15（重要・安全）：法规 file_path 校验已做监狱（点赞），但存在旁路副本、不可达路径与异常透传

- **位置**：`schemasregulation.py` L37–60（resolve 包含校验 + symlink 拒绝 + 后缀白名单，**较上轮显著改善**）；`servicesregulation_service.py` `create_regulation()` L107–168；`apiknowledge.py` L121–131/L185
- **残余问题**：
  1. `RegulationService.create_regulation` 是绕过上述校验的**重复实现**（直接 `ingest_from_file(file_path,...)` 无任何路径监狱，且不向量化、delete 不清向量）——当前零调用是运气不是设计，属于埋雷；
  2. 该校验要求文件已在 `compliance_regulation_dir` 内，但**系统没有任何上传法规文件的 API** → file_path 分支生产不可达；一旦有人手工放文件走通，`create_regulation` 的 file_path 分支还会因"先 commit r 行 → ingest_regulation 同名幂等删旧重建（新 UUID）→ 外层 `db.refresh(r)` 对已删实例刷新"抛 `ObjectDeletedError`，且返回旧 reg_id——该分支一用即坏；
  3. `HTTPException(500, f"ingest failed: {exc}")` / `f"search failed: {exc}"` 把内部异常文本（可能含服务器路径、DSN 片段）直接透传给客户端，建议对外统一错误文案、对内记日志。
  4. 顺带：`apiplaybooks.py` 对 `match_pattern` 的 `re:` 正则不做创建期编译校验与复杂度限制（engine 运行期捕获 re.error，但灾难性回溯 ReDoS 无防护——admin-only 降低了风险，仍建议 `re.compile` 预检 + 超时）。

### 问题 16（一般・架构）：死代码与僵尸组件本轮继续净增，文档性 docstring 与事实相反

- **清单**（全部零引用，已 grep 实证）：
  - `harnessstream.py` 整个模块（SSE 事件格式化写好了，**没有任何 API 端点使用**，前端实际是 2s 轮询）；
  - `harnesscheckpointer.py` `get_checkpointer()`（harness 直接调 `build_checkpointer`，lru_cache 单例成了摆设）；
  - `harnesshitl.py` **整个 HitlManager**（`should_pause`/`record_human_action`/`build_resume_command` 无人调用；模块 docstring 声称"human_review 节点调 record_human_action 写留痕表"——事实是 human_review 节点根本没调它，留痕走的是 ReviewService.human_action，两套逻辑并存且 hitl.py 版还是旧的 repr 留痕）；
  - `agentsresearcher.py` **ResearcherAgent**（README 架构图五 Agent 之一，实际链路零引用，RagSkill 直接调 retrieval+verifier）；
  - `skillsplaybook_skill.py` PlaybookSkill（实例化未调用，见问题 6）；
  - `parsingparser.py` `build_parsing_result`/`parse_document`/`collect_page_map`（主链路 ParseSkill 直连 load_text+split，页码映射整体未接入 → 报告 page_number 恒空）；`clause_splitter._refine_with_llm`；
  - `apiknowledge.py` `_parse_date`/`_ingest_articles_json`（与 ingestion 重复的死副本）；
  - **死配置 4 项**：`compliance_citation_similarity_threshold`（verifier docstring 声称使用它，代码硬编码 0.8/0.5）、`compliance_playbook_semantic_threshold`、`compliance_hitl_auto_confirm_low`（"低风险自动确认"从未实现）、`compliance_default_contract_type`（review_service L122 硬编码字面量 "labor_contract"）。

### 问题 17（一般・正确性）：引用校验短文本放行过宽 + RAG 补充引用无分数下限 + 占位法条仍会流入报告

- **位置**：`knowledgecitation_verifier.py` L90（`ref_norm in content_norm and len(ref_norm) >= 2`）；`runtime.py` `_enrich_references_with_rag` L528–535；`knowledge/seed_data/labor_contract/劳动合同法.json`
- **机理**：归一化后 ≥2 字的引用做子串命中即 `verified=True`——"工资""保密"这类双字词在任意含该词的条款上都会被打"✓ 已校验"绿标，与"逐字校验"的严肃语义不符；补充引用取 `hits[:3]` 无 score 下限（日志字段 `low_score_filtered` 实为"截断条数"，命名误导），而种子库仍含"【占位示例条文，真实全文待补】"内容——上一轮问题 4 修掉了"无条件 verified=True"（本轮 `_rag_hits_to_references` 诚实标注 verified=False + needs_human_check，点赞），但占位条文仍会以"候选法规依据"身份进入法务报告。
- **修复**：子串放行阈值提到 ≥10 字；补充引用加 score floor（如 0.5）；种子数据入库前过滤"占位"标记或给 hit 打 placeholder 标签透出。

### 问题 18（一般・正确性）：Extractor 的伪正则、噪声规则抽取与 8000 字截断

- **位置**：`agentsextractor.py` L24（关键词 `"自.*起至"` 走 `kw in haystack` 字面子串匹配，**永远不命中**——正则当字符串用了）；L58–72（`haystack.find(kw)` 取全文第一处，"自"extractor_prompt.py`（`max_chars=8000` 硬截断，长合同后半部的期限/争议解决条款必丢，无 map-reduce 或分段抽取）；另外条款分类 `_classify_clause_type` 在 LLM 生产模式下**也只有关键词路径**，README"Extractor 条款分类（LLM）"名不符实。

### 问题 19（一般・文档/测试）：README 与实现的漂移本轮未收敛，测试盲区恰好覆盖全部 P0

- README LangGraph 流程图节点序（PlaybookSkill→RiskSkill→RagSkill→ReviewerAgent）、"Supervisor 制定审查计划"（实际是 `agentssupervisor.py` L18–25 的静态常量 `DEFAULT_PLAN`，无 LLM 无路由决策）、"Researcher 检索法规"（未接入）均与代码不符；`reportinggenerator.py` L249 docstring"三路并行"实为串行；Word 报告文件名规则（`compliance_report_{id前8位}_{ts}.docx`，word_exporter L~165）与 HTML/PDF（`review-{id}-{ts}`）不一致，三路文件无法按名对应；word_exporter L81 把 Markdown 星号 `**{total}**` 原样打进 Word 正文，L64 使用已弃用的 `datetime.utcnow()`。
- 测试侧：`tests/compliance` 只覆盖纯函数（reflect 打分、citation、HTML/Word 渲染、入口权限），**harness 图路由、resume、_persist_results、merge_human_decisions 零集成测试**；更糟的是 `TestSecondRunIsolation` 把"两次运行 risk id 必须不同"写成断言——这正是问题 2 所依赖的错误行为的固化。建议按"每个 P0 配一条回归测试"的原则补齐（内存 checkpointer + test LLM 模式下 resume e2e 完全可测）。

### 问题 20（提示・前端联动）：Streamlit 侧无 HITL 操作与 resume 入口，后端语义已变但前端文案未同步

- **位置**：`appcompliance.py` L227（仍显示"等待人工审核（**MVP 不阻塞**）"——后端本轮已改为真阻塞：human_review→END 等 resume）、L311（提示"前端批量操作界面可在 P1 补全"）。全前端无任何调用 `/human-review` 或 `/resume` 的代码 → 默认配置下高风险审查在产品界面上**永久停在 pending_human**，只能靠 curl（而 curl 又会撞上问题 1）。前后端联合起来看，HITL 是"后端三重坏 + 前端零入口"。

---

## 二、对比业界标准的改进路线（按优先级）

**P0（立即，修完 HITL 才算上线）**
1. `resume_review` 改读 `tuple_result.checkpoint["channel_values"]`；补一条 InMemorySaver 下"pending_human → human_action → resume → completed"的 e2e 回归测试（test 模式即可跑通，这条测试同时能拦住问题 2/3）。
2. `_persist_results` 复用 state risk id（delete-recreate 改 upsert），merge 后保留 human_decision 字段；alembic 迁移 `compliance_human_actions.risk_id` → `ON DELETE SET NULL`。
3. `compare_template` 改为对条款**内容**匹配（并把命中时用的 clause content 存进 state），或诚实地把该节点与 template_id 字段一起下线到 P1。
4. HITL 迁移到 LangGraph 标准 `interrupt()` + `Command(resume=...)`：human_review 节点内 `interrupt(payload)`，resume 走 `graph.invoke(Command(resume=decisions), config)`，让 checkpoint 成为唯一事实源——这一步能顺带消灭"绕过图调节点函数"的状态脱节。

**P1（2–4 周，正确性与生产化）**
5. 追溯链统一修复：clause 生成稳定 id 全程透传 + `_hit_to_risk` 带上 rule_id + persist 用 clause_number 兜底（问题 5）。
6. LLM 模式接入确定性 Playbook 层：先 `match_rules_for_clauses` 得 hits，按条款过滤后作为 prompt 线索（含 rule_id 让 LLM 可回填），LLM 结果与 hits 融合去重，红线命中不可被 LLM 否决（问题 6）。
7. `RiskItemList` 容器 schema + 每条款失败计数进 reflect + 失败率超阈值降级/失败（问题 7/8）。
8. 检索前置：review 前按条款批量检索法规，`regulation_hits` 真正传入 RiskSkill（上一轮问题 11），RAG 补充引用加 score floor（问题 17）。
9. retry 带纠正反馈或确定性场景短路；条款级 LLM 调用改并发（LangGraph Send / asyncio.gather + 信号量）+ per-call timeout + 成本上限（问题 11/13）。
10. lifespan 增加 `_recover_stuck_reviews`；`start_review` 区分瞬时/永久异常，让 Celery autoretry 真正生效；resume 端点用条件 UPDATE（`WHERE status='pending_human'` 检查 rowcount）做乐观锁防并发重入（问题 12/14）。
11. 权限收尾：superuser 的 items/total 口径统一、forbidden 映射 403、播种 legal 角色（或改 require_roles 为 admin+superuser 并更新文档）、README 端点路径修正（问题 14）。

**P2（持续偿还）**
12. 死代码大扫除：stream.py（或把 SSE 端点真正做起来）、HitlManager、ResearcherAgent、RegulationService（或让 API 改调 service，二选一消灭双实现）、parser 未用函数、4 个死配置；`TestSecondRunIsolation` 的"ids 必不同"断言随问题 2 修复一并反转。
13. 长文档抽取 map-reduce、伪正则修复、报告三路命名统一、Word 星号/utcnow、citation 短文本阈值、种子占位条文过滤。
14. 可观测性：接入 LangFuse/LangSmith 或 OTel，落 per-node 耗时/token/成功率指标（README 待办自认项）；给审查质量建"金标合同评测集"，把**假阴性率**（有风险合同被判干净）作为发版门禁——这是合规 Agent 区别于普通 RAG 的最关键评测维度。
