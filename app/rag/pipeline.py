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
from app.rag.splitters import get_splitter_for
from app.rag.vector_store import add_documents_to_store, delete_documents_from_store
from app.rag.loaders import load_multi_documents
from app.services.document_service import update_document_status

logger = get_logger(__name__)


def _build_search_text(chunk: Document) -> str:
    """构建用于 PG tsvector 稀疏检索的 jieba 分词串。

    在 page_content 分词的基础上，额外注入法规领域关键 metadata 字段，让稀疏检索能精确命中"第八十二条"、"劳动合同法"等法条关键词。

    注意：JSON loader 已把 regulation_title / article_number / chapter写入 page_content 头部，这里再注入相当于对这些关键词做二次加权，对 BM25 稀疏检索的排序精度是正面的 —— 法规问答场景下，关键词精确匹配比语义相似度更能决定 context_precision。
    """
    meta_extras = []
    meta = chunk.metadata or {}
    for key in ("regulation_title", "article_number", "chapter", "regulation_type"):
        val = meta.get(key)
        if val:
            meta_extras.append(str(val))
    full_text = chunk.page_content + " " + " ".join(meta_extras)
    return " ".join(_chinese_tokenizer(full_text))


def _load_doc_or_fail(db: Session, doc_id: str) -> DocModel | None:
    """查询文档记录并检查文件是否存在。

    返回 None 表示文档不存在（非重试性错误）。
    文件不存在时内部已显式标记 failed 状态。
    """
    doc = db.query(DocModel).filter(DocModel.id == doc_id).first()
    if doc is None:
        logger.error("document %s not found in database", doc_id)
        return None

    if not Path(doc.file_path).exists():
        logger.error("file not found on disk: %s", doc.file_path)
        update_document_status(db, doc_id, "failed", error_message=f"文件不存在: {doc.file_path}")
        return None

    return doc


def _load_and_split(doc: DocModel) -> list[Document] | None:
    """加载文件并切分为 chunks。

    返回 None 表示文件格式不支持。
    返回空列表表示切分后无可检索内容。
    """
    try:
        logger.info(
            "loading document from file: %s, the mime type is: %s",
            doc.file_path,
            doc.mime_type,
        )
        raw_docs = load_multi_documents(doc.file_path, doc.mime_type)
    except ValueError as e:
        logger.error("unsupported file type for doc %s: %s", doc.id, e)
        return None

    # 添加元数据
    for d in raw_docs:
        d.metadata["document_id"] = doc.id
        d.metadata["filename"] = doc.original_filename
        d.metadata["uploaded_by"] = str(doc.uploaded_by)
        d.metadata["visibility"] = getattr(doc, "visibility", "private")

    logger.info("loaded %d documents from file: %s", len(raw_docs), doc.file_path)

    splitter = get_splitter_for(doc.original_filename)
    chunks = splitter.split_documents(raw_docs)

    if not chunks:
        logger.warning("document %s: empty after splitting, marked failed", doc.id)

    return chunks


def _replace_chunks(db: Session, doc_id: str, chunks: list[Document]) -> None:
    """清理该文档的旧 chunk 并写入新 chunk 到 DocumentChunk 表。"""
    # 清理该文档在 PGVector 中的旧向量（防止重复处理时累积孤儿条目）
    delete_documents_from_store(doc_id)

    # 清理该文档在 DocumentChunk 表中的旧记录（防止重复处理累积孤儿行）
    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
    db.commit()

    # 持久化 chunks 到 DocumentChunk 表（作为 BM25 重建的数据源）
    for i, chunk in enumerate(chunks):
        dc = DocumentChunk(
            id=str(uuid.uuid4()),
            document_id=doc_id,
            chunk_index=i,
            content=chunk.page_content,
            # jieba 分词空格串：供 PG tsvector 稀疏检索（to_tsvector('simple', ...)）
            search_text=_build_search_text(chunk),
            page_number=chunk.metadata.get("page"),
            meta_json=json.dumps(chunk.metadata, ensure_ascii=False),
        )
        db.add(dc)
    db.commit()
    logger.info("persisted %d chunks to document_chunks for doc %s", len(chunks), doc_id)


def _index_chunks(db: Session, doc_id: str, chunks: list[Document]) -> None:
    """写入向量数据库。失败时回滚刚写入的 chunk 并重新抛出异常。"""
    try:
        add_documents_to_store(chunks)
        update_document_status(db, doc_id, "indexed", chunk_count=len(chunks))
    except Exception:
        # 向量写入失败（重试也无望），清理刚落库的 chunk，
        # 避免 failed 文档的可检索内容残留在 document_chunks 中
        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
        db.commit()
        raise


def _refresh_bm25(doc: DocModel) -> None:
    """文档数据变更：先广播数据版本号，再增量重建 BM25 索引。"""
    is_shared = getattr(doc, "visibility", "private") == "shared"
    mark_bm25_data_changed(str(doc.uploaded_by), shared=is_shared)
    refresh_bm25_for_user(str(doc.uploaded_by))


def process_document(doc_id: str) -> None:
    """单个文档索引完整处理流程：

    1. 从磁盘加载
    2. 分成若干块
    3. 嵌入并索引到 PGVector 中
    4. 更新数据库状态

    异常分类：
      - 预期内失败（文件不存在、格式不支持、空内容）→ return，不重试
      - 系统异常（向量库写入失败）→ raise，让上层 Celery 重试
    """
    db: Session = SessionLocal()
    try:
        doc = _load_doc_or_fail(db, doc_id)
        if doc is None:
            return

        # 将文档状态标记为 processing
        update_document_status(db, doc_id, "processing")
        logger.info("the file name is: %s, the file type is: %s", doc.filename, doc.mime_type)

        chunks = _load_and_split(doc)
        if chunks is None:
            update_document_status(db, doc_id, "failed", error_message="文件格式不支持")
            return

        if not chunks:
            # 切分后没有任何 chunk（文件为空/全空白/扫描件无文本层）
            update_document_status(
                db,
                doc_id,
                "failed",
                error_message="未提取到文本内容（可能为空文件或扫描件暂不支持 OCR ）",
            )
            return

        _replace_chunks(db, str(doc.id), chunks)
        _index_chunks(db, str(doc.id), chunks)
        _refresh_bm25(doc)

        logger.info("document %s: indexed %d chunks", doc_id, len(chunks))

    except Exception as e:
        logger.error("document %s processing failed: %s", doc_id, e)
        update_document_status(db, doc_id, "failed", error_message=str(e))
        raise  # 让 Celery 重试机制处理
    finally:
        db.close()
