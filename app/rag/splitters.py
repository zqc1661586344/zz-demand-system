"""Text splitting strategies for document chunking."""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings


def get_default_splitter():
    """Return the default text splitter configured from settings."""
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", "。", ".", " ", ""],
        length_function=len,
    )


def get_splitter_for_mime(mime_type: str):
    """Return an appropriate splitter for the given MIME type."""
    return get_default_splitter()