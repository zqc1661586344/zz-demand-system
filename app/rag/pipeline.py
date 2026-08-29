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
from app.rag.retrievers import _chinese_tokenizer, mark_bm25_data_changed, refresh_bm25_for_user
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
    3. 嵌入并索引到PGVector中
    4. 更新数据库状态

    异常分类：
      - ValueError（文件格式不支持）→ 不可重试，直接标记 failed
      - FileNotFoundError（文件已被删除）→ 不可重试，直接标记 failed
      - 其他异常 → 让上层 Celery 重试机制处理
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

        # 检查文件是否存在（必须在 load_document 之前，否则死代码）
        if not Path(doc.file_path).exists():
            raise FileNotFoundError(f"File not found on disk: {doc.file_path}")

        # 按文件类型加载文档
        try:
            raw_docs = load_document(doc.file_path, doc.mime_type)
        except ValueError as e:
            logger.error(f"unsupported file type for doc {doc_id}: {e}")
            update_document_status(db, doc_id, "failed", error_message=str(e))
            return

        # 添加元数据
        for d in raw_docs:
            d.metadata["document_id"] = doc.id
            d.metadata["filename"] = doc.original_filename
            d.metadata["uploaded_by"] = str(doc.uploaded_by)
            d.metadata["visibility"] = getattr(doc, "visibility", "private")

        # 切分chunks
        splitter = get_default_splitter()
        chunks = splitter.split_documents(raw_docs)

        if not chunks:
            # 切分后没有任何 chunk（文件为空/全空白/扫描件无文本层）→ 无可检索内容，标记 failed
            update_document_status(
                db,
                doc_id,
                "failed",
                error_message="未提取到文本内容（可能为空文件或扫描件，暂不支持 OCR）",
            )
            logger.warning(f"document {doc_id}: empty after splitting, marked failed")
            return

        # 清理该文档在 PGVector 中的旧向量（防止重复处理时累积孤儿条目）
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
                # jieba 分词空格串：供 PG tsvector 稀疏检索（to_tsvector('simple', ...)）
                search_text=" ".join(_chinese_tokenizer(chunk.page_content)),
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

        # 文档数据变更：先广播数据版本号（使所有 worker 的相关缓存失效），
        # 再增量重建本进程自己的索引。
        is_shared = getattr(doc, "visibility", "private") == "shared"
        mark_bm25_data_changed(str(doc.uploaded_by), shared=is_shared)
        refresh_bm25_for_user(str(doc.uploaded_by))

    except (FileNotFoundError, ValueError) as e:
        logger.exception(f"document {doc_id}: non-retryable error")
        update_document_status(db, doc_id, "failed", error_message=str(e))
    except Exception as e:
        logger.exception(f"document {doc_id} processing failed")
        update_document_status(db, doc_id, "failed", error_message=str(e))
        raise  # 让 Celery 机制重试
    finally:
        db.close()
