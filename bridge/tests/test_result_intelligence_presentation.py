from __future__ import annotations

import pytest

from app.working_context import ResultIntelligence, format_money, make_context_object


def _document(text: str, *, currency: str | None = None) -> dict[str, object]:
    document: dict[str, object] = {
        "filename": "statement.pdf",
        "text_chunks": [{"page": 1, "chunk_index": 1, "text": text}],
    }
    if currency:
        document["currency"] = currency
    return document


def _selection(
    *,
    label: str,
    value: str,
    quote: str,
    semantic_field: str,
    value_kind: str,
) -> dict[str, object]:
    return {
        "label": label,
        "value": value,
        "evidence_quote": quote,
        "semantic_field": semantic_field,
        "value_kind": value_kind,
    }


def test_positive_mailbox_result_dominates_bounded_provider_miss() -> None:
    response = ResultIntelligence.mailbox_topic_search(
        topic="wage slip",
        matches=[
            {
                "provider_name": "Outlook",
                "subject": "WAGE SLIP",
                "sender": "Joseph Scott",
                "date": "24 September",
                "count": 7,
            }
        ],
        no_matches=[{"provider_name": "Gmail", "bounded": True, "checked": 25}],
    )

    assert response == (
        "Yes — I found your latest wage slip in Outlook. "
        "It’s from Joseph Scott, dated 24 September."
    )
    assert "25 recent Gmail" not in response
    assert "7 matching messages" not in response


def test_all_provider_misses_preserve_bounded_scope() -> None:
    response = ResultIntelligence.mailbox_topic_search(
        topic="statement",
        matches=[],
        no_matches=[
            {"provider_name": "Gmail", "bounded": True, "checked": 25},
            {"provider_name": "Outlook", "bounded": False, "checked": 0},
        ],
    )

    assert "25 recent Gmail Inbox messages I checked" in response
    assert "I didn’t find one in Outlook" in response


def test_gbp_document_money_uses_natural_field_presentations() -> None:
    document = _document("NET PAY £2,544.76\nIncome Tax £415.60\nTotal Gross Pay £3,126.65")
    net = ResultIntelligence.document_fact(
        question="How much did I get?",
        selection=_selection(
            label="NET PAY",
            value="£2,544.76",
            quote="NET PAY £2,544.76",
            semantic_field="net_pay",
            value_kind="money",
        ),
        document=document,
    )
    tax = ResultIntelligence.document_fact(
        question="How much tax did I pay?",
        selection=_selection(
            label="Income Tax",
            value="£415.60",
            quote="Income Tax £415.60",
            semantic_field="income_tax",
            value_kind="money",
        ),
        document=document,
    )
    gross = ResultIntelligence.document_fact(
        question="What was my gross pay?",
        selection=_selection(
            label="Total Gross Pay",
            value="£3,126.65",
            quote="Total Gross Pay £3,126.65",
            semantic_field="gross_pay",
            value_kind="money",
        ),
        document=document,
    )

    assert net["response"] == "Your net pay was £2,544.76."
    assert tax["response"] == "You paid £415.60 in Income Tax."
    assert gross["response"] == "Your gross pay was £3,126.65."
    assert net["money"] == {"amount": "2544.76", "currency": "GBP"}


def test_unknown_currency_is_not_invented() -> None:
    selection = _selection(
        label="NET PAY",
        value="2544.76",
        quote="NET PAY 2544.76",
        semantic_field="net_pay",
        value_kind="money",
    )
    # Even a model-supplied currency cannot restore a symbol absent from the
    # grounded evidence or trusted document metadata.
    selection["currency"] = "GBP"
    fact = ResultIntelligence.document_fact(
        question="How much did I get?",
        selection=selection,
        document=_document("NET PAY 2544.76"),
    )

    assert fact["response"] == "Your net pay was 2,544.76."
    assert fact["money"] == {"amount": "2544.76", "currency": None}
    assert all(symbol not in str(fact["response"]) for symbol in "£$€")
    assert fact["currency_evidence"]["source"] == "unknown"
    assert fact["scalar"]["currency"] is None


def test_page_extraction_currency_evidence_survives_split_glyph_text() -> None:
    document = _document("NET PAY 2,544.76")
    document["currency_evidence"] = {
        "version": 1,
        "currency": "GBP",
        "currencies": ["GBP"],
        "source": "document",
        "verified": True,
        "ambiguous": False,
        "page_currencies": {"1": ["GBP"]},
        "extraction_modes": ["layout"],
    }
    fact = ResultIntelligence.document_fact(
        question="How much did I get?",
        selection=_selection(
            label="NET PAY",
            value="2,544.76",
            quote="NET PAY 2,544.76",
            semantic_field="net_pay",
            value_kind="money",
        ),
        document=document,
    )

    assert fact["response"] == "Your net pay was £2,544.76."
    assert fact["currency_evidence"]["source"] == "page"
    assert fact["scalar"]["currency"] == "GBP"


def test_document_currency_conflict_stays_unknown_but_exact_field_wins() -> None:
    document = _document("Currency GBP\nAmount 100.00\nTravel reimbursement USD 100.00")
    document["currency_evidence"] = {
        "version": 1,
        "currency": None,
        "currencies": ["GBP", "USD"],
        "source": "ambiguous",
        "verified": False,
        "ambiguous": True,
        "page_currencies": {"1": ["GBP", "USD"]},
    }
    ambiguous = ResultIntelligence.document_fact(
        question="What was the amount?",
        selection=_selection(
            label="Amount",
            value="100.00",
            quote="Amount 100.00",
            semantic_field="monetary_amount",
            value_kind="money",
        ),
        document=document,
    )
    exact = ResultIntelligence.document_fact(
        question="What was the reimbursement?",
        selection=_selection(
            label="Travel reimbursement",
            value="USD 100.00",
            quote="Travel reimbursement USD 100.00",
            semantic_field="monetary_amount",
            value_kind="money",
        ),
        document=document,
    )

    assert ambiguous["currency_evidence"]["ambiguous"] is True
    assert ambiguous["money"]["currency"] is None
    assert exact["money"]["currency"] == "USD"
    assert exact["currency_evidence"]["source"] == "field"


def test_usd_and_eur_are_grounded_from_local_evidence() -> None:
    usd = ResultIntelligence.document_fact(
        question="What was the net pay?",
        selection=_selection(
            label="Net Pay",
            value="$2,100.00",
            quote="Net Pay $2,100.00",
            semantic_field="net_pay",
            value_kind="money",
        ),
        document=_document("Net Pay $2,100.00"),
    )
    eur = ResultIntelligence.document_fact(
        question="What was the net pay?",
        selection=_selection(
            label="Net Pay",
            value="EUR 2100.00",
            quote="Net Pay EUR 2100.00",
            semantic_field="net_pay",
            value_kind="money",
        ),
        document=_document("Net Pay EUR 2100.00"),
    )

    assert usd["response"] == "Your net pay was $2,100.00."
    assert eur["response"] == "Your net pay was €2,100.00."


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    (
        ("2544.76", "GBP", "£2,544.76"),
        ("1000", "GBP", "£1,000.00"),
        ("-125.5", "GBP", "-£125.50"),
        ("0", "GBP", "£0.00"),
        ("2100", "USD", "$2,100.00"),
        ("2100", "EUR", "€2,100.00"),
        ("2544.76", None, "2,544.76"),
    ),
)
def test_money_formatter_handles_sign_precision_and_supported_currencies(
    amount: str, currency: str | None, expected: str
) -> None:
    assert format_money(amount, currency) == expected


def test_non_money_measurement_does_not_gain_currency_formatting() -> None:
    fact = ResultIntelligence.document_fact(
        question="How many overtime hours?",
        selection=_selection(
            label="Overtime Hours",
            value="6.5",
            quote="Overtime Hours 6.5",
            semantic_field="measurement",
            value_kind="number",
        ),
        document=_document("Overtime Hours 6.5"),
    )

    assert fact["response"] == "Overtime Hours was 6.5 hours."
    assert all(symbol not in str(fact["response"]) for symbol in "£$€")


@pytest.mark.parametrize(
    ("label", "value", "quote", "expected", "unit"),
    (
        ("Efficiency", "87", "Efficiency 87%", "Efficiency was 87%.", "%"),
        ("Energy Used", "12.5", "Energy Used 12.5 kWh", "Energy Used was 12.5 kWh.", "kWh"),
        ("Temperature", "21.5", "Temperature 21.5 °C", "Temperature was 21.5°C.", "°C"),
        ("Distance", "3.2", "Distance 3.2 miles", "Distance was 3.2 miles.", "miles"),
    ),
)
def test_grounded_scalar_preserves_generic_measurement_units(
    label: str,
    value: str,
    quote: str,
    expected: str,
    unit: str,
) -> None:
    fact = ResultIntelligence.document_fact(
        question=f"What was the {label}?",
        selection=_selection(
            label=label,
            value=value,
            quote=quote,
            semantic_field="measurement",
            value_kind="number",
        ),
        document=_document(quote),
    )

    assert fact["response"] == expected
    assert fact["scalar"]["value"] == value
    assert fact["scalar"]["display_value"] in expected
    assert fact["scalar"]["unit"] == unit
    assert fact["scalar"]["currency"] is None


def _comparison(currency_a: str | None, currency_b: str | None) -> tuple[dict, list]:
    current = make_context_object(
        object_type="document",
        display_name="Current statement",
        source="test",
        canonical_id="current",
        immutable=True,
    )
    previous = make_context_object(
        object_type="document",
        display_name="Previous statement",
        source="test",
        canonical_id="previous",
        immutable=True,
    )
    derived = {
        "values": [
            {
                "reference_id": current.reference_id,
                "value": 2544.76,
                "unit": currency_a,
                "value_kind": "money",
            },
            {
                "reference_id": previous.reference_id,
                "value": 2400.00,
                "unit": currency_b,
                "value_kind": "money",
            },
        ]
    }
    return derived, [current, previous]


def test_money_comparison_formats_same_currency_difference() -> None:
    derived, objects = _comparison("GBP", "GBP")
    assert ResultIntelligence.comparison(derived, objects) == "£144.76 more."


def test_money_comparison_rejects_incompatible_currencies() -> None:
    derived, objects = _comparison("GBP", "USD")
    response = ResultIntelligence.comparison(derived, objects)
    assert "can’t compare" in response
    assert "currencies differ" in response
    assert "144.76" not in response


def test_money_comparison_rejects_two_unverified_currencies() -> None:
    derived, objects = _comparison(None, None)
    response = ResultIntelligence.comparison(derived, objects)
    assert "one currency is unverified" in response
    assert "144.76" not in response
