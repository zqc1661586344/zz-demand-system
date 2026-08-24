"""一次性迁移脚本：给已有的 Chroma chunks 追加 uploaded_by + visibility metadata。

使用场景：在部署多租户隔离代码之前，Chroma 数据库中的旧 chunks 没有
uploaded_by 和 visibility 字段，导致上线后普通用户检索不到这些旧文档。

运行方式：
    cd /Users/zz/LanguagePath/python/LLM/zz-demand-system
    .venv/bin/python scripts/migrate_chroma_tenant.py

前提条件：
    1. app.db 中已有 Document 记录（含 uploaded_by 和 visibility）
    2. data/chroma/ 目录存在旧索引数据
    3. 无需启动任何服务

注意：
    - 该脚本只会更新已有记录的 metadata，不会删除或修改向量数据
    - 如果 chroma 目录为空或不存在，脚本会优雅退出
    - 对于 DB 中查不到的 document_id（如已删除文档的孤儿 chunk），保留原 metadata 不变
"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def main():
    from app.config import settings
    from app.database import SessionLocal
    from app.models.document import Document

    # 1. 从 DB 读取所有文档的 uploaded_by 和 visibility
    db = SessionLocal()
    try:
        docs = db.query(Document).all()
        doc_map = {
            str(d.id): {
                "uploaded_by": str(d.uploaded_by),
                "visibility": d.visibility or "private",
            }
            for d in docs
        }
    finally:
        db.close()

    if not doc_map:
        print("DB 中没有找到任何文档记录，无需迁移。")
        return

    print(f"从 DB 读取到 {len(doc_map)} 个文档的元数据")

    # 2. 检查 Chroma 持久化路径是否存在
    chroma_path = Path(settings.chroma_persist_path)
    if not chroma_path.exists():
        print(f"Chroma 数据目录不存在: {chroma_path}，跳过迁移。")
        return

    # 3. 连接 Chroma 并读取现有数据
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection_name = settings.chroma_collection_name

    try:
        collection = client.get_collection(collection_name)
    except Exception as e:
        print(f"Chroma collection '{collection_name}' 不存在或无法访问: {e}")
        return

    data = collection.get()
    if not data["ids"]:
        print("Chroma collection 中没有数据，无需迁移。")
        return

    # 4. 逐条更新 metadata
    updated_metadatas = []
    update_count = 0
    skip_count = 0

    for mid in data["metadatas"]:
        m = dict(mid) if mid else {}
        doc_id = m.get("document_id", "")
        if doc_id in doc_map:
            m["uploaded_by"] = doc_map[doc_id]["uploaded_by"]
            m["visibility"] = doc_map[doc_id].get("visibility", "private")
            update_count += 1
        else:
            # 文档已被删除但 chunk 还在 Chroma 中 → 保留原 metadata
            skip_count += 1
        updated_metadatas.append(m)

    # 5. 批量更新
    collection.update(ids=data["ids"], metadatas=updated_metadatas)
    print(
        f"迁移完成：更新 {update_count} 个 chunks 的 metadata，"
        f"跳过 {skip_count} 个已删除文档的孤儿 chunk。"
    )


if __name__ == "__main__":
    main()