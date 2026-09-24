"""Black-Scholes helpers. Used by the synthetic feed, and to derive delta when
a data source provides IV but no greeks."""

from __future__ import annotations

from math import erf, exp, log, sqrt

RISK_FREE = 0.04


def _cdf(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def _d1(spot: float, strike: float, t: float, iv: float) -> float:
    return (log(spot / strike) + (RISK_FREE + iv * iv / 2) * t) / (iv * sqrt(t))


def bs_price(spot: float, strike: float, t_years: float, iv: float, is_call: bool) -> float:
    if t_years <= 0 or iv <= 0:
        return max(0.0, spot - strike if is_call else strike - spot)
    d1 = _d1(spot, strike, t_years, iv)
    d2 = d1 - iv * sqrt(t_years)
    disc = strike * exp(-RISK_FREE * t_years)
    if is_call:
        return spot * _cdf(d1) - disc * _cdf(d2)
    return disc * _cdf(-d2) - spot * _cdf(-d1)


def bs_delta(spot: float, strike: float, t_years: float, iv: float, is_call: bool) -> float:
    if t_years <= 0 or iv <= 0:
        itm = spot > strike if is_call else spot < strike
        return (1.0 if is_call else -1.0) if itm else 0.0
    d1 = _d1(spot, strike, t_years, iv)
    return _cdf(d1) if is_call else _cdf(d1) - 1
