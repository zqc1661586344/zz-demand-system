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

    logger.info(f"loading document from file: {file_path},the mime type is: {mime_type}")

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

    # csv文件（纯标准库 csv 解析，无需额外依赖）
    elif mime_type == "text/csv":
        from langchain_community.document_loaders import CSVLoader

        loader = CSVLoader(file_path=str(path))
        return loader.load()

    # html文件（需 beautifulsoup4）
    elif mime_type == "text/html":
        from langchain_community.document_loaders import BSHTMLLoader

        loader = BSHTMLLoader(str(path))
        return loader.load()

    # excel文件（pandas + openpyxl 逐 sheet 转 CSV 文本，避免 unstructured 重依赖；每 sheet 一个 Document）
    elif mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        import pandas as pd

        sheets = pd.read_excel(str(path), sheet_name=None)  # dict[sheet_name, DataFrame]
        return [
            Document(
                page_content=f"### Sheet: {name}\n\n{df.astype(str).to_csv(index=False)}",
                metadata={"source": str(path)},
            )
            for name, df in sheets.items()
        ]

    # ppt文件（python-pptx 遍历每个 slide 的 text frame，每张 slide 一个 Document）
    elif mime_type == "application/vnd.openxmlformats-officedocument.presentationml.presentation":
        from pptx import Presentation

        prs = Presentation(str(path))
        docs = []
        for i, slide in enumerate(prs.slides, 1):
            parts = [sh.text for sh in slide.shapes if getattr(sh, "has_text_frame", False)]
            docs.append(
                Document(
                    page_content=f"### Slide {i}\n\n" + "\n\n".join(parts),
                    metadata={"source": str(path)},
                )
            )
        return docs

    # toml文件（Python 3.11 内置 tomllib，无需额外依赖）
    elif mime_type == "application/toml":
        from langchain_community.document_loaders import TomlLoader

        loader = TomlLoader(str(path))
        return loader.load()

    # json文件（自定义处理，兼容法规 seed_data 格式和通用 JSON）
    elif mime_type == "application/json":
        return _load_json(path)

    else:
        logger.error(f"unsupported MIME type for loading: {mime_type}")
        raise ValueError(f"Unsupported MIME type for loading: {mime_type}")


def _load_json(path: Path) -> list[Document]:
    """自定义 JSON 加载器，兼容多种格式：

    1. 法规 seed_data 格式: {title, articles: [{article_number, chapter?, content}]}  → 每条条文一个 Document
    2. 对象数组: [{key: value, ...}, ...]                                                    → 每个对象一个 Document
    3. 字符串数组: ["text", ...]                                                             → 每个字符串一个 Document
    4. 普通对象: {key: value, ...}                                                           → 整体一个 Document
    """
    with open(str(path), "r", encoding="utf-8") as f:
        data = json.load(f)

    # 格式 1：法规 seed_data — 顶层有 articles 数组
    if isinstance(data, dict) and isinstance(data.get("articles"), list):
        regulation_title = data.get("title") or data.get("name") or path.stem
        regulation_type = data.get("regulation_type", "")
        docs: list[Document] = []
        for art in data["articles"]:
            content = art.get("content", "").strip()
            if not content:
                continue
            parts = [f"# {regulation_title}"]
            if regulation_type:
                parts.append(f"类型：{regulation_type}")
            if art.get("chapter"):
                parts.append(f"章节：{art['chapter']}")
            if art.get("article_number"):
                parts.append(f"条文编号：{art['article_number']}")
            parts.append("")
            parts.append(content)
            docs.append(
                Document(
                    page_content="\n".join(parts),
                    metadata={
                        "source": str(path),
                        "regulation_title": regulation_title,
                        "article_number": art.get("article_number", ""),
                    },
                )
            )
        if docs:
            logger.info(
                f"json (regulation format): extracted {len(docs)} articles from {regulation_title}"
            )
            return docs

    # 格式 2：对象数组
    if isinstance(data, list) and data and isinstance(data[0], dict):
        docs = []
        for item in data:
            lines = []
            for k, v in item.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
                else:
                    lines.append(f"{k}: {v}")
            docs.append(
                Document(
                    page_content="\n".join(lines),
                    metadata={"source": str(path)},
                )
            )
        logger.info(f"json (list of objects): {len(docs)} items")
        return docs

    # 格式 3：字符串数组
    if isinstance(data, list) and data and isinstance(data[0], str):
        docs = [
            Document(page_content=item, metadata={"source": str(path)})
            for item in data
            if isinstance(item, str) and item.strip()
        ]
        logger.info(f"json (list of strings): {len(docs)} items")
        return docs

    # 格式 4：普通对象或其他 — 整体序列化为文本
    docs = [
        Document(
            page_content=json.dumps(data, ensure_ascii=False, indent=2),
            metadata={"source": str(path)},
        )
    ]
    logger.info("json (generic): single document")
    return docs


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
            logger.error(f"document {doc_id} not found in database")
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

        logger.info(f"loaded {len(raw_docs)} documents from file: {doc.file_path}")

        # TODO: 当前切分策略单一，比如对pdf使用ocr识别，对markdown识别标题，可以根据文件类型选择不同的分块器，如 PDF 可以使用 PageContentSplitter，图片可以使用 ImageSplitter 等等
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

        # 存入向量数据库，成功后置 indexed。若向量写入失败（重试也无望），清理刚落库的 chunk，避免 failed 文档的可检索内容残留在 document_chunks 中；异常重新抛出交由外层置 failed / Celery 重试。
        try:
            add_documents_to_store(chunks)
            update_document_status(db, doc_id, "indexed", chunk_count=len(chunks))
        except Exception:
            db.query(DocumentChunk).filter(DocumentChunk.document_id == str(doc.id)).delete()
            db.commit()
            raise
        logger.info(f"document {doc_id}: indexed {len(chunks)} chunks")

        # 文档数据变更：先广播数据版本号（使所有 worker 的相关缓存失效），再增量重建本进程自己的索引。
        is_shared = getattr(doc, "visibility", "private") == "shared"
        mark_bm25_data_changed(str(doc.uploaded_by), shared=is_shared)
        refresh_bm25_for_user(str(doc.uploaded_by))

    except (FileNotFoundError, ValueError) as e:
        logger.error(f"document {doc_id}: non-retryable error")
        update_document_status(db, doc_id, "failed", error_message=str(e))
    except Exception as e:
        logger.error(f"document {doc_id} processing failed")
        update_document_status(db, doc_id, "failed", error_message=str(e))
        # TODO: 处理异常，比如重试、记录日志等。让 Celery 机制重试
        raise
    finally:
        db.close()
