# Strategy Developer Clarification Questions

These questions are restricted to unresolved rule definitions that can change
PDS, PDR, ORB, or IRF signal counts. They are not implementation assumptions.

1. PDS/PDR zone: Is the previous-day zone the body of the candle containing
   PDH/PDL, the complete candle range, or a fixed point? What exact proximity
   tolerance constitutes a zone test?
2. PDS/PDR gap: Is the gap measured against PDC, PDO, or another level? Is any
   positive/negative gap sufficient, or is a minimum point/percentage threshold
   required?
3. PDS/PDR confirmation: What exact candle quality rule is required for “good
   red”, “HPR”, “green”, or “small support”? Please specify body/wick ratios,
   close location, and whether the confirmation candle must close beyond the
   zone.
4. PDS/PDR timing: May a zone test and confirmation occur on the same candle?
   May the setup remain valid after a failed or repeated test, and for how many
   candles?
5. ORB: Is the first candle exactly 09:15-09:20, and is entry allowed only on
   the next completed candle? Define established trend and the required body/
   wick quality numerically.
6. ORB levels: Which of PDH/PDL, prior-day body levels, EMA/DMA, or inner levels
   are mandatory, and what precedence applies when levels overlap?
7. IRF Method 1/2/3: Define the exact breakout, immediate return, extension
   count, recent-support lookback, and red-candle quality. Confirm whether a
   close or intrabar touch is required.
8. RF: Define the canonical entry trigger, stop, target, and exit. The current
   guide does not provide an executable canonical exit, so RF remains blocked.

Historical examples attached to the PDF use only stored provider OHLC. They
illustrate where the missing definitions affect eligibility; they do not assert
that a signal should have been taken.
