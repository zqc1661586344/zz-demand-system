"""Text splitting strategies for document chunking."""

from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.config import settings


def _default_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", "。", ".", " ", ""],
        length_function=len,
    )


def get_splitter_for(filename: str | Path):
    """根据文件名/扩展名返回合适的切分器。

    - Markdown (.md/.markdown) → MarkdownHeaderTextSplitter 保留标题层级
    - JSON (.json) → None（JSON loader 已按 article 粒度切分，不再二次切割）
    - 其他 → RecursiveCharacterTextSplitter（默认递归切分）
    """
    ext = Path(str(filename)).suffix.lower()

    if ext in (".md", ".markdown"):
        try:
            from langchain_text_splitters import MarkdownHeaderTextSplitter

            headers_to_split_on = [
                ("#", "header_1"),
                ("##", "header_2"),
                ("###", "header_3"),
                ("####", "header_4"),
            ]
            return MarkdownHeaderTextSplitter(
                headers_to_split_on=headers_to_split_on,
                return_each_line=False,
            )
        except ImportError:
            return _default_splitter()

    if ext == ".json":
        return None

    return _default_splitter()


def get_default_splitter() -> RecursiveCharacterTextSplitter:
    """返回默认递归切分器（兼容旧调用点）。"""
    return _default_splitter()
