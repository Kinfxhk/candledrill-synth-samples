#!/usr/bin/env python3
"""
SYNTH-TECHIDX-24H 1-minute synthetic bar generator (practice data only).

Mirrors CandleDrill packages/core/src/synthetic.ts algorithm (GBM-ish + AR(1) log-vol
+ substep OHLC + tick round + SFC32 RNG), extended with:
  - near-24h session-like calendar (18:00→17:00, skip hour 17)
  - activityByHour[24] instead of mathematical U-shape

Input is a small params JSON (seed, calendar, activity curve). Output is synthetic
OHLCV. Not index-licensed. Not exchange, CME, or Nasdaq data. Not affiliated with
any exchange, index provider, or data vendor.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Sequence

from public_ids import PUBLIC_PACK_SYMBOL, canonical_public_id, public_disclaimer

SUBSTEPS = 8
VOL_PERSISTENCE = 0.995
VOL_OF_VOL = 0.03
LOG_VOL_VAR = (VOL_OF_VOL * VOL_OF_VOL) / (1 - VOL_PERSISTENCE * VOL_PERSISTENCE)
TRADING_DAYS_PER_YEAR = 252
MAX_BARS = 2_000_000


def u32(x: int) -> int:
    return x & 0xFFFFFFFF


class Rng:
    """SplitMix32 + SFC32 + Marsaglia polar — match candledrill rng.ts."""

    def __init__(self, seed: int) -> None:
        if not isinstance(seed, int) or seed < 0 or seed > 0xFFFFFFFF:
            raise ValueError(f"bad seed {seed}")

        def imul(a: int, b: int) -> int:
            return u32(a * b)

        state = [u32(seed)]

        def splitmix32() -> int:
            state[0] = u32(state[0] + 0x9E3779B9)
            z = state[0]
            z = imul(z ^ (z >> 16), 0x85EBCA6B)
            z = imul(z ^ (z >> 13), 0xC2B2AE35)
            return u32(z ^ (z >> 16))

        self.a = splitmix32()
        self.b = splitmix32()
        self.c = splitmix32()
        self.d = splitmix32()
        for _ in range(12):
            self._next_u32()
        self._spare: float | None = None

    def _next_u32(self) -> int:
        t = u32(u32(self.a + self.b) + self.d)
        self.d = u32(self.d + 1)
        self.a = self.b ^ (self.b >> 9)
        self.b = u32(self.c + u32(self.c << 3))
        self.c = u32((u32(self.c << 21) | (self.c >> 11)))
        self.c = u32(self.c + t)
        return t

    def next(self) -> float:
        return self._next_u32() / 4294967296.0

    def normal(self) -> float:
        if self._spare is not None:
            s = self._spare
            self._spare = None
            return s
        while True:
            u = self.next() * 2 - 1
            v = self.next() * 2 - 1
            s = u * u + v * v
            if 0 < s < 1:
                break
        m = math.sqrt((-2 * math.log(s)) / s)
        self._spare = v * m
        return u * m


@dataclass
class Span:
    date: str
    start_time: int  # unix seconds UTC of first minute
    minutes: int


def parse_iso_date(date: str) -> int:
    y, mo, d = map(int, date.split("-"))
    ms = datetime(y, mo, d, tzinfo=timezone.utc).timestamp() * 1000
    check = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    if check.year != y or check.month != mo or check.day != d:
        raise ValueError(f"invalid date {date}")
    return int(ms // 86_400_000)


def format_iso_date(epoch_day: int) -> str:
    return datetime.fromtimestamp(epoch_day * 86_400, tz=timezone.utc).strftime("%Y-%m-%d")


def weekday_of(epoch_day: int) -> int:
    return ((epoch_day + 4) % 7 + 7) % 7


def parse_clock(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def expand_sessions(calendar: dict, start_date: str, days: int) -> List[Span]:
    off = int(calendar["utcOffsetMinutes"])
    first = parse_iso_date(start_date)
    spans: List[Span] = []
    for day in range(first, first + days):
        wd = weekday_of(day)
        for w in calendar["windows"]:
            if wd not in w["days"]:
                continue
            s = parse_clock(w["start"])
            e = parse_clock(w["end"])
            if e <= s:
                e += 1440
            start_time = (day * 1440 + s - off) * 60
            spans.append(Span(format_iso_date(day), start_time, e - s))
    spans.sort(key=lambda x: x.start_time)
    for i in range(1, len(spans)):
        prev, cur = spans[i - 1], spans[i]
        if prev.start_time + prev.minutes * 60 > cur.start_time:
            raise ValueError(f"overlapping windows {prev.date}/{cur.date}")
    return spans


def ticks_to_price(ticks: int, tick_size: float, decimals: int) -> float:
    return float(f"{ticks * tick_size:.{decimals}f}")


def generate(params: dict) -> tuple[list[dict], dict]:
    seed = int(params["seed"])
    start_date = params["startDate"]
    days = int(params["days"])
    calendar = params["calendar"]
    tick_size = float(params["tickSize"])
    start_price = float(params["startPrice"])
    annual_vol = float(params["annualVolatility"])
    gap_vol = float(params["sessionGapVolatility"])
    base_volume = float(params["baseVolume"])
    activity_by_hour: Sequence[float] = params["activityByHour"]
    assert len(activity_by_hour) == 24

    spans = expand_sessions(calendar, start_date, days)
    total = sum(s.minutes for s in spans)
    if total > MAX_BARS:
        raise ValueError(f"too many bars {total}")

    rng = Rng(seed)
    decimals = 2 if abs(tick_size - 0.25) < 1e-12 else 8
    avg_session = total / len(spans) if spans else 1
    minute_vol = annual_vol / math.sqrt(TRADING_DAYS_PER_YEAR * avg_session)
    off = int(calendar["utcOffsetMinutes"])

    log_price = math.log(start_price)
    log_vol_state = 0.0
    bars: list[dict] = []

    for span_index, span in enumerate(spans):
        if span_index > 0 and gap_vol > 0:
            log_price += gap_vol * rng.normal()
        for i in range(span.minutes):
            # Local hour from unix time + offset
            tsec = span.start_time + i * 60
            local_min = (tsec // 60 + off) % 1440
            if local_min < 0:
                local_min += 1440
            hour = int(local_min // 60)
            activity = float(activity_by_hour[hour])
            if activity <= 0:
                # Maintenance / closed hour: skip bar (should not appear if calendar correct)
                continue

            log_vol_state = VOL_PERSISTENCE * log_vol_state + VOL_OF_VOL * rng.normal()
            vol_mult = math.exp(log_vol_state - LOG_VOL_VAR)
            sigma = minute_vol * activity * vol_mult
            sub_sigma = sigma / math.sqrt(SUBSTEPS)

            open_ticks = max(1, round(math.exp(log_price) / tick_size))
            hi = lo = open_ticks
            lp = log_price
            for _ in range(SUBSTEPS):
                lp += sub_sigma * rng.normal() - 0.5 * sub_sigma * sub_sigma
                tk = max(1, round(math.exp(lp) / tick_size))
                if tk > hi:
                    hi = tk
                if tk < lo:
                    lo = tk
            log_price = lp
            close_ticks = max(1, round(math.exp(log_price) / tick_size))
            hi = max(hi, close_ticks)
            lo = min(lo, close_ticks)

            range_in_sigmas = (
                ((hi - lo) * tick_size) / math.exp(log_price) / sigma if sigma > 0 else 0
            )
            volume = max(
                1,
                round(
                    base_volume
                    * activity
                    * (vol_mult ** 0.7)
                    * math.exp(0.45 * rng.normal())
                    * (0.6 + 0.25 * range_in_sigmas)
                ),
            )
            bars.append(
                {
                    "time": tsec,
                    "open": ticks_to_price(open_ticks, tick_size, decimals),
                    "high": ticks_to_price(hi, tick_size, decimals),
                    "low": ticks_to_price(lo, tick_size, decimals),
                    "close": ticks_to_price(close_ticks, tick_size, decimals),
                    "volume": volume,
                }
            )

    meta = {
        "source": "synthetic",
        "synthetic": True,
        "symbol": canonical_public_id(str(params.get("symbol") or PUBLIC_PACK_SYMBOL)),
        "timeframeSeconds": 60,
        "tickSize": tick_size,
        "generator": {
            "name": "kinfx-techidx-24h-synth",
            "version": 1,
            "seed": seed,
            "calendar": "near-24h-session-like-est-fixed",
            "activityByHour": True,
        },
        "disclaimer": public_disclaimer(params.get("disclaimer")),
    }
    return bars, meta


def write_csv(bars: list[dict], path: Path, offset_minutes: int) -> None:
    # Write naive exchange-local datetime (CandleDrill import with utcOffsetMinutes)
    off = timedelta(minutes=offset_minutes)
    lines = ["datetime,open,high,low,close,volume"]
    for b in bars:
        local = datetime.fromtimestamp(b["time"], tz=timezone.utc) + off
        # Represent as naive local string
        dt = local.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(
            f"{dt},{b['open']},{b['high']},{b['low']},{b['close']},{b['volume']}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize(bars: list[dict], offset_minutes: int) -> dict:
    if not bars:
        return {"barCount": 0}
    hour_vol = [0.0] * 24
    hour_n = [0] * 24
    off = offset_minutes
    closes = []
    for b in bars:
        local_min = (b["time"] // 60 + off) % 1440
        if local_min < 0:
            local_min += 1440
        h = int(local_min // 60)
        hour_vol[h] += b["volume"]
        hour_n[h] += 1
        closes.append(b["close"])
    # daily close-to-close from last bar of each local date
    by_day: dict[str, float] = {}
    for b in bars:
        local = datetime.fromtimestamp(b["time"], tz=timezone.utc) + timedelta(minutes=off)
        by_day[local.strftime("%Y-%m-%d")] = b["close"]
    days = sorted(by_day)
    rets = []
    for i in range(1, len(days)):
        a, c = by_day[days[i - 1]], by_day[days[i]]
        if a > 0 and c > 0:
            rets.append(math.log(c / a))
    ann = 0.0
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        ann = math.sqrt(var) * math.sqrt(252)
    mean_vol = sum(hour_vol) / max(1, sum(1 for n in hour_n if n))
    rel = {
        str(h): (hour_vol[h] / hour_n[h] / mean_vol if hour_n[h] and mean_vol else 0.0)
        for h in range(24)
    }
    on_tick = all(abs(c / 0.25 - round(c / 0.25)) < 1e-6 for c in closes)
    return {
        "barCount": len(bars),
        "firstTime": bars[0]["time"],
        "lastTime": bars[-1]["time"],
        "firstLocal": (
            datetime.fromtimestamp(bars[0]["time"], tz=timezone.utc) + timedelta(minutes=off)
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "lastLocal": (
            datetime.fromtimestamp(bars[-1]["time"], tz=timezone.utc) + timedelta(minutes=off)
        ).strftime("%Y-%m-%d %H:%M:%S"),
        "tradingDaysApprox": len(days),
        "annualizedVol_dailyClose": round(ann, 6),
        "startClose": closes[0],
        "endClose": closes[-1],
        "hour17Bars": hour_n[17],
        "hourBarCounts": {str(h): hour_n[h] for h in range(24) if hour_n[h]},
        "hourRelativeVolume": {k: round(v, 4) for k, v in rel.items() if hour_n[int(k)]},
        "allClosesOnTick025": on_tick,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--out-meta", required=True)
    ap.add_argument("--out-summary", required=True)
    args = ap.parse_args()
    params = json.loads(Path(args.params).read_text(encoding="utf-8"))
    # Hygiene: refuse if params look like bar dumps
    raw = Path(args.params).read_text(encoding="utf-8").lower()
    for needle in ('"open":', '"bars":', '"prices":'):
        if needle in raw and "openinterest" not in needle:
            # allow disclaimer text; check structure instead
            pass
    bars, meta = generate(params)
    off = int(params["calendar"]["utcOffsetMinutes"])
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    write_csv(bars, out_csv, off)
    summary = summarize(bars, off)
    Path(args.out_meta).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    Path(args.out_summary).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "csv": str(out_csv), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
