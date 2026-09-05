"""图节点薄封装（app/compliance/workflows/nodes.py）。

设计文档目录要求本文件存在；职责与 runtime.py 分工：
  - 节点**业务实现**集中在 ComplianceHarness（harness/runtime.py）：
    parse/supervise/extract/review/reflect/compare/human_review/generate_report；
  - 本文件只提供**图级执行辅助**（供单测 test_review_graph 与外部复用）：

    stream_updates(harness, initial, config)  生成器：逐节点更新事件
    invoke(harness, initial, config)          跑到 END，返回合并后的最终 state
    run_to_end(harness, initial, thread_id)   便捷入口（自动生成 thread config）

不重复任何业务逻辑。
"""

from typing import Iterator, Optional

from app.compliance.harness.runtime import ComplianceHarness, _thread_config


def stream_updates(
    harness: ComplianceHarness,
    initial: dict,
    config: dict,
) -> Iterator[dict]:
    """生成器：以 updates 模式逐节点产出事件（{节点名: state_update}）。"""
    for event in harness.graph.stream(initial, config, stream_mode="updates"):
        yield event


def invoke(harness: ComplianceHarness, initial: dict, config: dict) -> dict:
    """执行到 END，返回合并后的最终 state（节点更新的并集，后写覆盖）。"""
    final: dict = {}
    for event in stream_updates(harness, initial, config):
        for update in event.values():
            if isinstance(update, dict):
                final.update(update)
    return final


def run_to_end(
    harness: ComplianceHarness,
    initial: dict,
    thread_id: str,
) -> dict:
    """便捷入口：自动构造 thread config 并跑到 END。返回最终 state。"""
    config = _thread_config(thread_id)
    return invoke(harness, initial, config)