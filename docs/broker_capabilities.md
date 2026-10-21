# 5paisa Broker Capabilities & Integration Architecture

## 1. Overview
This document reviews the official capabilities, APIs, and operational constraints of the 5paisa brokerage platform (OpenAPI Xstream and `py5paisa` SDK), establishing the boundaries for the platform's broker adapter.

**Official References:**
- OMS API Docs: [https://xstream.5paisa.com/dev-docs/order-management-system/place-order](https://xstream.5paisa.com/dev-docs/order-management-system/place-order)
- Python SDK: [https://github.com/OpenApi-5p/py5paisa](https://github.com/OpenApi-5p/py5paisa)

---

## 2. API Capabilities Matrix

| Functional Area | 5paisa API Support | py5paisa SDK Support | Implementation Status in Platform |
| :--- | :--- | :--- | :--- |
| **Authentication** | TOTP + Client Code + User Key + PIN/PW | `client.get_totp_session()` | Implemented via `FivePaisaBroker` |
| **Historical OHLC** | REST Historical Candles (1m, 5m, 1d) | `client.historical()` | Supported for historical ingestion |
| **Live Market Feeds** | WebSocket Feed (Market Depth & Ticks) | `client.Request_Marketfeed()` | Real-time streaming feed |
| **Instrument Master** | Scrip Master CSV download | `client.get_scrip_info()` | Daily instrument master ingestion |
| **Order Placement** | REST `PlaceOrderRequest` | `client.place_order()` | Sandboxed: PAPER by default; LIVE guarded |
| **Order Modification**| REST `ModifyOrderRequest` | `client.modify_order()` | Sandboxed: PAPER by default; LIVE guarded |
| **Order Cancellation**| REST `CancelOrderRequest` | `client.cancel_order()` | Sandboxed: PAPER by default; LIVE guarded |
| **Order Book** | REST `OrderBook` | `client.order_book()` | Periodic reconciliation & sync |
| **Trade Book** | REST `TradeBook` | `client.trade_book()` | Trade audit & journal generation |
| **Position Book** | REST `PositionBook` | `client.positions()` | Margin, exposure & reconciliation check |
| **Margin / Funds** | REST `Margin` | `client.margin()` | Risk engine pre-trade capital checks |

---

## 3. Authentication & Session Lifecycle

1. **Credentials Required:**
   - `AppName`, `AppSource`, `UserId` (Client Code), `Password`, `UserKey`, `EncryptionKey`, and `TOTPSecret`.
2. **Session Token Validity:**
   - 5paisa JWT access tokens typically expire daily at midnight IST.
   - The platform manages automatic reconnection and re-authentication during startup.
3. **Storage Security:**
   - Credentials are NEVER hard-coded in source files. They are loaded exclusively from `.env` or system environment variables.
   - The platform can initialize and run in `RESEARCH` and `PAPER` modes without valid 5paisa live credentials.

---

## 4. Operational Limits & Error Handling

1. **Rate Limits:**
   - Order placement: ~10 requests/sec.
   - Market data / historical queries: ~5 requests/sec.
   - The platform wraps broker requests with `tenacity` exponential backoff and rate-limiting throttles.
2. **WebSocket Resilience:**
   - Heartbeat monitoring every 30 seconds.
   - Automatic reconnect with exponential backoff on connection drop.
   - Cache recent ticks to prevent data gap during minor disconnections.
3. **Live Trading Guardrails:**
   - `OPERATIONAL_MODE` must explicitly equal `LIVE`.
   - `LIVE_TRADING_APPROVED` flag must be set to `True` in `configs/live_trading.yaml`.
   - Any order submission call fails immediately with `LiveTradingPermissionError` if these guards are not satisfied.
   - All orders generated in research or backtesting are routed to `PaperBroker` or the backtest simulator.
