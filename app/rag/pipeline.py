"""Document processing pipeline — parse, split, embed, index."""

from pathlib import Path

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.logging_config import get_logger
from app.models.document import Document as DocModel
from app.rag.splitters import get_default_splitter
from app.rag.vector_store import add_documents_to_store, delete_documents_from_store
from app.services.document_service import update_document_status
from app.rag.retrievers import refresh_bm25_index_from_chroma

logger = get_logger(__name__)


def load_document(file_path: str, mime_type: str) -> list[Document]:
    """从磁盘加载文件并返回LangChain的Document对象列表。"""
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
    """单个文档完整处理流程：

    1. 从磁盘加载
    2. 分成若干块
    3. 嵌入并索引到Chroma中
    4. 更新数据库状态
    """
    db: Session = SessionLocal()
    try:
        doc = db.query(DocModel).filter(DocModel.id == doc_id).first()
        if doc is None:
            logger.error(f"Document {doc_id} not found in database")
            return

        # Mark as processing
        update_document_status(db, doc_id, "processing")

        logger.info(f"the file name is: {doc.filename}, the file type is: {doc.mime_type}")
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

        # 清理该文档在 Chroma 中的旧向量（防止重复处理时累积孤儿条目）
        delete_documents_from_store(str(doc.id))

        # 存入向量数据库
        add_documents_to_store(chunks)

        # 更新数据库状态为indexed
        update_document_status(db, doc_id, "indexed", chunk_count=len(chunks))
        logger.info(f"Document {doc_id}: indexed {len(chunks)} chunks")

        # TODO：记录一个优化
        # 同步刷新 BM25 索引（让新增内容立即可在 hybrid 模式下检索到）
        refresh_bm25_index_from_chroma()

    except Exception as e:
        logger.exception(f"Document {doc_id} processing failed")
        update_document_status(db, doc_id, "failed", error_message=str(e))
    finally:
        db.close()
