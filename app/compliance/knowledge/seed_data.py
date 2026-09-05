"""法规种子加载器 — 从 seed_data/<contract_type>/*.json 批量摄入占位法规。

seed_data 目录按合同类型分文件夹（labor_contract/nda/...），每个 JSON 为一部法规：
    {
      "title": "法规名称",
      "regulation_type": "law",
      "publish_date": null | "YYYY-MM-DD",
      "effective_date": null | "YYYY-MM-DD",
      "expire_date": null | "YYYY-MM-DD",
      "source": "...",
      "articles": [{"article_number", "chapter", "section", "content"}, ...]
    }

加载器幂等：库中已存在同名法规则跳过（重复调用不重复入库、不重复向量化）。
MVP 阶段这些 JSON 多为占位骨架（法条全文后补），摄入管线本身已单独验证。
"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.compliance.knowledge.ingestion import ingest_regulation
from app.compliance.models.regulation import ComplianceRegulation
from app.logging_config import get_logger

logger = get_logger(__name__)

SEED_DIR = Path(__file__).resolve().parent / "seed_data"


def _iter_seed_files(contract_type: str | None = None) -> list[Path]:
    """列出种子 JSON 文件（按合同类型目录；None = 全部目录）。"""
    if contract_type:
        d = SEED_DIR / contract_type
        return sorted(d.glob("*.json")) if d.is_dir() else []
    return sorted(SEED_DIR.glob("*/*.json"))


def load_seed_regulations(
    db: Session,
    contract_type: str | None = None,
) -> dict:
    """批量摄入种子法规（幂等：同名法规已存在则跳过）。

    Args:
        db: 数据库会话（调用方提供，事务由调用方管理；本函数内部逐部提交）。
        contract_type: 可选，只摄入某合同类型目录下种子。

    Returns:
        {"imported": [title...], "skipped": [title...], "total": n}
    """
    imported: list[str] = []
    skipped: list[str] = []
    for f in _iter_seed_files(contract_type):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("skip seed file %s: %s", f.name, e)
            continue
        title = data.get("title", "")
        if not title:
            logger.warning("skip seed file %s: missing title", f.name)
            continue
        # 幂等：同名法规已存在则跳过
        exists = (
            db.query(ComplianceRegulation).filter(ComplianceRegulation.title == title).first()
        )
        if exists:
            skipped.append(title)
            continue
        try:
            ingest_regulation(
                title=title,
                regulation_type=data.get("regulation_type", "law"),
                articles=data.get("articles", []),
                publish_date=data.get("publish_date"),
                effective_date=data.get("effective_date"),
                expire_date=data.get("expire_date"),
                source=data.get("source"),
                db=db,
            )
            imported.append(title)
        except Exception as e:  # noqa: BLE001 — 单部失败不阻断整体
            logger.exception("seed import failed for %s: %s", title, e)
    result = {"imported": imported, "skipped": skipped, "total": len(imported) + len(skipped)}
    logger.info("seed regulations loaded: %s", result)
    return result