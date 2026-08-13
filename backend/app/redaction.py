"""Fail-closed redaction with stable per-application aliases.

Direct identifiers (ID card numbers, phone numbers, bank accounts, emails,
credit codes, and the borrower's or related subjects' names) are replaced with
stable aliases that are deterministic per application, so the same entity keeps
the same alias across slices and reruns. The alias map itself never leaves the
intranet: only the redacted text is sent to the cloud, and verification
re-scans the redacted text and aborts the call when any identifier pattern
still matches.
"""

import hashlib
import re
from dataclasses import dataclass

REDACTION_VERSION = "icrm-redaction-1"

ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
PHONE_RE = re.compile(
    r"(?:电话|手机|联系电话|联系方式|号码|联系人|Tel|Phone|Mobile)\s*[:：]?\s*(1[3-9]\d{9})"
)
PHONE_STANDALONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)(?!\s*(?:元|万|亿))")
ACCOUNT_RE = re.compile(r"(?:账号|账户|卡号|帐号|银行账号|对公账号)\s*[:：]?\s*(\d{12,19})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
USCC_RE = re.compile(r"(?<![0-9A-Za-z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Za-z])")

IDENTIFIER_PATTERNS = (
    ID_CARD_RE,
    PHONE_RE,
    PHONE_STANDALONE_RE,
    ACCOUNT_RE,
    EMAIL_RE,
    USCC_RE,
)

ALIAS_CATEGORIES = (
    ("身份证", ID_CARD_RE),
    ("电话", PHONE_RE),
    ("电话", PHONE_STANDALONE_RE),
    ("账号", ACCOUNT_RE),
    ("邮箱", EMAIL_RE),
    ("信用代码", USCC_RE),
)

# Masked names must be surrounded by non-identifier characters so a short
# Chinese name never corrupts longer text it happens to be a prefix of.
NAME_GUARD = r"(?<![0-9A-Za-z\u4e00-\u9fa5])"


class RedactionError(Exception):
    """Raised when redaction cannot guarantee that no identifier remains."""


@dataclass(frozen=True)
class RedactionResult:
    text: str
    alias_map: dict[str, str]
    masked_count: int


def _stable_alias(application_id: str, category: str, identifier: str, index: int) -> str:
    digest = hashlib.sha256(f"{application_id}\0{category}\0{identifier}".encode()).hexdigest()[:8]
    return f"[{category}{index}-{digest}]"


def alias_map(
    application_id: str,
    known_names: list[str] | None = None,
    extra_texts: list[str] | None = None,
) -> dict[str, str]:
    """Build the deterministic per-application alias map.

    ``known_names`` are names discovered by extraction (borrower, legal
    representative, guarantors, ...); ``extra_texts`` are scanned for pattern
    identifiers (ID cards, phones, accounts, emails, credit codes) so every
    match gets a stable alias.
    """
    identifiers: dict[str, str] = {}
    for name in known_names or []:
        name = name.strip()
        if name:
            identifiers[name] = "主体"
    for text in extra_texts or []:
        for category, pattern in ALIAS_CATEGORIES:
            for match in pattern.findall(text):
                identifiers.setdefault(match, category)
    aliases: dict[str, str] = {}
    counters: dict[str, int] = {}
    for identifier in sorted(identifiers):
        category = identifiers[identifier]
        counters[category] = counters.get(category, 0) + 1
        aliases[identifier] = _stable_alias(
            application_id, category, identifier, counters[category]
        )
    return aliases


def redact(application_id: str, text: str, aliases: dict[str, str]) -> RedactionResult:
    """Replace every direct identifier in ``text`` with its stable alias.

    Identifiers already in the alias map are replaced with their stable alias;
    any pattern identifier not yet mapped is aliased on the fly so verification
    never fails on content redaction can in fact handle.
    """
    masked = _mask_known(text, aliases)
    masked, extra = _mask_patterns(application_id, masked, aliases)
    return RedactionResult(text=masked, alias_map=aliases, masked_count=extra)


def _mask_known(text: str, aliases: dict[str, str]) -> str:
    for identifier, alias in aliases.items():
        guarded = NAME_GUARD + re.escape(identifier) + r"(?![0-9A-Za-z\u4e00-\u9fa5])"
        text = re.sub(guarded, alias, text)
    return text


def _mask_patterns(application_id: str, text: str, aliases: dict[str, str]) -> tuple[str, int]:
    masked = 0
    for category, pattern in ALIAS_CATEGORIES:
        def replace(match: re.Match) -> str:
            nonlocal masked
            identifier = match.group(0)
            index = (
                sum(1 for value in aliases.values() if value.startswith(f"[{category}")) + 1
            )
            alias = _stable_alias(application_id, category, identifier, index)
            aliases[identifier] = alias
            masked += 1
            return alias

        text = pattern.sub(replace, text)
    return text, masked


def verify_redaction(text: str) -> None:
    """Fail closed: raise when any identifier pattern survives redaction."""
    for pattern in IDENTIFIER_PATTERNS:
        if pattern.search(text):
            raise RedactionError("unredacted_identifier")
