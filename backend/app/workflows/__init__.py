"""LangGraph 工作流层（B5-a）。

learning_workflow：诊断 → RAG 检索 → 生成 → 审核 的多智能体状态机，
审核低分回生成重试（≤2 次），仍不过降级输出；全程记录 11.2 可渲染 trace。
"""
