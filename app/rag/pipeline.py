"""Document processing pipeline — parse, split, embed, index."""

import json
import uuid
from pathlib import Path

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.logging_config import get_logger
from app.models.document import Document as DocModel
from app.models.document import DocumentChunk
from app.rag.retrievers import invalidate_other_users_bm25, refresh_bm25_for_user
from app.rag.splitters import get_default_splitter
from app.rag.vector_store import add_documents_to_store, delete_documents_from_store
from app.services.document_service import update_document_status

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
        logger.error(f"unsupported MIME type for loading: {mime_type}")
        raise ValueError(f"Unsupported MIME type for loading: {mime_type}")


def process_document(doc_id: str) -> None:
    """单个文档索引完整处理流程：

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

        # 将文档状态标记为processing
        update_document_status(db, doc_id, "processing")

        logger.info(f"the file name is: {doc.filename}, the file type is: {doc.mime_type}")
        # 按文件类型加载文档
        raw_docs = load_document(doc.file_path, doc.mime_type)

        # 添加元数据
        for d in raw_docs:
            d.metadata["document_id"] = doc.id
            d.metadata["filename"] = doc.original_filename
            d.metadata["uploaded_by"] = str(doc.uploaded_by)
            d.metadata["visibility"] = getattr(doc, "visibility", "private")

        # 切分chunks
        # TODO：根据不同的文档类型选择不同的切分器，比如pdf引入ocr，不同的切分策略
        splitter = get_default_splitter()
        chunks = splitter.split_documents(raw_docs)

        if not chunks:
            # 如果没有切分出任何chunk，则标记为indexed
            update_document_status(db, doc_id, "indexed", chunk_count=0)
            logger.info(f"document {doc_id}: empty after splitting, marked indexed")
            return

        # 清理该文档在 Chroma 中的旧向量（防止重复处理时累积孤儿条目）
        delete_documents_from_store(str(doc.id))

        # 清理该文档在 DocumentChunk 表中的旧记录（防止重复处理累积孤儿行）
        db.query(DocumentChunk).filter(DocumentChunk.document_id == str(doc.id)).delete()
        db.commit()

        # 持久化 chunks 到 DocumentChunk 表（作为 BM25 重建的数据源）
        for i, chunk in enumerate(chunks):
            dc = DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=str(doc.id),
                chunk_index=i,
                content=chunk.page_content,
                page_number=chunk.metadata.get("page"),
                meta_json=json.dumps(chunk.metadata, ensure_ascii=False),
            )
            db.add(dc)
        db.commit()
        logger.info(f"persisted {len(chunks)} chunks to document_chunks for doc {doc_id}")

        # 存入向量数据库
        add_documents_to_store(chunks)

        # 更新数据库状态为indexed
        update_document_status(db, doc_id, "indexed", chunk_count=len(chunks))
        logger.info(f"document {doc_id}: indexed {len(chunks)} chunks")

        # 增量刷新该用户的 BM25 索引（从 DB 而非 Chroma 全量读取）
        refresh_bm25_for_user(str(doc.uploaded_by))
        # 共享文档变更 → 其他用户的 BM25 缓存失效（下次查询懒加载重建）
        if getattr(doc, "visibility", "private") == "shared":
            invalidate_other_users_bm25(except_user_id=str(doc.uploaded_by))

    except Exception as e:
        logger.exception(f"Document {doc_id} processing failed")
        update_document_status(db, doc_id, "failed", error_message=str(e))
    finally:
        db.close()
