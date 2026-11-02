"""Unit tests for dashboard.explain (SYNTHETIC rows)."""

from __future__ import annotations

import json

from test_dash_synthetic import synthetic_trade

from trading_platform.dashboard.explain import NOT_RECORDED, explain_trade


def test_full_row_has_opened_and_closed_from_recorded_fields() -> None:
    exp = explain_trade(synthetic_trade(1))
    o, c = exp["opened"], exp["closed"]
    assert o["title"] == "WHY THE TRADE OPENED" and c["title"] == "WHY THE TRADE CLOSED"
    assert o["strategy"] == "ORB"
    assert o["entry_conditions"]["satisfied"] == ["close_above_PDH", "body_ratio_ok"]
    assert o["entry_conditions"]["failed"] == ["volume_filter"]
    assert o["previous_day_levels"] == {"PDH": 25000.0, "PDL": 24900.0}
    assert o["levels"]["other"] == {"support_zone": [24950.0, 24960.0]}
    assert o["trigger_candle"]["c"] == 25005.0
    assert o["contract"]["symbol"] == "NIFTY CE SYNTH"
    assert c["exit_condition"] == "underlying reached target 25065.0"
    assert c["underlying_at_trigger"] == 25065.0
    assert c["original_stop"] == 24975.0 and c["original_target"] == 25065.0
    assert c["option_exit_bid"] == 150.0 and c["total_charges"] == 40.0
    assert c["net_pnl"] == 500.0


def test_missing_fields_are_not_recorded_never_guessed() -> None:
    row = synthetic_trade(2)
    for k in ("entry_conditions_json", "levels_json", "trigger_candle_json", "exit_detail_json",
              "entry_reason"):
        row[k] = ""
    exp = explain_trade(row)
    o, c = exp["opened"], exp["closed"]
    assert o["entry_conditions"] == NOT_RECORDED
    assert o["previous_day_levels"] == NOT_RECORDED
    assert o["trigger_candle"] == NOT_RECORDED
    assert o["confirmation_rule"] == NOT_RECORDED
    assert o["entry_reason"] == NOT_RECORDED
    assert c["exit_condition"] == NOT_RECORDED and c["exit_detail"] == NOT_RECORDED
    for key in ("opened.entry_conditions", "opened.trigger_candle", "closed.exit_condition"):
        assert key in exp["missing"]


def test_corrupt_json_is_treated_as_not_recorded() -> None:
    row = synthetic_trade(3)
    row["entry_conditions_json"] = "{not json"
    row["exit_detail_json"] = "nan"
    exp = explain_trade(row)
    assert exp["opened"]["entry_conditions"] == NOT_RECORDED
    assert exp["closed"]["exit_detail"] == NOT_RECORDED


def test_confirmation_rule_only_when_recorded() -> None:
    row = synthetic_trade(4)
    row["entry_conditions_json"] = json.dumps({"confirmation_rule": "next candle closes above"})
    assert explain_trade(row)["opened"]["confirmation_rule"] == "next candle closes above"


def test_condition_dict_with_satisfied_failed_lists() -> None:
    row = synthetic_trade(5)
    row["entry_conditions_json"] = json.dumps({"satisfied": ["a"], "failed": ["b"], "x": 3})
    cond = explain_trade(row)["opened"]["entry_conditions"]
    assert cond == {"satisfied": ["a"], "failed": ["b"], "details": {"x": 3}}
