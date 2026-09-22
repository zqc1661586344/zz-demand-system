"""文档分块中的文本分割策略。
将过长的文本切分为多个适合嵌入模型（bge-m3 最大 8192 token）和检索粒度（chunk_size=800）的短块。
"""

from abc import ABC, abstractmethod
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from app.config import settings


# 抽象类，定义切分器策略的接口
class SplitterStrategy(ABC):
    @abstractmethod
    def split_documents(self, documents: list[Document]) -> list[Document]:
        """将文档列表切分为更小的 chunks。策略内部自行处理 API 差异。"""
        ...


# 默认切分器策略，递归切分文本
class DefaultSplittingStrategy(SplitterStrategy):
    def __init__(self):
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.chunk_size,
            chunk_overlap=settings.chunk_overlap,
            separators=["\n\n", "\n", "。", ".", " ", ""],
            length_function=len,
        )

    def split_documents(self, documents: list[Document]) -> list[Document]:
        return self._splitter.split_documents(documents)


# Markdown 切分器策略，按标题层级切分
class MarkdownHeaderStrategy(SplitterStrategy):
    def __init__(self):
        from langchain_text_splitters import MarkdownHeaderTextSplitter

        headers_to_split_on = [
            ("#", "header_1"),
            ("##", "header_2"),
            ("###", "header_3"),
            ("####", "header_4"),
        ]

        self._splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on,
            return_each_line=False,
        )

    def split_documents(self, documents: list[Document]) -> list[Document]:
        # Markdown 需要先拼回完整文本再切，才能正确识别标题层级
        text = "\n\n".join(d.page_content for d in documents)
        chunks = self._splitter.split_text(text)

        # ── 保留原始 metadata ──────────────────────────────────
        # MarkdownHeaderTextSplitter.split_text() 接收纯文本字符串，
        # 返回的 chunks 只有 splitter 注入的 header_* 元数据。
        # pipeline.py 中注入的 filename / document_id / uploaded_by
        # / visibility 全部丢失，需要手动补回。
        if chunks and documents:
            base = documents[0].metadata.copy()
            # 不覆盖 header_* 元数据，只补 pipeline 注入的业务字段
            for chunk in chunks:
                for k, v in base.items():
                    if not k.startswith("header_"):
                        chunk.metadata.setdefault(k, v)

        return chunks


# HTML 切分器策略
class HtmlHeaderStrategy(SplitterStrategy):
    def split_documents(self, documents: list[Document]) -> list[Document]:
        return DefaultSplittingStrategy().split_documents(documents)


# TODO：需要增加处理 PDF 中类似“表格”数据的逻辑，考虑表格数据的切分策略
# PDF 切分器策略
class TableAwarePageStrategy(SplitterStrategy):
    def split_documents(self, documents: list[Document]) -> list[Document]:
        return DefaultSplittingStrategy().split_documents(documents)


# TODO：根据文件类型动态添加切分器策略，后续需要拓展更多文件类型
STRATEGY_MAP = {
    ".md": MarkdownHeaderStrategy(),
    ".markdown": MarkdownHeaderStrategy(),
    ".html": HtmlHeaderStrategy(),  # 预留
    ".htm": HtmlHeaderStrategy(),  # 预留
    ".pdf": TableAwarePageStrategy(),  # 预留
    ".docx": TableAwarePageStrategy(),  # 预留
}


def get_splitter_for(filename: str | Path) -> SplitterStrategy:
    """根据文件名/扩展名返回合适的切分器。

    - Markdown (.md/.markdown) → MarkdownHeaderStrategy（按标题层级切分）

    - 其他（含 JSON） → DefaultSplittingStrategy（纯递归切分）
    """
    ext = Path(str(filename)).suffix.lower()

    return STRATEGY_MAP.get(ext, DefaultSplittingStrategy())
