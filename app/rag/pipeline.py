"""Document processing pipeline — parse, split, embed, index."""

import logging
from pathlib import Path

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.document import Document as DocModel
from app.rag.splitters import get_default_splitter
from app.rag.vector_store import add_documents_to_store
from app.services.document_service import update_document_status

logger = logging.getLogger(__name__)


def load_document(file_path: str, mime_type: str) -> list[Document]:
    """Load a document from disk and return LangChain Document objects."""
    path = Path(file_path)

    # pdf文件
    if mime_type == "application/pdf":
        from langchain_community.document_loaders import PyPDFLoader

        loader = PyPDFLoader(str(path))
        return loader.load()

    # markdown文件
    elif mime_type == "text/plain" or mime_type == "text/markdown":
        from langchain_community.document_loaders import TextLoader

        loader = TextLoader(str(path), encoding="utf-8")
        return loader.load()

    # word文件
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        from langchain_community.document_loaders import Docx2txtLoader

        loader = Docx2txtLoader(str(path))
        return loader.load()

    else:
        raise ValueError(f"Unsupported MIME type for loading: {mime_type}")


def process_document(doc_id: str) -> None:
    """Full processing pipeline for a single document.

    1. Load from disk
    2. Split into chunks
    3. Embed and index into Chroma
    4. Update DB status
    """
    db: Session = SessionLocal()
    try:
        doc = db.query(DocModel).filter(DocModel.id == doc_id).first()
        if doc is None:
            logger.error(f"Document {doc_id} not found in database")
            return

        # Mark as processing
        update_document_status(db, doc_id, "processing")

        # 按文件类型加载文档
        raw_docs = load_document(doc.file_path, doc.mime_type)

        # Add metadata
        for d in raw_docs:
            d.metadata["document_id"] = doc.id
            d.metadata["filename"] = doc.original_filename

        # 切分chunks
        splitter = get_default_splitter()
        chunks = splitter.split_documents(raw_docs)

        if not chunks:
            # 如果没有切分出任何chunk，则标记为indexed
            update_document_status(db, doc_id, "indexed", chunk_count=0)
            logger.info(f"Document {doc_id}: empty after splitting, marked indexed")
            return

        # 存入向量数据库
        add_documents_to_store(chunks)

        # 更新数据库状态为indexed
        update_document_status(db, doc_id, "indexed", chunk_count=len(chunks))
        logger.info(f"Document {doc_id}: indexed {len(chunks)} chunks")

    except Exception as e:
        logger.exception(f"Document {doc_id} processing failed")
        update_document_status(db, doc_id, "failed", error_message=str(e))
    finally:
        db.close()
