# Indian Options Transaction Cost Methodology

## 1. Overview & Regulatory Baseline

Every backtested option trade in the platform is evaluated net of all statutory taxes, exchange fees, clearing charges, broker commissions, and market slippage.

---

## 2. Cost Component Breakdown

| Charge Component | Rate / Schedule | Statutory Source / Effective Date | Applicable Leg |
| :--- | :--- | :--- | :--- |
| **5paisa Brokerage** | Flat ₹20 per executed order | 5paisa standard tariff | Both Buy & Sell legs (₹40 round-trip) |
| **STT (Securities Transaction Tax)** | 0.0625% on Sell Premium value | Prior to Oct 1, 2024 | **Sell Leg ONLY** |
| | **0.1000%** on Sell Premium value | **Effective Oct 1, 2024** (Finance Act 2024) | **Sell Leg ONLY** |
| **Exchange Turnover Charges** | 0.0505% on Premium Turnover | NSE Derivatives Circular | Both Buy & Sell legs |
| **SEBI Turnover Charges** | ₹10 per crore (0.0001%) | SEBI regulatory levy | Both Buy & Sell legs |
| **Stamp Duty** | 0.0030% (₹300 per crore) | Indian Stamp Act (2020) | **Buy Leg ONLY** |
| **GST (Goods & Services Tax)** | 18% on (Brokerage + Exchange + SEBI) | Government of India | Both Buy & Sell |
| **Slippage** | Configurable (Default 0.50 pts per unit) | Execution microstructure | Both Entry & Exit fills |

---

## 3. Numerical Verification Example

**Setup:**
- Trading Date: 15-Oct-2024 (Post-Oct 1, 2024 $\rightarrow$ STT = 0.10%)
- Instrument: NIFTY 24OCT25000CE
- Quantity: 50 units (2 lots $\times$ 25 lot size)
- Entry Premium: ₹120.00
- Exit Premium: ₹150.00

**Calculations:**
1. **Buy Turnover:** $120.00 \times 50 = ₹6,000.00$
2. **Sell Turnover:** $150.00 \times 50 = ₹7,500.00$
3. **Total Turnover:** $₹13,500.00$
4. **Brokerage:** $₹20.00 \times 2 = ₹40.00$
5. **STT (Sell only @ 0.10%):** $7,500 \times 0.0010 = ₹7.50$
6. **Exchange Charges (0.0505%):** $13,500 \times 0.000505 = ₹6.82$
7. **Stamp Duty (Buy only @ 0.003%):** $6,000 \times 0.00003 = ₹0.18$
8. **SEBI Charges (0.0001%):** $13,500 \times 0.000001 = ₹0.01$
9. **GST (18% on ₹40 + ₹6.82 + ₹0.01 = ₹46.83):** $46.83 \times 0.18 = ₹8.43$
10. **Slippage (1 pt round-trip $\times$ 50):** $₹50.00$
11. **Total Costs:** $₹40.00 + ₹7.50 + ₹6.82 + ₹0.18 + ₹0.01 + ₹8.43 + ₹50.00 = ₹112.94$
12. **Gross P&L:** $(150 - 120) \times 50 = ₹1,500.00$
13. **Net P&L:** $₹1,500.00 - ₹112.94 = ₹1,387.06$
