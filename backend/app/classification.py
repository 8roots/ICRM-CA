"""Content-based material classification candidates.

The worker runs a deterministic keyword classifier over parsed material text
and stores per-document category candidates. Candidates are only suggestions:
an item is never satisfied by an unconfirmed classification (see the
completeness evaluator), and the application owner must explicitly confirm a
category per document before it can drive mapping suggestions.
"""

from enum import StrEnum

from sqlalchemy.orm import Session

from app.models import Document, DocumentOutput, MaterialClassificationCandidate

CLASSIFICATION_METHOD = "content_keyword"
CLASSIFICATION_VERSION = "1"

MAX_CANDIDATES_PER_DOCUMENT = 3
MIN_CONFIDENCE = 0.2


class MaterialCategory(StrEnum):
    BASIC_INFO = "basic_info"
    OPERATION = "operation"
    LOAN_APPLICATION = "loan_application"
    PURPOSE = "purpose"
    CREDIT = "credit"
    COLLATERAL = "collateral"
    OTHER = "other"


CATEGORY_LABELS = {
    MaterialCategory.BASIC_INFO: "基础信息",
    MaterialCategory.OPERATION: "经营",
    MaterialCategory.LOAN_APPLICATION: "贷款申请",
    MaterialCategory.PURPOSE: "用途",
    MaterialCategory.CREDIT: "征信",
    MaterialCategory.COLLATERAL: "抵押担保",
    MaterialCategory.OTHER: "其他",
}

# Category keywords are deliberately generic; confidence comes from how many
# distinct keywords match. Short generic words that appear in many materials
# (for example 合同) are kept out of the scoring sets.
CATEGORY_KEYWORDS: dict[MaterialCategory, tuple[str, ...]] = {
    MaterialCategory.BASIC_INFO: (
        "营业执照",
        "统一社会信用代码",
        "身份证",
        "户口本",
        "结婚证",
        "法定代表人",
        "任职证明",
    ),
    MaterialCategory.OPERATION: (
        "财务报表",
        "利润表",
        "资产负债表",
        "现金流量表",
        "损益表",
        "审计报告",
        "纳税申报",
        "银行流水",
        "对账单",
        "交易明细",
        "账户明细",
    ),
    MaterialCategory.LOAN_APPLICATION: (
        "借款申请书",
        "借款申请",
        "贷款申请",
        "授信申请",
        "贷款申请书",
        "额度申请",
    ),
    MaterialCategory.PURPOSE: (
        "购销合同",
        "采购合同",
        "销售合同",
        "买卖合同",
        "订货单",
        "采购订单",
    ),
    MaterialCategory.CREDIT: (
        "征信报告",
        "信用报告",
        "征信授权",
        "查询授权书",
        "个人征信",
        "企业征信",
    ),
    MaterialCategory.COLLATERAL: (
        "抵押物",
        "抵押合同",
        "担保合同",
        "保证合同",
        "产权证",
        "不动产权证",
        "评估报告",
        "保证人",
    ),
}


def classify_text(text: str) -> list[tuple[MaterialCategory, float]]:
    """Rank content-based category candidates for one material's text.

    The score of a category is the number of distinct keywords found in the
    text; confidence is the score normalized against the top score, so the best
    category always has confidence 1.0 and ties keep deterministic order.
    Returns candidates above ``MIN_CONFIDENCE``, capped at
    ``MAX_CANDIDATES_PER_DOCUMENT``.
    """
    haystack = text.lower()
    scored: list[tuple[MaterialCategory, int]] = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword in haystack)
        if hits:
            scored.append((category, hits))
    if not scored:
        return []
    best = max(hits for _, hits in scored)
    ranked = sorted(scored, key=lambda pair: (-pair[1], pair[0].value))
    candidates = [
        (category, hits / best)
        for category, hits in ranked
        if hits / best >= MIN_CONFIDENCE
    ]
    return candidates[:MAX_CANDIDATES_PER_DOCUMENT]


def output_text(output: DocumentOutput) -> str:
    parts: list[str] = []
    for page in output.pages:
        for block in page.blocks:
            parts.append(block.text)
            parts.extend(cell.text for cell in block.cells)
    return "\n".join(parts)


def run_classification(db: Session, document: Document, output: DocumentOutput) -> None:
    """Store classification candidates for one parsed output, idempotently."""
    for category, confidence in classify_text(output_text(output)):
        existing = (
            db.query(MaterialClassificationCandidate)
            .filter_by(
                document_id=document.id,
                category=category.value,
                method=CLASSIFICATION_METHOD,
            )
            .first()
        )
        if existing:
            continue
        db.add(
            MaterialClassificationCandidate(
                document_id=document.id,
                category=category.value,
                confidence=confidence,
                method=CLASSIFICATION_METHOD,
                method_version=CLASSIFICATION_VERSION,
            )
        )
    db.flush()
