"""Compliance ORM models — all tables prefixed with `compliance_`."""

from app.database import Base

from app.compliance.models.document import ComplianceDocument
from app.compliance.models.clause import ComplianceClause, ComplianceKeyInfo
from app.compliance.models.review import ComplianceReview, ComplianceRisk, ComplianceRiskReference
from app.compliance.models.regulation import ComplianceRegulation, ComplianceRegulationArticle
from app.compliance.models.playbook import CompliancePlaybook
from app.compliance.models.report import ComplianceReport, ComplianceHumanAction

__all__ = [
    "ComplianceDocument",
    "ComplianceClause",
    "ComplianceKeyInfo",
    "ComplianceReview",
    "ComplianceRisk",
    "ComplianceRiskReference",
    "ComplianceRegulation",
    "ComplianceRegulationArticle",
    "CompliancePlaybook",
    "ComplianceReport",
    "ComplianceHumanAction",
]