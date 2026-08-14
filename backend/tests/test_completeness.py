"""Pure completeness evaluator: every demo item and condition is exercised.

The evaluator is deliberately free of database and HTTP concerns; these tests
drive it table-driven across all items of both demo templates and assert the
five documented states plus the "unconfirmed evidence never satisfies" rule.
"""

from app.classification import MaterialCategory
from app.completeness import (
    DEMO_CORP_OPERATING,
    DEMO_INDIVIDUAL_OPERATING,
    DEMO_ITEMS,
    DEMO_TEMPLATE_SPECS,
    evaluate_item,
    evaluate_items,
    item_condition_met,
)
from app.models import ItemState

CONDITION_KEYS = {"collateral", "guarantor"}

NO_CONDITIONS = {key: False for key in CONDITION_KEYS}
ALL_CONDITIONS = {key: True for key in CONDITION_KEYS}


class Item:
    """Attribute-style item mirroring ChecklistItem for the pure evaluator."""

    def __init__(self, spec: dict) -> None:
        self.code = spec["code"]
        self.label = spec["label"]
        self.category = spec["category"]
        self.requires_seal = spec.get("requires_seal", False)
        self.requires_signature = spec.get("requires_signature", False)
        self.condition = spec.get("condition")


def items_for(code: str) -> list[Item]:
    return [Item(spec) for spec in DEMO_ITEMS[code]]


def all_demo_items() -> list[tuple[str, Item]]:
    return [
        (template_code, item)
        for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING)
        for item in items_for(template_code)
    ]


def demo_item_codes() -> list[str]:
    codes: list[str] = []
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        codes.extend(item["code"] for item in DEMO_ITEMS[template_code])
    return sorted(set(codes))


def run(
    items: list[dict],
    *,
    waivers: set[str] | None = None,
    condition_context: dict[str, bool] | None = None,
    mappings: dict[str, set[str]] | None = None,
    confirmed_category: dict[str, str] | None = None,
    classification_candidates: dict[str, set[str]] | None = None,
    seal_present: set[str] | None = None,
    signature_present: set[str] | None = None,
) -> dict[str, ItemState]:
    return evaluate_items(
        items,
        waivers=waivers or set(),
        condition_context=condition_context or NO_CONDITIONS,
        mappings=mappings or {},
        confirmed_category=confirmed_category or {},
        classification_candidates=classification_candidates or {},
        seal_present=seal_present or set(),
        signature_present=signature_present or set(),
    )


def test_demo_specs_are_well_formed() -> None:
    codes = {spec["code"] for spec in DEMO_TEMPLATE_SPECS}
    assert codes == {DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING}
    for spec in DEMO_TEMPLATE_SPECS:
        assert spec["demo_only"] is True
        assert spec["product"] == "经营贷"
        items = DEMO_ITEMS[spec["code"]]
        assert items, spec["code"]
        item_codes = [item["code"] for item in items]
        assert len(item_codes) == len(set(item_codes))
        for item in items:
            MaterialCategory(item["category"])
            condition = item.get("condition")
            if condition is not None:
                assert condition["requires"] in CONDITION_KEYS


def test_conditions_and_items_cover_each_other() -> None:
    conditional = {item.code for _, item in all_demo_items() if item.condition}
    unconditional = {item.code for _, item in all_demo_items() if not item.condition}
    assert conditional and unconditional
    assert conditional & unconditional == set()


def test_no_evidence_yields_missing_or_not_applicable() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        states = run(items_for(template_code))
        for item in items_for(template_code):
            expected = ItemState.NOT_APPLICABLE if item.condition else ItemState.MISSING
            assert states[item.code] == expected, (template_code, item.code)


def test_condition_context_activates_conditional_items() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        states = run(items_for(template_code), condition_context=ALL_CONDITIONS)
        for item in items_for(template_code):
            assert states[item.code] == ItemState.MISSING, (template_code, item.code)


def test_conditions_map_to_collateral_and_guarantor_only() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            if not item.condition:
                continue
            requires = item.condition["requires"]
            context = {key: key == requires for key in CONDITION_KEYS}
            assert (
                evaluate_item(
                    item,
                    waived=False,
                    condition_context=context,
                    mapped_documents=set(),
                    confirmed_category={},
                    classification_candidates={},
                    seal_present=set(),
                    signature_present=set(),
                )
                == ItemState.MISSING
            ), (template_code, item.code, requires)


def test_mapping_without_seal_signature_requirement_satisfies() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            if item.requires_seal or item.requires_signature:
                continue
            states = run(
                items_for(template_code),
                condition_context=ALL_CONDITIONS,
                mappings={item.code: {"doc-1"}},
            )
            assert states[item.code] == ItemState.SATISFIED, (
                template_code,
                item.code,
            )


def test_mapping_with_unconfirmed_seal_or_signature_pends() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            if not (item.requires_seal or item.requires_signature):
                continue
            states = run(
                items_for(template_code),
                condition_context=ALL_CONDITIONS,
                mappings={item.code: {"doc-1"}},
            )
            assert states[item.code] == ItemState.PENDING_CONFIRMATION, (
                template_code,
                item.code,
            )


def test_confirmed_seal_and_signature_satisfy_required_items() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            if not (item.requires_seal or item.requires_signature):
                continue
            states = run(
                items_for(template_code),
                condition_context=ALL_CONDITIONS,
                mappings={item.code: {"doc-1"}},
                seal_present={"doc-1"} if item.requires_seal else set(),
                signature_present={"doc-1"} if item.requires_signature else set(),
            )
            assert states[item.code] == ItemState.SATISFIED, (
                template_code,
                item.code,
            )


def test_waiver_overrides_everything() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            states = run(
                items_for(template_code),
                waivers={item.code},
                condition_context=ALL_CONDITIONS,
                mappings={item.code: {"doc-1"}},
            )
            assert states[item.code] == ItemState.MANUALLY_WAIVED, (
                template_code,
                item.code,
            )


def test_unconfirmed_classification_never_satisfies() -> None:
    # A classification candidate alone (no confirmed category, no mapping) must
    # pend; it must never become satisfied.
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            if item.condition:
                continue
            states = run(
                items_for(template_code),
                classification_candidates={"doc-1": {item.category}},
            )
            assert states[item.code] == ItemState.PENDING_CONFIRMATION, (
                template_code,
                item.code,
            )


def test_confirmed_category_without_mapping_pends() -> None:
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            if item.condition:
                continue
            states = run(
                items_for(template_code),
                confirmed_category={"doc-1": item.category},
            )
            assert states[item.code] == ItemState.PENDING_CONFIRMATION, (
                template_code,
                item.code,
            )


def test_every_demo_item_reaches_every_reachable_state() -> None:
    """100% demo-item coverage: each item is asserted in satisfied and waived states."""
    covered: set[str] = set()
    for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING):
        for item in items_for(template_code):
            inputs = {
                "condition_context": ALL_CONDITIONS,
                "mappings": {item.code: {"doc-1"}},
                "seal_present": {"doc-1"} if item.requires_seal else set(),
                "signature_present": {"doc-1"} if item.requires_signature else set(),
            }
            satisfied = run(items_for(template_code), **inputs)
            assert satisfied[item.code] == ItemState.SATISFIED, (template_code, item.code)
            waived = run(items_for(template_code), waivers={item.code}, **inputs)
            assert waived[item.code] == ItemState.MANUALLY_WAIVED, (template_code, item.code)
            covered.add(f"{template_code}:{item.code}")
    expected = {
        f"{template_code}:{item.code}"
        for template_code in (DEMO_CORP_OPERATING, DEMO_INDIVIDUAL_OPERATING)
        for item in items_for(template_code)
    }
    assert covered == expected


def test_item_condition_met_is_table_driven() -> None:
    cases = [
        (None, {}, True),
        ({"requires": "collateral"}, {"collateral": True}, True),
        ({"requires": "collateral"}, {"collateral": False}, False),
        ({"requires": "guarantor"}, {"guarantor": True}, True),
        ({"requires": "unknown"}, {"unknown": True}, False),
    ]
    for condition, context, expected in cases:
        item = Item({"code": "x", "label": "x", "category": "basic_info", "condition": condition})
        assert item_condition_met(item, context) is expected, (condition, context)
