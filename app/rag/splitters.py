"""Text splitting strategies for document chunking."""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings


def get_default_splitter():
    """Return the default text splitter configured from settings."""
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,  # 默认800
        chunk_overlap=settings.chunk_overlap,  # 默认150
        separators=["\n\n", "\n", "。", ".", " ", ""],
        length_function=len,
    )
