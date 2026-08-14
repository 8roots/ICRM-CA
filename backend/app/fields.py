"""P0 typed field whitelist and subject roles.

The whitelist is the single source of truth for which fields extraction may
produce candidates for, how their values are normalized, and whether they are
eligible for the cloud (DeepSeek) path. ``critical`` fields are never
auto-confirmed; high confidence only affects review ordering.
"""

from dataclasses import dataclass
from enum import StrEnum


class SubjectRole(StrEnum):
    PRIMARY_BORROWER = "primary_borrower"
    LEGAL_REPRESENTATIVE = "legal_representative"
    SHAREHOLDER = "shareholder"
    SPOUSE = "spouse"
    GUARANTOR = "guarantor"
    COLLATERAL_OWNER = "collateral_owner"


SUBJECT_LABELS = {
    SubjectRole.PRIMARY_BORROWER: "主借款人",
    SubjectRole.LEGAL_REPRESENTATIVE: "法定代表人",
    SubjectRole.SHAREHOLDER: "股东",
    SubjectRole.SPOUSE: "配偶",
    SubjectRole.GUARANTOR: "保证人",
    SubjectRole.COLLATERAL_OWNER: "抵押物权利人",
}


class FieldGroup(StrEnum):
    APPLICATION = "application"
    PROPOSED_LOAN = "proposed_loan"
    IDENTITY = "identity"
    FINANCIAL = "financial"
    TRANSACTION = "transaction"
    CREDIT = "credit"
    COLLATERAL = "collateral"
    EVIDENCE = "evidence"


GROUP_LABELS = {
    FieldGroup.APPLICATION: "申请",
    FieldGroup.PROPOSED_LOAN: "拟议贷款",
    FieldGroup.IDENTITY: "主体身份",
    FieldGroup.FINANCIAL: "财务报表",
    FieldGroup.TRANSACTION: "银行流水",
    FieldGroup.CREDIT: "征信",
    FieldGroup.COLLATERAL: "抵押担保",
    FieldGroup.EVIDENCE: "证据元数据",
}


class ValueType(StrEnum):
    TEXT = "text"
    DECIMAL = "decimal"
    INTEGER = "integer"
    DATE = "date"
    AMOUNT = "amount"
    RATE = "rate"
    ROW = "row"


@dataclass(frozen=True)
class FieldDef:
    key: str
    group: FieldGroup
    label: str
    value_type: ValueType
    keywords: tuple[str, ...]
    critical: bool = False
    cloud_extractable: bool = False
    default_subject: SubjectRole | None = None


FIELDS: dict[str, FieldDef] = {
    # 申请/证据元数据两组字段由 Application / Document 实体承载（申请编号、产品、
    # 申请日期、材料文件名、哈希等），不通过材料文本抽取，因此白名单不含这两组。
    # 拟议贷款
    "loan_amount": FieldDef(
        "loan_amount",
        FieldGroup.PROPOSED_LOAN,
        "贷款金额",
        ValueType.AMOUNT,
        ("贷款金额", "拟贷款金额", "借款金额", "融资金额", "申请金额"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "loan_term": FieldDef(
        "loan_term",
        FieldGroup.PROPOSED_LOAN,
        "贷款期限",
        ValueType.INTEGER,
        ("贷款期限", "借款期限", "期限"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "loan_purpose": FieldDef(
        "loan_purpose",
        FieldGroup.PROPOSED_LOAN,
        "贷款用途",
        ValueType.TEXT,
        ("贷款用途", "借款用途", "资金用途", "用途"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "proposed_signing_date": FieldDef(
        "proposed_signing_date",
        FieldGroup.PROPOSED_LOAN,
        "拟签约日期",
        ValueType.DATE,
        ("拟签约日期", "签约日期"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "interest_rate": FieldDef(
        "interest_rate",
        FieldGroup.PROPOSED_LOAN,
        "利率",
        ValueType.RATE,
        ("年利率", "月利率", "名义利率", "执行利率", "贷款利率", "利率"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "repayment_method": FieldDef(
        "repayment_method",
        FieldGroup.PROPOSED_LOAN,
        "还款方式",
        ValueType.TEXT,
        ("还款方式", "还款计划", "还本付息"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "interest_method": FieldDef(
        "interest_method",
        FieldGroup.PROPOSED_LOAN,
        "计息方式",
        ValueType.TEXT,
        ("计息方式", "计息"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "loan_fees": FieldDef(
        "loan_fees",
        FieldGroup.PROPOSED_LOAN,
        "必要费用",
        ValueType.TEXT,
        ("必要费用", "各项费用", "贷款费用"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "overdue_interest_rate": FieldDef(
        "overdue_interest_rate",
        FieldGroup.PROPOSED_LOAN,
        "罚息利率",
        ValueType.RATE,
        ("罚息利率", "逾期利率", "逾期罚息", "罚息"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    # 主体身份
    "corporate_name": FieldDef(
        "corporate_name",
        FieldGroup.IDENTITY,
        "企业名称",
        ValueType.TEXT,
        ("企业名称", "公司名称", "名称"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "uscc": FieldDef(
        "uscc",
        FieldGroup.IDENTITY,
        "统一社会信用代码",
        ValueType.TEXT,
        ("统一社会信用代码", "信用代码", "社会信用代码"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "legal_representative": FieldDef(
        "legal_representative",
        FieldGroup.IDENTITY,
        "法定代表人",
        ValueType.TEXT,
        ("法定代表人", "法人代表"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.LEGAL_REPRESENTATIVE,
    ),
    "registered_capital": FieldDef(
        "registered_capital",
        FieldGroup.IDENTITY,
        "注册资本",
        ValueType.AMOUNT,
        ("注册资本", "注册资金"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "personal_name": FieldDef(
        "personal_name",
        FieldGroup.IDENTITY,
        "姓名",
        ValueType.TEXT,
        ("姓名", "借款人", "申请人"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "marital_status": FieldDef(
        "marital_status",
        FieldGroup.IDENTITY,
        "婚姻状况",
        ValueType.TEXT,
        ("婚姻状况", "婚姻状态"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "shareholder": FieldDef(
        "shareholder",
        FieldGroup.IDENTITY,
        "股东",
        ValueType.TEXT,
        ("股东", "出资人"),
        cloud_extractable=True,
        default_subject=SubjectRole.SHAREHOLDER,
    ),
    "id_card": FieldDef(
        "id_card",
        FieldGroup.IDENTITY,
        "证件号码",
        ValueType.TEXT,
        ("身份证号", "证件号码", "身份证号码"),
        critical=True,
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    # 财务
    "financial_statement_item": FieldDef(
        "financial_statement_item",
        FieldGroup.FINANCIAL,
        "财务报表科目",
        ValueType.ROW,
        ("科目", "报表", "利润表", "资产负债表", "现金流量表"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    # 流水
    "transaction_item": FieldDef(
        "transaction_item",
        FieldGroup.TRANSACTION,
        "流水明细行",
        ValueType.ROW,
        ("交易日期", "交易对手", "对方户名", "摘要", "流水"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    # 征信
    "credit_report_date": FieldDef(
        "credit_report_date",
        FieldGroup.CREDIT,
        "征信报告日期",
        ValueType.DATE,
        ("报告日期", "查询日期", "征信报告"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "credit_line": FieldDef(
        "credit_line",
        FieldGroup.CREDIT,
        "授信额度",
        ValueType.AMOUNT,
        ("授信额度", "授信总额", "授信余额"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "credit_balance": FieldDef(
        "credit_balance",
        FieldGroup.CREDIT,
        "负债余额",
        ValueType.AMOUNT,
        ("负债余额", "贷款余额", "未结清余额"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "overdue_amount": FieldDef(
        "overdue_amount",
        FieldGroup.CREDIT,
        "当前逾期金额",
        ValueType.AMOUNT,
        ("逾期金额", "当前逾期"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    "credit_query_count": FieldDef(
        "credit_query_count",
        FieldGroup.CREDIT,
        "征信查询次数",
        ValueType.INTEGER,
        ("查询次数", "查询记录"),
        cloud_extractable=True,
        default_subject=SubjectRole.PRIMARY_BORROWER,
    ),
    # 抵押担保
    "collateral_type": FieldDef(
        "collateral_type",
        FieldGroup.COLLATERAL,
        "抵押物",
        ValueType.TEXT,
        ("抵押物", "抵押财产", "担保物"),
        cloud_extractable=True,
    ),
    "collateral_certificate": FieldDef(
        "collateral_certificate",
        FieldGroup.COLLATERAL,
        "权证号",
        ValueType.TEXT,
        ("权证号", "权证", "不动产权证"),
        cloud_extractable=True,
    ),
    "collateral_value": FieldDef(
        "collateral_value",
        FieldGroup.COLLATERAL,
        "抵押物评估价值",
        ValueType.AMOUNT,
        ("评估价值", "评估值", "评估价"),
        cloud_extractable=True,
    ),
    "guarantor": FieldDef(
        "guarantor",
        FieldGroup.COLLATERAL,
        "保证人",
        ValueType.TEXT,
        ("保证人", "担保人"),
        cloud_extractable=True,
        default_subject=SubjectRole.GUARANTOR,
    ),
    "guarantee_method": FieldDef(
        "guarantee_method",
        FieldGroup.COLLATERAL,
        "保证方式",
        ValueType.TEXT,
        ("保证方式", "连带责任保证", "一般保证"),
        cloud_extractable=True,
    ),
}


def field_def(field_key: str) -> FieldDef:
    try:
        return FIELDS[field_key]
    except KeyError:
        raise ValueError(f"unknown field key: {field_key}") from None


def cloud_targets() -> dict[str, FieldDef]:
    return {key: definition for key, definition in FIELDS.items() if definition.cloud_extractable}
