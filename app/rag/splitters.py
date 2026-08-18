"""Text splitting strategies for document chunking."""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings


def get_default_splitter():
    """返回根据设置配置的默认文本分割器（递归切割）。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,  # 默认800
        chunk_overlap=settings.chunk_overlap,  # 默认150
        separators=["\n\n", "\n", "。", ".", " ", ""],
        length_function=len,
    )
