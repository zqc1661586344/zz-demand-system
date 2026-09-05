"""Seed compliance_playbooks 表 — 从 app/compliance/playbook/default_rules/ 目录导入 JSON 规则包。

幂等执行：已存在同名规则（contract_type + name + priority）跳过，新规则插入。
用法：python -m app.compliance.scripts.seed_playbooks
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.database import SessionLocal
from app.models import *  # noqa: F401 — 先导入主业务表，让 Base.metadata 完整
from app.compliance.models.playbook import CompliancePlaybook
from app.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_RULES_DIR = Path(__file__).resolve().parent.parent / "playbook" / "default_rules"


def seed_one_package(db, json_path: Path) -> int:
    with open(json_path, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    rules = pkg.get("rules", [])
    contract_type = pkg.get("contract_type", "other")
    inserted = 0
    for r in rules:
        exists = (
            db.query(CompliancePlaybook)
            .filter(
                CompliancePlaybook.contract_type == contract_type,
                CompliancePlaybook.name == r["name"],
                CompliancePlaybook.priority == r.get("priority", 100),
            )
            .first()
        )
        if exists:
            continue
        row = CompliancePlaybook(
            id=str(uuid.uuid4()),
            name=r["name"],
            description=r.get("description"),
            contract_type=contract_type,
            clause_type=r.get("clause_type"),
            risk_level=r["risk_level"],
            match_type=r.get("match_type", "keyword"),
            match_pattern=r.get("match_pattern"),
            match_threshold=r.get("match_threshold", 0.8),
            legal_basis_ref=r.get("legal_basis_ref"),
            standard_position=r.get("standard_position"),
            red_line=r.get("red_line", False),
            negotiable=r.get("negotiable", True),
            suggested_clause=r.get("suggested_clause"),
            priority=r.get("priority", 100),
            is_active=True,
            version=1,
        )
        db.add(row)
        inserted += 1
    if inserted:
        db.commit()
    logger.info(
        "seeded %d rules from %s (contract_type=%s)", inserted, json_path.name, contract_type
    )
    return inserted


def seed_all() -> int:
    if not DEFAULT_RULES_DIR.exists():
        logger.warning("default_rules dir not found: %s", DEFAULT_RULES_DIR)
        return 0
    db = SessionLocal()
    try:
        total = 0
        for f in sorted(DEFAULT_RULES_DIR.glob("*.json")):
            total += seed_one_package(db, f)
        logger.info("playbook seed complete: %d new rules", total)
        return total
    finally:
        db.close()


if __name__ == "__main__":
    seed_all()
