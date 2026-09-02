"""文档合规审查模块（Document Compliance Review）。

面向企业法务/合规团队的「智能文档审查副驾驶」：
上传合同 → 条款解析 → 5 类风险识别分级 → 法规知识库检索 + 引用强制校验 →
审查报告导出，全程由 LangGraph 审查工作流编排（app/compliance/workflows）。

模块原则（与 app/rag、app/workflows 零耦合）：
- 全部代码在本包内，原有模块不做修改
- 数据库表全部 compliance_ 前缀，与业务表隔离
- 法规向量库用独立 PGVector collection=compliance_regulations
- 由 COMPLIANCE_ENABLED 配置开关控制是否加载（路由、模型注册）
"""