"""Fail-closed redaction: identifiers absent from output, stable aliases per app."""

import pytest

from app.redaction import (
    ID_CARD_RE,
    RedactionError,
    alias_map,
    redact,
    verify_redaction,
)

MATERIAL = (
    "借款人：张三，身份证号：330102199001011234，联系电话：13800138000。\n"
    "企业名称：示例企业，统一社会信用代码：91330100MA27XW1234。\n"
    "开户账号：6222021234567890123。邮箱 zhang.san@example.com。"
)


def test_redaction_masks_every_direct_identifier() -> None:
    aliases = alias_map("app-1", ["张三"], [MATERIAL])
    result = redact("app-1", MATERIAL, aliases)
    verify_redaction(result.text)
    assert "330102199001011234" not in result.text
    assert "13800138000" not in result.text
    assert "91330100MA27XW1234" not in result.text
    assert "6222021234567890123" not in result.text
    assert "zhang.san@example.com" not in result.text
    assert "张三" not in result.text
    assert "示例企业" in result.text  # amounts/business text survives


def test_redaction_keeps_amounts_rates_and_dates() -> None:
    text = "贷款金额：500万元，年利率3.85%，拟签约日期2026年8月7日"
    result = redact("app-1", text, alias_map("app-1", [], [text]))
    assert "500万元" in result.text
    assert "3.85%" in result.text
    assert "2026年8月7日" in result.text


def test_aliases_are_stable_per_application() -> None:
    first = alias_map("app-1", ["张三"], [MATERIAL])
    second = alias_map("app-1", ["张三"], [MATERIAL])
    assert first == second
    result_a = redact("app-1", MATERIAL, first)
    result_b = redact("app-1", MATERIAL, second)
    assert result_a.text == result_b.text
    # A different application gets different aliases for the same identifiers.
    other = alias_map("app-2", ["张三"], [MATERIAL])
    assert other != first
    assert "张三" in first


def test_redacted_text_never_contains_the_alias_mapping() -> None:
    aliases = alias_map("app-1", ["张三"], [MATERIAL])
    result = redact("app-1", MATERIAL, aliases)
    # The alias map (identifier -> alias) itself must not be embedded in output.
    for identifier, alias in aliases.items():
        assert f"{identifier}:{alias}" not in result.text
    assert "alias" not in result.text.lower()


def test_verify_redaction_fails_closed_on_remaining_identifier() -> None:
    with pytest.raises(RedactionError):
        verify_redaction("身份证号：330102199001011234 仍在文本中")


def test_short_name_does_not_corrupt_longer_text() -> None:
    text = "华信银行发放贷款给华信集团"
    aliases = alias_map("app-1", ["华信"], [text])
    result = redact("app-1", text, aliases)
    # The name appears only as a standalone token; longer words survive.
    assert "华信集团" in result.text


def test_id_card_pattern_matches_standalone_numbers_only() -> None:
    assert ID_CARD_RE.search("证件号330102199001011234")
    assert not ID_CARD_RE.search("金额3301021990010112345")
