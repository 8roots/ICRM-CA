"""Local deterministic extraction over parsed materials."""

import io
from datetime import date

import pytest

from app.extraction_service import run_candidate_extraction
from app.fields import SUBJECT_LABELS, SubjectRole
from app.main import create_app
from app.models import Application, Base, CandidateFact, Document, DocumentOutput, User
from app.parsed_outputs import store_parsed_output
from app.structured import parse_structured

CORPORATE_MATERIAL = """# 企业贷款申请材料

## 一、企业概况
企业名称：示例企业有限公司
统一社会信用代码：91330100MA27XW1234
法定代表人：王小明
注册资本：5000万元

## 二、拟议贷款
贷款金额：800万元
贷款期限：24个月
贷款用途：补充流动资金
拟签约日期：2026年8月7日
年利率：3.85%
还款方式：等额本息

## 三、财务报表（2025年度）
| 科目 | 金额 |
| --- | --- |
| 营业收入 | 12,345,678.90 |
| 营业成本 | 9,876,543.21 |
| 净利润 | 1,234,567.89 |

## 四、银行流水
| 交易日期 | 交易对手 | 金额 | 余额 |
| --- | --- | --- | --- |
| 2026-07-01 | 杭州某贸易公司 | 50,000.00 | 120,000.00 |
| 2026-07-02 | 某供应商 | -12,000.00 | 108,000.00 |

## 五、征信与担保
报告日期：2026年7月15日
负债余额：200万元
保证人：李四
保证方式：连带责任保证
评估价值：1500万元
"""

INDIVIDUAL_MATERIAL = """# 个人贷款申请材料

## 借款人信息
姓名：张伟
身份证号：330102199001011234
婚姻状况：已婚
配偶：刘芳
联系电话：13800138000

## 拟议贷款
贷款金额：30万元
贷款期限：36个月
年利率：4.2%
"""


@pytest.fixture
def app():
    created = create_app("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(created.state.database.engine)
    return created


def build_output(app, text: str, borrower_name: str) -> tuple[DocumentOutput, str]:
    parsed = parse_structured("material.md", io.BytesIO(text.encode()))
    with app.state.database.session() as db:
        user = User(username="owner", password_hash="hash", role="approval_officer")
        db.add(user)
        db.flush()
        application = Application(
            borrower_type="corporate",
            borrower_name=borrower_name,
            product="经营贷",
            application_date=date(2026, 8, 7),
            owner_id=user.id,
        )
        db.add(application)
        db.flush()
        document = Document(
            application_id=application.id,
            filename="material.md",
            extension=".md",
            declared_mime="text/markdown",
            size_bytes=len(text),
            sha256="a" * 64,
            object_key="material",
        )
        db.add(document)
        db.flush()
        output = store_parsed_output(db, document.id, parsed)
        run_candidate_extraction(db, document, output)
        db.commit()
        return output, application.id


def field_keys(app, output: DocumentOutput) -> dict[str, list[dict]]:
    with app.state.database.session() as db:
        facts = db.query(CandidateFact).filter_by(output_id=output.id).all()
        result: dict[str, list[dict]] = {}
        for fact in facts:
            result.setdefault(fact.field_key, []).append(
                {
                    "value": fact.typed_value.get("value"),
                    "raw_text": fact.raw_text,
                    "subject_role": fact.subject_role,
                    "extractor": fact.extractor,
                    "source_refs": fact.source_refs,
                }
            )
        return result


def test_corporate_material_extracts_identity_and_loan_fields(app) -> None:
    output, _ = build_output(app, CORPORATE_MATERIAL, "示例企业有限公司")
    keys = field_keys(app, output)
    assert keys["corporate_name"][0]["value"] == "示例企业有限公司"
    assert keys["corporate_name"][0]["subject_role"] == SubjectRole.PRIMARY_BORROWER
    assert keys["uscc"][0]["value"] == "91330100MA27XW1234"
    assert keys["legal_representative"][0]["value"] == "王小明"
    assert keys["legal_representative"][0]["subject_role"] == SubjectRole.LEGAL_REPRESENTATIVE
    assert keys["registered_capital"][0]["value"] == "50000000"
    assert keys["loan_amount"][0]["value"] == "8000000"
    assert keys["loan_term"][0]["value"] == "24"
    assert keys["loan_purpose"][0]["value"] == "补充流动资金"
    assert keys["proposed_signing_date"][0]["value"] == "2026-08-07"
    assert keys["interest_rate"][0]["value"] == "3.85"
    assert keys["repayment_method"][0]["value"] == "等额本息"


def test_credit_guarantee_and_collateral_fields(app) -> None:
    output, _ = build_output(app, CORPORATE_MATERIAL, "示例企业有限公司")
    keys = field_keys(app, output)
    assert keys["credit_report_date"][0]["value"] == "2026-07-15"
    assert keys["credit_balance"][0]["value"] == "2000000"
    assert keys["guarantor"][0]["value"] == "李四"
    assert keys["guarantor"][0]["subject_role"] == SubjectRole.GUARANTOR
    assert keys["guarantee_method"][0]["value"] == "连带责任保证"
    assert keys["collateral_value"][0]["value"] == "15000000"


def test_financial_and_transaction_rows_keep_row_level_detail(app) -> None:
    output, _ = build_output(app, CORPORATE_MATERIAL, "示例企业有限公司")
    keys = field_keys(app, output)
    financial = sorted(keys.get("financial_statement_item", []), key=lambda item: item["value"])
    assert [item["value"] for item in financial] == ["净利润", "营业成本", "营业收入"]
    assert any(item["raw_text"].startswith("营业收入") for item in financial)
    transaction = sorted(keys.get("transaction_item", []), key=lambda item: item["value"])
    assert [item["value"] for item in transaction] == ["杭州某贸易公司", "某供应商"]


def test_individual_material_extracts_name_and_id_card(app) -> None:
    output, _ = build_output(app, INDIVIDUAL_MATERIAL, "张伟")
    keys = field_keys(app, output)
    assert keys["personal_name"][0]["value"] == "张伟"
    assert keys["personal_name"][0]["subject_role"] == SubjectRole.PRIMARY_BORROWER
    assert keys["id_card"][0]["value"] == "330102199001011234"
    assert keys["id_card"][0]["subject_role"] == SubjectRole.PRIMARY_BORROWER
    assert keys["loan_amount"][0]["value"] == "300000"
    assert keys["loan_term"][0]["value"] == "36"
    assert keys["interest_rate"][0]["value"] == "4.2"


def test_monthly_rate_label_preserves_period_and_annualizes(app) -> None:
    output, _ = build_output(app, "# 材料\n\n月利率：1.2%\n", "示例企业")
    keys = field_keys(app, output)
    rate = keys["interest_rate"][0]
    assert rate["value"] == "14.4"
    assert rate["raw_text"] == "1.2%"


def test_key_value_table_rate_keeps_period_from_label(app) -> None:
    table = "# 材料\n\n| 项目 | 数值 |\n| --- | --- |\n| 月利率 | 0.5% |\n"
    output, _ = build_output(app, table, "示例企业")
    keys = field_keys(app, output)
    assert keys["interest_rate"][0]["value"] == "6.0"


def test_key_value_table_loan_fees_label_matches_paragraph_rule(app) -> None:
    # ``必要费用``/``各项费用``/``贷款费用`` must behave the same in a
    # key-value table row as in a paragraph (golden corpus runs XLSX/CSV).
    table = (
        "# 材料\n\n| 项目 | 数值 |\n| --- | --- |"
        "\n| 必要费用 | 评估费、公证费 |\n| 各项费用 | 服务费 |\n| 贷款费用 | 担保费 |\n"
    )
    output, _ = build_output(app, table, "示例企业")
    keys = field_keys(app, output)
    assert {candidate["value"] for candidate in keys["loan_fees"]} == {
        "评估费、公证费",
        "服务费",
        "担保费",
    }


def test_extracts_marital_status_shareholder_interest_method_and_query_count(app) -> None:
    material = """# 材料

婚姻状况：已婚
股东：陈明
计息方式：按月计息
查询次数：3次
"""
    output, _ = build_output(app, material, "示例企业")
    keys = field_keys(app, output)
    assert keys["marital_status"][0]["value"] == "已婚"
    assert keys["shareholder"][0]["value"] == "陈明"
    assert keys["shareholder"][0]["subject_role"] == SubjectRole.SHAREHOLDER
    assert keys["interest_method"][0]["value"] == "按月计息"
    assert keys["credit_query_count"][0]["value"] == "3"


def test_candidates_carry_source_refs_and_extractor_version(app) -> None:
    output, _ = build_output(app, CORPORATE_MATERIAL, "示例企业有限公司")
    keys = field_keys(app, output)
    uscc = keys["uscc"][0]
    assert uscc["extractor"] == "local_rule"
    ref = uscc["source_refs"][0]
    assert ref["output_id"] == output.id
    assert ref["document_id"] == output.document_id
    assert ref["output_version"] == 1
    assert ref["locator"]["kind"] == "markdown"
    assert ref["locator"]["line_start"] >= 1


def test_subject_role_labels_are_defined_for_all_roles() -> None:
    for role in SubjectRole:
        assert SUBJECT_LABELS[role]
    assert SUBJECT_LABELS[SubjectRole.PRIMARY_BORROWER] == "主借款人"
