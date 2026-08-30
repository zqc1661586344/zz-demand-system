"""PG 原生全文检索（tsvector + ts_rank + GIN）——替代内存版 BM25 的稀疏检索后端。

与 `app/rag/retrievers.py` 的内存 BM25 相比：
  - 数据源相同（document_chunks.content），但无需全量载入进程内存、无需全量重建；
    写入时同步维护 `search_text`（jieba 分词空格串），删除时随行删除，天然增量。
  - 检索直接落在 PG，`to_tsvector('simple', search_text)` 的 GIN 表达式索引加速。
  - 多进程天然一致：每次查询读库即最新，无需 Redis 时间戳/进程内 LRU 缓存。

仅在 `settings.database_url` 指向 PostgreSQL 时可用；SQLite 开发环境由调用方回退
到 `bm25_memory`（见 `retrievers.py::hybrid_search`）。
"""

import json

from langchain_core.documents import Document
from sqlalchemy import text

from app.config import settings
from app.database import engine
from app.logging_config import get_logger
from app.rag.retrievers import _chinese_tokenizer

logger = get_logger(__name__)

# 与 `_user_where`（vector_store.py）一致的权限语义：本人私有 + 全部共享；superuser 不过滤。
_USER_SCOPE_SQL = " (d.uploaded_by = :uid OR d.visibility = 'shared') "


def is_pg_available() -> bool:
    """database_url 是否指向 PostgreSQL（tsvector 后端的前提，SQLite 不支持）。"""
    return settings.database_url.startswith("postgres")


def tokenize_query(query: str) -> str:
    """对查询做与索引一致的 jieba 分词，空格 join。

    索引侧存储的是 `" ".join(_chinese_tokenizer(content))`，查询侧必须用同样的分词，
    否则 `simple` 全文配置下中文字符会被当作一个整体、无法与已切好的词条精确匹配。
    """
    return " ".join(_chinese_tokenizer(query))


def ensure_fts_index() -> None:
    """幂等地为 document_chunks.search_text 建 GIN 表达式索引（仅 PG）。

    分两步，均为幂等操作、无需停机：
      1. `ALTER TABLE ... ADD COLUMN IF NOT EXISTS search_text TEXT` —— 补上旧 schema 缺失的列
      2. `CREATE INDEX IF NOT EXISTS ... ON ... USING gin (to_tsvector('simple', search_text))`

    SQLite 环境直接返回。
    """
    if not is_pg_available():
        logger.info(
            "database_url is not PostgreSQL — skip FTS GIN index (%s)", settings.database_url
        )
        return
    try:
        with engine.begin() as conn:
            # 1) 保证 search_text 列存在（旧库可能没有此列，create_all 不会给已存在表补列）
            conn.execute(
                text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS search_text TEXT")
            )
            # 2) 建 GIN 表达式索引（已存在则跳过）
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_document_chunks_search_gin "
                    "ON document_chunks USING gin (to_tsvector('simple', search_text))"
                )
            )
        logger.info("ensured search_text column + GIN FTS index on document_chunks")
    except Exception as e:  # noqa: BLE001 —— 补列/索引失败只影响检索性能，不应阻断应用启动
        logger.warning("failed to ensure search_text column / GIN FTS index: %s", e)


def search(query: str, top_k: int = 5, user_id: str | None = None) -> list[Document]:
    """PG 全文稀疏检索，等价替代内存 `get_bm25_for_user` 返回的稀疏 retriever。

    Args:
        query: 原始查询字符串（内部做与索引一致的 jieba 分词）。
        top_k: 返回的 chunk 数量上限（即融合前的稀疏候选取回量）。
        user_id: 用户 ID；None 表示 superuser（不过滤权限）。

    Returns:
        list[Document]，metadata 含 document_id/page 等，供后续 RRF 融合与重排使用。
    """
    ts_query = tokenize_query(query)
    where = _USER_SCOPE_SQL if user_id is not None else " TRUE "

    sql = text(
        f"""
        SELECT c.id AS chunk_id, c.content, c.meta,
               ts_rank(to_tsvector('simple', c.search_text), q.ts, 1) AS r
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        CROSS JOIN (SELECT plainto_tsquery('simple', :q_str) AS ts) AS q
        WHERE c.search_text IS NOT NULL
          AND to_tsvector('simple', c.search_text) @@ q.ts  -- 仅返回真正命中关键词的行（无关查询为空）
          AND d.status = 'indexed'                           -- 跳过 failed/pending 等未完成文档的 chunk
          AND {where}
        ORDER BY r DESC NULLS LAST
        LIMIT :k
        """
    )
    params: dict = {"q_str": ts_query, "k": top_k}
    if user_id is not None:
        params["uid"] = user_id

    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
    except Exception as e:  # noqa: BLE001 —— 检索失败回退空结果，由调用方决定是否纯稠密
        logger.warning("PG tsvector search failed for query=%r: %s", query[:50], e)
        return []

    results: list[Document] = []
    for row in rows:
        meta = {}
        if row["meta"]:
            try:
                meta = json.loads(row["meta"])
            except (TypeError, ValueError):
                meta = {}
        # 统一 metadata：让稀疏结果的 document_id/源信息与稠密结果对齐，便于 RRF 去重。
        meta.setdefault("chunk_id", row["chunk_id"])
        meta.setdefault("content", row["content"])
        results.append(Document(page_content=row["content"], metadata=meta))

    logger.info(
        "PG tsvector sparse: query=%r top-%d hits=%d",
        query[:50],
        top_k,
        len(results),
    )
    return results
