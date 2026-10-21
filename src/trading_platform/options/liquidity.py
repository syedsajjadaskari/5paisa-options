"""Liquidity and quality filters for option contracts."""

from dataclasses import dataclass

from trading_platform.data.option_chain import OptionBar


@dataclass(frozen=True)
class LiquidityFilterConfig:
    """Configurable thresholds for contract liquidity checks."""

    min_traded_volume: int = 1000
    max_bid_ask_spread_pct: float = 2.0
    min_premium_points: float = 20.0
    reject_zero_volume: bool = True


@dataclass(frozen=True)
class LiquidityCheckResult:
    """Result of contract liquidity check."""

    is_liquid: bool
    reason: str = "LIQUID"
    volume: int = 0
    spread_pct: float | None = None
    premium: float = 0.0


def evaluate_option_liquidity(
    bar: OptionBar,
    config: LiquidityFilterConfig | None = None,
) -> LiquidityCheckResult:
    """Evaluate whether an option candle exhibits sufficient market liquidity."""
    cfg = config or LiquidityFilterConfig()

    # 1. Minimum premium check (penny options rejection)
    if bar.close < cfg.min_premium_points:
        return LiquidityCheckResult(
            is_liquid=False,
            reason=f"PENNY_OPTION: Premium {bar.close:.2f} < min {cfg.min_premium_points}",
            volume=bar.volume,
            premium=bar.close,
        )

    # 2. Minimum volume check
    if cfg.reject_zero_volume and bar.volume <= 0:
        return LiquidityCheckResult(
            is_liquid=False,
            reason="ZERO_VOLUME: No trades recorded on candle",
            volume=bar.volume,
            premium=bar.close,
        )

    if bar.volume < cfg.min_traded_volume:
        return LiquidityCheckResult(
            is_liquid=False,
            reason=f"LOW_VOLUME: Volume {bar.volume} < min {cfg.min_traded_volume}",
            volume=bar.volume,
            premium=bar.close,
        )

    # 3. Bid-Ask spread check (if quotes exist)
    spread_pct = None
    if bar.bid is not None and bar.ask is not None and bar.ask > 0:
        spread_pct = ((bar.ask - bar.bid) / bar.ask) * 100.0
        if spread_pct > cfg.max_bid_ask_spread_pct:
            return LiquidityCheckResult(
                is_liquid=False,
                reason=f"WIDE_SPREAD: Bid/Ask spread {spread_pct:.2f}% > max {cfg.max_bid_ask_spread_pct}%",
                volume=bar.volume,
                spread_pct=spread_pct,
                premium=bar.close,
            )

    return LiquidityCheckResult(
        is_liquid=True,
        reason="LIQUID",
        volume=bar.volume,
        spread_pct=spread_pct,
        premium=bar.close,
    )
