"""Interactive Candlestick and Strategy Performance Visualizer.

Generates high-fidelity Plotly interactive charts overlaying NIFTY 50 5-minute candles,
market reference levels (PDH, PDL, PDC, IRH, IRL), dynamic entry/exit trade markers,
stop-loss and target boundaries, and hover performance analytics.
"""

from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from trading_platform.backtesting.engine import UnderlyingBacktestReport, UnderlyingTradeRecord
from trading_platform.core.enums import SignalDirection
from trading_platform.core.models import Bar
from trading_platform.data.reference_levels import ReferenceLevelCalculator


class StrategyVisualizer:
    """Plotly-based visualization generator for intraday strategy analysis."""

    def __init__(self, theme: str = "plotly_dark") -> None:
        self.theme = theme
        self.calculator = ReferenceLevelCalculator()

    def build_chart(
        self,
        bars: list[Bar],
        report: UnderlyingBacktestReport | None = None,
        title: str = "NIFTY 50 Intraday Strategy Execution",
    ) -> go.Figure:
        """Construct interactive 2-panel Plotly figure: Price Action & Trades + Volume."""
        if not bars:
            fig = go.Figure()
            fig.update_layout(title="No Bar Data Available")
            return fig

        timestamps = [b.exchange_time for b in bars]
        opens = [b.open for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        closes = [b.close for b in bars]
        volumes = [b.volume for b in bars]

        # Calculate reference levels and context for the bars
        enriched = self.calculator.enrich_bars_with_context(bars)
        last_ctx = enriched[-1] if enriched else None
        session_levels = last_ctx.session_levels if last_ctx else None

        # 2 Subplots: Row 1 = Candlesticks + Levels + Trades (80%), Row 2 = Volume (20%)
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.82, 0.18],
            specs=[[{"type": "xy"}], [{"type": "xy"}]],
        )

        # 1. Candlestick trace
        candlestick = go.Candlestick(
            x=timestamps,
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            name="NIFTY 50",
            increasing_line_color="#10b981",  # Emerald Green
            decreasing_line_color="#ef4444",  # Crimson Red
            showlegend=True,
        )
        fig.add_trace(candlestick, row=1, col=1)

        # 2. Volume bar chart
        vol_colors = [
            "#10b981" if c >= o else "#ef4444" for o, c in zip(opens, closes, strict=False)
        ]
        volume_bar = go.Bar(
            x=timestamps,
            y=volumes,
            marker_color=vol_colors,
            name="Volume",
            opacity=0.6,
            showlegend=False,
        )
        fig.add_trace(volume_bar, row=2, col=1)

        # 3. Horizontal Reference Levels
        x_min, x_max = timestamps[0], timestamps[-1]

        def add_level_line(price: float | None, name: str, color: str, dash: str) -> None:
            if price is not None and price > 0:
                fig.add_trace(
                    go.Scatter(
                        x=[x_min, x_max],
                        y=[price, price],
                        mode="lines",
                        line=dict(color=color, width=1.5, dash=dash),
                        name=f"{name} ({price:.1f})",
                        hoverinfo="name+y",
                    ),
                    row=1,
                    col=1,
                )

        if session_levels:
            add_level_line(session_levels.pdh, "PDH", "#f59e0b", "dash")  # Amber
            add_level_line(session_levels.pdl, "PDL", "#3b82f6", "dash")  # Blue
            add_level_line(session_levels.pdc, "PDC", "#8b5cf6", "dot")  # Violet

        if last_ctx:
            add_level_line(last_ctx.irh, "IRH (15m)", "#ec4899", "dashdot")  # Pink
            add_level_line(last_ctx.irl, "IRL (15m)", "#06b6d4", "dashdot")  # Cyan

        # 4. 200 EMA overlay if available
        enriched = self.calculator.enrich_bars_with_context(bars)
        ema_times = [ctx.bar.exchange_time for ctx in enriched if ctx.ema_200 is not None]
        ema_values = [ctx.ema_200 for ctx in enriched if ctx.ema_200 is not None]
        if ema_values:
            fig.add_trace(
                go.Scatter(
                    x=ema_times,
                    y=ema_values,
                    mode="lines",
                    line=dict(color="#f97316", width=1.5),  # Orange
                    name="200 EMA (5m)",
                ),
                row=1,
                col=1,
            )

        # 5. Overlay Trades if present in report
        if report and report.trades:
            self._overlay_trades(fig, report.trades)

        # Chart Layout & Aesthetics
        fig.update_layout(
            title=dict(
                text=f"<b>{title}</b>",
                font=dict(family="Inter, -apple-system, sans-serif", size=18, color="#f3f4f6"),
                x=0.02,
                y=0.98,
            ),
            template=self.theme,
            paper_bgcolor="#0b0f19",
            plot_bgcolor="#111827",
            xaxis=dict(
                rangeslider=dict(visible=False),
                gridcolor="#1f2937",
                color="#9ca3af",
            ),
            xaxis2=dict(
                gridcolor="#1f2937",
                color="#9ca3af",
                title="Time (IST)",
            ),
            yaxis=dict(
                gridcolor="#1f2937",
                color="#9ca3af",
                title="NIFTY Index Points",
            ),
            yaxis2=dict(
                gridcolor="#1f2937",
                color="#9ca3af",
                title="Volume",
            ),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1.0,
                font=dict(size=10, color="#d1d5db"),
                bgcolor="rgba(17, 24, 39, 0.8)",
            ),
            margin=dict(l=60, r=40, t=70, b=50),
            hovermode="x unified",
        )

        return fig

    def _overlay_trades(self, fig: go.Figure, trades: list[UnderlyingTradeRecord]) -> None:
        """Add markers and connectors for entries, exits, stop loss, and targets."""
        for t in trades:
            is_buy = t.direction == SignalDirection.BUY
            entry_symbol = "triangle-up" if is_buy else "triangle-down"
            entry_color = "#10b981" if is_buy else "#ef4444"

            # Entry marker
            fig.add_trace(
                go.Scatter(
                    x=[t.entry_time],
                    y=[t.entry_price],
                    mode="markers+text",
                    marker=dict(
                        symbol=entry_symbol,
                        size=14,
                        color=entry_color,
                        line=dict(width=1, color="#ffffff"),
                    ),
                    text=[f"{t.direction.value} ({t.method})"],
                    textposition="top center" if not is_buy else "bottom center",
                    textfont=dict(size=10, color=entry_color),
                    name=f"Entry {t.trade_id}",
                    hoverinfo="text",
                    hovertext=(
                        f"<b>Trade {t.trade_id} ENTRY</b><br>"
                        f"Strategy: {t.strategy_name} ({t.method})<br>"
                        f"Direction: {t.direction.value}<br>"
                        f"Time: {t.entry_time.strftime('%H:%M:%S')}<br>"
                        f"Price: {t.entry_price:.2f}<br>"
                        f"Stop Loss: {t.stop_loss:.2f}<br>"
                        f"Target: {t.target:.2f}"
                    ),
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

            # Exit marker
            exit_color = "#10b981" if t.is_win else "#ef4444"
            fig.add_trace(
                go.Scatter(
                    x=[t.exit_time],
                    y=[t.exit_price],
                    mode="markers+text",
                    marker=dict(
                        symbol="x", size=12, color=exit_color, line=dict(width=2, color="#ffffff")
                    ),
                    text=[f"{t.exit_reason} ({t.points_pnl:+.1f} pts)"],
                    textposition="top center" if is_buy else "bottom center",
                    textfont=dict(size=10, color=exit_color),
                    name=f"Exit {t.trade_id}",
                    hoverinfo="text",
                    hovertext=(
                        f"<b>Trade {t.trade_id} EXIT</b><br>"
                        f"Reason: {t.exit_reason}<br>"
                        f"Time: {t.exit_time.strftime('%H:%M:%S')}<br>"
                        f"Exit Price: {t.exit_price:.2f}<br>"
                        f"PnL: {t.points_pnl:+.2f} pts ({'WIN' if t.is_win else 'LOSS'})"
                    ),
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

            # Connect entry to exit
            fig.add_trace(
                go.Scatter(
                    x=[t.entry_time, t.exit_time],
                    y=[t.entry_price, t.exit_price],
                    mode="lines",
                    line=dict(color=exit_color, width=1.5, dash="dot"),
                    name=f"Trade {t.trade_id} Path",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

            # Draw SL and Target horizontal segments during trade duration
            fig.add_trace(
                go.Scatter(
                    x=[t.entry_time, t.exit_time],
                    y=[t.stop_loss, t.stop_loss],
                    mode="lines",
                    line=dict(color="#ef4444", width=1, dash="dash"),
                    name=f"SL {t.trade_id}",
                    hovertext=f"SL: {t.stop_loss:.2f}",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=[t.entry_time, t.exit_time],
                    y=[t.target, t.target],
                    mode="lines",
                    line=dict(color="#10b981", width=1, dash="dash"),
                    name=f"Target {t.trade_id}",
                    hovertext=f"Target: {t.target:.2f}",
                    showlegend=False,
                ),
                row=1,
                col=1,
            )

    def export_html(
        self,
        fig: go.Figure,
        output_path: Path | str,
    ) -> Path:
        """Export Plotly chart to a standalone HTML file."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.write_html(str(out), include_plotlyjs="cdn", full_html=True)
        return out

    def generate_strategy_report_chart(
        self,
        bars: list[Bar],
        report: UnderlyingBacktestReport,
        output_path: Path | str,
    ) -> Path:
        """Convenience method to build and export a complete strategy report chart."""
        title = f"{report.strategy_name} Execution (Win Rate: {report.win_rate_pct:.1f}%, PnL: {report.total_points:+.1f} pts)"
        fig = self.build_chart(bars=bars, report=report, title=title)
        return self.export_html(fig, output_path)
