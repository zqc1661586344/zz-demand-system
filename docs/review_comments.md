# 代码评审报告

问题1：流式路径引用越界校验仍缺失
位置：appchain.py → query_rag_stream()
分析：sanitize_citations() 至今只挂在非流式 query_rag()。Streamlit 主入口走流式路径，full_answer 未经校验就随 sources 事件发出并落库——[来源 3] 越界引用照样进历史消息。流式场景前端已逐 token 渲染，事后只能修库。
建议：query_rag_stream() 在 yield {"type": "sources", ...} 之前加一行 full_answer = sanitize_citations(full_answer, sources)。一行修复。
