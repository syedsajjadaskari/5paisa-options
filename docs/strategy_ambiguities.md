# Strategy Ambiguities & Discrepancies Inventory

This document registers all subjective, missing, or contradictory specifications identified across the five canonical strategy rulebooks. Each item is formally cataloged with its exact page reference, the computational obstacle, a deterministic proposed interpretation, its impact on the original method, and its approval status.

> [!IMPORTANT]
> In accordance with institutional research principles, **all unapproved interpretations are kept DISABLED by default**. The engine defaults to the strict, conservative baseline unless explicitly overridden via experimental configuration flags.

---

## 1. Initial Range Failure (IRF)

### AMB-IRF-01: Timing of the Failure Candle in Method 1
* **Original Rule & Page:** Page 1 ("the very next candle closes back inside the range"), Page 5 ("the very next candle, however, fails"), vs. Page 14 ("The next candle or following candle closes back inside the range").
* **Ambiguity:** Page 1 & 5 require strictly the immediately succeeding candle ($C_2$). Page 14 expands this to "the following candle" ($C_3$).
* **Computational Issue:** Non-deterministic definition of whether an intervening candle between breakout and reclaim is allowed.
* **Proposed Measurable Interpretation:**
  - *Baseline (Default):* Strict 2-candle sequence ($C_1$ closes $> IRH$, $C_2$ closes $< IRH$).
  - *Experimental Variant:* Multi-bar reclaim allowing up to $N=2$ candles to reclaim IRH.
* **Changes Original Method:** Yes, if relaxed beyond candle $C_2$.
* **Approval Status:** **PENDING_APPROVAL** (Baseline enforced by default).

### AMB-IRF-02: Stop-Loss Selection Rule (35 vs 40 Points)
* **Original Rule & Page:** Page 2 ("₹35 or ₹40, selected according to the most recent opposite (green) candle"), Page 10 ("The most recent green candle is within ₹35 of entry, so the ₹35 stop applies"), Page 14 ("₹35 (distance below ₹35) · ₹40 (distance above ₹35)").
* **Ambiguity:** Does "distance" mean:
  1. Entry price minus green candle low?
  2. Entry price minus green candle high?
  3. Size/range of the green candle itself?
* **Computational Issue:** Ambiguous anchor point for the distance calculation.
* **Proposed Measurable Interpretation:**
  - Measure distance as $\Delta = |Entry - High_{\text{recent green candle}}|$.
  - If $\Delta \le 35.0$ index points $\rightarrow SL = 35.0$ points.
  - If $\Delta > 35.0$ index points $\rightarrow SL = 40.0$ points.
* **Changes Original Method:** No, provides a precise mathematical interpretation consistent with Page 10 example.
* **Approval Status:** **PENDING_APPROVAL** (Parameter configurable).

### AMB-IRF-03: Definition of "Recent Support" & "Recent Resistance" in Method 2 & 3
* **Original Rule & Page:** Page 3 ("a price level formed by recent candles where price has held or turned upward"), Page 8, Page 11.
* **Ambiguity:** No numeric lookback window ($K$ bars) or swing pivot rule is defined for "recent support" or "recent resistance".
* **Computational Issue:** Algorithmic detection of support/resistance requires a precise swing fractal rule (e.g., $N$-bar swing low or lowest low of the last $N$ candles).
* **Proposed Measurable Interpretation:**
  - Define recent support as the lowest low of the last $K$ candles ($K=3$ default for 5-min chart) prior to the current candle.
  - Require a close strictly below this level: $Close < \min_{i \in [1, K]} Low_{t-i}$.
* **Changes Original Method:** Operationalizes a qualitative concept into a deterministic indicator.
* **Approval Status:** **PENDING_APPROVAL**.

### AMB-IRF-04: Method 3 "Small Candles Inside Range" & Wick Threshold
* **Original Rule & Page:** Page 11 ("Small candles form inside the initial range"), Page 12 ("Approximately 60–70% of the upper wick lies outside the range").
* **Ambiguity:** What qualifies as a "small candle"? Is the 60–70% a hard filter $[0.60, 0.70]$ or a minimum $\ge 0.60$?
* **Computational Issue:** A hard upper bound of 70% would disqualify a 75% wick sweep (which is logically even more decisive).
* **Proposed Measurable Interpretation:**
  - "Small candles": Bar range $\le 0.5 \times \text{ATR}(14)$ or bar range $\le 0.6 \times (IRH - IRL)$.
  - Wick threshold: Minimum $60\%$ of upper wick above IRH ($\frac{High - IRH}{High - Open} \ge 0.60$).
* **Changes Original Method:** Clarifies subjective wording.
* **Approval Status:** **PENDING_APPROVAL**.

---

## 2. Opening Range Breakout (ORB)

### AMB-ORB-01: Definition of "Established Trend"
* **Original Rule & Page:** Page 1, Page 3 ("Confirm the uptrend. Price above the 200 EMA is a basic confirmation, supported by bullish price structure").
* **Ambiguity:** "Bullish price structure" is subjective. Does it require higher highs and higher lows, ADX $> 25$, or solely $Close > EMA(200)$?
* **Computational Issue:** Algorithmic backtesting requires unambiguous trend qualification.
* **Proposed Measurable Interpretation:**
  - Baseline Trend Rule: $Close_{prev} > EMA_{200}(prev)$ AND $EMA_{200}$ slope is non-negative ($EMA_t \ge EMA_{t-5}$).
  - Multi-timeframe Option: Daily chart $Close > EMA(200)$ or 5-min chart $Close > EMA(200)$.
* **Changes Original Method:** Quantifies "bullish price structure".
* **Approval Status:** **PENDING_APPROVAL**.

### AMB-ORB-02: Quantification of "Strong Body" and "Small Rejection Wick"
* **Original Rule & Page:** Page 3, Page 10 ("Large green body, close near the high, small upper wick").
* **Ambiguity:** No percentage thresholds given for body-to-range ratio or wick-to-range ratio.
* **Computational Issue:** Candle classification needs deterministic thresholds.
* **Proposed Measurable Interpretation:**
  - Body Ratio: $\frac{|Close - Open|}{High - Low} \ge 0.50$ (body represents at least 50% of full bar range).
  - Upper Wick Ratio (for Long): $\frac{High - \max(Open, Close)}{High - Low} \le 0.20$ (upper wick is $\le 20\%$ of bar range).
  - Lower Wick Ratio (for Short): $\frac{\min(Open, Close) - Low}{High - Low} \le 0.20$ (lower wick is $\le 20\%$ of bar range).
* **Changes Original Method:** Quantifies candle quality rules.
* **Approval Status:** **PENDING_APPROVAL**.

### AMB-ORB-03: Method 2 Trendline Construction
* **Original Rule & Page:** Page 5 ("Draw the rising trendline through the relevant swing lows").
* **Ambiguity:** Manual visual trendlines depend on human discretion.
* **Computational Issue:** Automated trendline identification requires objective swing pivot detection (e.g. 2 consecutive swing lows with linear regression or pivot point connections).
* **Proposed Measurable Interpretation:**
  - Identify swing pivots using a 3-bar swing low algorithm ($Low_{t} < Low_{t-1}, Low_{t-2}, Low_{t+1}, Low_{t+2}$).
  - Connect the two most recent swing lows. Validate positive slope.
* **Changes Original Method:** Operationalizes manual drawing.
* **Approval Status:** **PENDING_APPROVAL**.

---

## 3. Previous Day Resistance (PDR)

### AMB-PDR-01: Definition of "Previous Day's High and Reversal"
* **Original Rule & Page:** Page 1 ("Find the high where price reached a peak and then reversed"), Page 2.
* **Ambiguity:** Is this strictly the Previous Day High (PDH), or can any intraday swing high from yesterday qualify?
* **Computational Issue:** Multiple intraday peaks could generate competing zones.
* **Proposed Measurable Interpretation:**
  - *Baseline:* The absolute high of the previous trading session (PDH) and its associated 5-min candle.
  - *Variant:* Major swing highs identified by fractal order 5.
* **Changes Original Method:** Prevents subjective peak picking.
* **Approval Status:** **PENDING_APPROVAL** (Baseline PDH enforced by default).

### AMB-PDR-02: "Very Close Approach" to the Zone
* **Original Rule & Page:** Page 2 ("Price comes close enough to count as a test of the zone").
* **Ambiguity:** Subjective definition of proximity without numerical buffer.
* **Computational Issue:** An exact touch vs. proximity buffer changes trade frequency drastically.
* **Proposed Measurable Interpretation:**
  - Zone is defined by $[Low_{zone}, High_{zone}] = [\min(Open, Close)_{candle}, High_{candle}]$.
  - Proximity buffer: Price low must be within $0.15 \times \text{ATR}(14)$ or within 5 index points of $High_{zone}$.
* **Changes Original Method:** Standardizes proximity test.
* **Approval Status:** **PENDING_APPROVAL**.

### AMB-PDR-03: Stop-Loss Discretion (35 vs 40 Points)
* **Original Rule & Page:** Page 2 ("35 or 40 points, depending on the situation").
* **Ambiguity:** Rulebook does not specify what "the situation" means for PDR (unlike IRF and PDS which reference the green candle distance).
* **Proposed Measurable Interpretation:**
  - Apply the PDS/IRF distance heuristic: If distance from entry to lowest point of retest candle is $\le 35$ points $\rightarrow SL = 35$; else $SL = 40$.
  - Provide a fixed 35-point or 40-point configuration parameter.
* **Changes Original Method:** Removes discretion.
* **Approval Status:** **PENDING_APPROVAL**.

---

## 4. Previous Day Support (PDS)

### AMB-PDS-01: Discrepancies Explicitly Listed in PDF Open Items
* **Original Rule & Page:** Pages 5 & 6 (Table "Open item / Why it matters").
* **Ambiguities Documented in Rulebook:**
  1. *What counts as a good red candle:* Body size and close not numerically defined.
  2. *HPR pin bar definition:* Wick-to-body ratio not specified.
  3. *Size of small support (Method 3):* Number of candles and height not defined.
  4. *Support break trigger:* Close below vs. touch during candle.
  5. *Distance of exactly 35 points:* Boundary condition for 35 vs 40.
  6. *How green candle distance is measured:* Anchor point undefined.
  7. *Entry timing:* Intra-candle touch vs. bar close.
* **Proposed Measurable Interpretation:**
  1. Good red candle: Body ratio $\ge 0.50$ and close within bottom 25% of bar.
  2. HPR pin bar: Upper wick $\ge 2.0 \times \text{Body}$, $Close < Open$.
  3. Small support: 2 to 4 consecutive candles with equal/similar lows ($\pm 3$ pts).
  4. Support break: Requires strict bar CLOSE below support level.
  5. Distance boundary: Distance $\le 35.0 \rightarrow SL = 35$; Distance $> 35.0 \rightarrow SL = 40$.
  6. Measurement: $\Delta = |High_{\text{green candle}} - Entry|$.
  7. Entry timing: Always at bar CLOSE.
* **Changes Original Method:** Directly implements the solutions requested in the author's open items table.
* **Approval Status:** **PENDING_APPROVAL**.

---

## 5. Reverse Failure (RF)

### AMB-RF-01: Open Specifications from Strategy Rulebook
* **Original Rule & Page:** Page 8 (Rule readiness table).
* **Ambiguities Documented in Rulebook:**
  1. *Stop-loss size:* Listed as PARTIAL (35–40 points; fixed vs candle-extreme not stated).
  2. *Exact entry price:* Listed as OPEN ("Not yet specified").
  3. *Profit target:* Listed as OPEN ("Not yet specified").
* **Proposed Measurable Interpretation:**
  - *Entry Trigger:* Enter Long immediately at the close of the candle that reclaims the RF zone ($Close > IRL$).
  - *Stop-Loss:* Fixed 35 or 40 points below entry price ($SL = Entry - 35$ or $Entry - 40$), or low of the failure sweep candle.
  - *Target:* Fixed 60 or 70 points (matching IRF / PDR 1:1.5–1:2 risk-reward ratios).
* **Changes Original Method:** Formulates complete parameters for items left open by the author.
* **Approval Status:** **PENDING_APPROVAL** (Kept under research mode).

---

## Summary Matrix of Approval Status

| Strategy | Ambiguity ID | Category | Default Behavior | Approval Status |
| :--- | :--- | :--- | :--- | :--- |
| **IRF** | AMB-IRF-01 | Timing Window | Strict 1-bar failure ($C_2$) | **PENDING_APPROVAL** |
| **IRF** | AMB-IRF-02 | SL Heuristic | $\Delta = \|Entry - High_{\text{green}}\|$ | **PENDING_APPROVAL** |
| **IRF** | AMB-IRF-03 | Level Detection | 3-bar swing pivot | **PENDING_APPROVAL** |
| **IRF** | AMB-IRF-04 | Wick Sweep % | Upper wick $\ge 60\%$ above IRH | **PENDING_APPROVAL** |
| **ORB** | AMB-ORB-01 | Trend Filter | $Close > EMA_{200}$ & non-negative slope | **PENDING_APPROVAL** |
| **ORB** | AMB-ORB-02 | Candle Quality | Body $\ge 50\%$, opposing wick $\le 20\%$ | **PENDING_APPROVAL** |
| **ORB** | AMB-ORB-03 | Trendline Break | Algorithmic pivot line | **PENDING_APPROVAL** |
| **PDR** | AMB-PDR-01 | Zone Definition | Previous Day Absolute High (PDH) | **PENDING_APPROVAL** |
| **PDR** | AMB-PDR-02 | Proximity Buffer | $\le 5$ points or $0.15 \times \text{ATR}$ | **PENDING_APPROVAL** |
| **PDR** | AMB-PDR-03 | SL Choice | Green candle distance rule | **PENDING_APPROVAL** |
| **PDS** | AMB-PDS-01 | PDF Open Items | Deterministic thresholds per spec | **PENDING_APPROVAL** |
| **RF** | AMB-RF-01 | Unspecified Entry/TP | Reclaim close, Target 60, SL 35 | **PENDING_APPROVAL** |
