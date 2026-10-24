"""Independent reference calculation for indicator_reference.csv.

Plain-Python recursion, deliberately not using pandas, so it checks the
pandas-based implementation. Run: python tests/fixtures/make_reference.py
"""

import csv
from pathlib import Path

HERE = Path(__file__).resolve().parent


def ema(values, n):
    a = 2.0 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(a * v + (1 - a) * out[-1])
    return out


def main():
    rows = list(csv.DictReader((HERE / "indicator_input.csv").open(encoding="utf-8")))
    close = [float(r["close"]) for r in rows]
    e13, e26, e12 = ema(close, 13), ema(close, 26), ema(close, 12)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    hist = [m - s for m, s in zip(macd, sig)]
    states = ["unavailable"]
    for t in range(1, len(close)):
        up = e13[t] > e13[t - 1] and hist[t] > hist[t - 1]
        down = e13[t] < e13[t - 1] and hist[t] < hist[t - 1]
        states.append("green" if up else "red" if down else "blue")
    with (HERE / "indicator_reference.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["session_date", "close", "ema13", "ema26", "macd", "signal", "histogram", "elder_state"])
        for i, r in enumerate(rows):
            w.writerow([r["session_date"], r["close"], f"{e13[i]:.12f}", f"{e26[i]:.12f}",
                        f"{macd[i]:.12f}", f"{sig[i]:.12f}", f"{hist[i]:.12f}", states[i]])


if __name__ == "__main__":
    main()
