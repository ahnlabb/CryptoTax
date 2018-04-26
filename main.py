import csv
import pickle
from typing import List, Dict, Any
from functools import reduce
from itertools import groupby
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path
from copy import deepcopy

import dateutil.parser

# import sqlite3
# connection = sqlite3.connect(':memory:')


class Table:
    def __init__(self, header, rows):
        self.header = header
        self.rows = rows


def _load_csv(filepath) -> List[Dict[str, Any]]:
    with open(filepath, "r") as csvfile:
        reader = csv.reader(csvfile, delimiter=',')
        header = next(reader)
        return list(dict(zip(header, row)) for row in reader)


def _load_price_csv(symbol):
    """Returns a dict mapping from date to price"""
    with open(f"data_public/prices-{symbol}.csv", "r") as csvfile:
        price_by_date = {}
        reader = csv.reader(csvfile, delimiter=',')
        next(reader)  # discard header
        for row in reader:
            price_by_date[row[0]] = float(row[1])
        return price_by_date


def _load_price_csv2(symbol):
    """Returns a dict mapping from date to price"""
    history = _load_pricehistory(symbol)
    return {k: v["open"] for k, v in history.items()}


def _load_pricehistory(symbol) -> Dict[date, Dict[str, float]]:
    """Returns a dict mapping from date to price"""
    with open(f"tmp/{symbol}-pricehistory.pickle", "rb") as f:
        data = {k.isoformat(): v for k, v in pickle.load(f).items()}
        return data


def _load_incoming_balances() -> List[Dict[str, Any]]:
    p = Path("data_private/balances-incoming.csv")
    if p.exists():
        data = _load_csv(p.absolute())
        return data
    else:
        return []


symbolmap = {
    "XXBTC": "XXBT",
    "XBT": "XXBT",
    "XXDG": "XXDG",
    "ETH": "XETH",
    "BTC": "XXBT",
    "BCH": "XBCH",
    "GNO": "XGNO",
    "EOS": "XEOS",
}


def _format_csv_from_kraken(trades_csv):
    "Format a CSV from a particular source into a canonical data format"
    for trade in trades_csv:
        # Kraken has really weird pair formatting...
        pairlen = int(len(trade["pair"]) / 2)
        trade["pair"] = (trade["pair"][:pairlen], trade["pair"][pairlen:])
        trade["pair"] = tuple(map(lambda symbol: symbolmap[symbol] if symbol in symbolmap else symbol, trade["pair"]))

        trade["time"] = dateutil.parser.parse(trade["time"])
        trade["price"] = float(trade["price"])
        trade["vol"] = float(trade["vol"])
        trade["cost"] = float(trade["cost"])

        del trade["txid"]
        del trade["ordertxid"]
    return trades_csv


def _format_csv_from_poloniex(trades_csv):
    "Format a CSV from a particular source into a canonical data format"
    for trade in trades_csv:
        trade["pair"] = trade.pop("Market").split("/")
        trade["pair"] = tuple(map(lambda symbol: symbolmap[symbol] if symbol in symbolmap else symbol, trade["pair"]))
        trade["type"] = trade.pop("Type").lower()

        trade["time"] = dateutil.parser.parse(trade.pop("Date"))
        trade["price"] = float(trade.pop("Price"))
        trade["vol"] = float(trade.pop("Amount"))
        trade["cost"] = float(trade.pop("Total"))

        del trade["Category"]
        del trade["Order Number"]
        del trade["Fee"]
        del trade["Base Total Less Fee"]
        del trade["Quote Total Less Fee"]

    print(trades_csv[0])

    return trades_csv


def _print_trades(trades, n=None):
    h = dict(zip(trades[0].keys(), trades[0].keys()))
    print(f"{h['time']:10}  {h['pair']:12.12}  {h['type']:4.4}  {h['price']:12}  {h['vol']:9}  {h['cost']:12.10}  {h['cost_usd']:14.14}")
    for d in (trades[:n] if n else trades):
        print(f"{d['time'].isoformat():.10}  {' / '.join(d['pair']):12}  {d['type']:4.4}  {d['price']:12.6}  {d['vol']:9.6}  {d['cost']:12.6}  {str(d['cost_usd']):14.14}")


def _sum_trades(t1, t2):
    # Price becomes volume-weighted average
    t1["price"] = (t1["price"] * t1["vol"] + t2["price"] * t2["vol"]) / (t1["vol"] + t2["vol"])
    # Volume becomes sum of trades
    t1["vol"] += t2["vol"]
    t1["cost"] += t2["cost"]
    if "cost_usd" in t1:
        t1["cost_usd"] += t2["cost_usd"]
    return t1


def test_sum_trades():
    t1 = {"price": 1, "vol": 1, "cost": 1}
    t2 = {"price": 2, "vol": 1, "cost": 2}
    t3 = _sum_trades(t1, t2)
    assert t3["price"] == 1.5
    assert t3["vol"] == 2
    assert t3["cost"] == 3


def _reduce_trades(trades):
    """Merges consequtive trades in the same pair on the same day"""
    def r(processed, next):
        if len(processed) == 0:
            processed.append(next)
        else:
            last = processed[-1]
            if last["time"].date() == next["time"].date() and \
               last["pair"] == next["pair"]:
                processed[-1] = _sum_trades(last, next)
            else:
                processed.append(next)
        return processed
    return reduce(r, trades, [])


def _calc_cost_usd(trades):
    btcusd_price_csv = _load_price_csv2("XXBT")
    ethusd_price_csv = _load_price_csv2("XETH")
    xlmusd_price_csv = _load_price_csv2("XXLM")

    # TODO: Use actual rates
    eurusd_rate = 1.23

    for trade in trades:
        date = trade["time"].date().isoformat()
        if trade["pair"][1] in ["ZEUR"]:
            # Buy/sell something valued in EUR
            trade["cost_usd"] = trade["cost"] * eurusd_rate

        elif trade["pair"][1] in ["XXBT", "XBT", "XXBTC"]:
            # Buy/sell something valued in BTC
            trade["cost_usd"] = trade["cost"] * btcusd_price_csv[date]

        elif trade["pair"][1] in ["ETH", "XETH"]:
            # Buy/sell something valued in ETH
            trade["cost_usd"] = trade["cost"] * ethusd_price_csv[date]

        elif trade["pair"][1] in ["XXLM"]:
            # Buy/sell something valued in XLM
            trade["cost_usd"] = trade["cost"] * xlmusd_price_csv[date]

        else:
            print(f"Could not calculate USD cost for pair: {trade['pair']}, add support for {trade['pair'][1]}")
            trade["cost_usd"] = None
    return trades


def _cost_basis_per_asset(trades):
    costbasis = defaultdict(lambda: 0)
    vol = defaultdict(lambda: 0)

    incoming = _load_incoming_balances()
    if incoming:
        print("WARNING: Loaded incoming balances, setting cost to zero.")
        for r in incoming:
            costbasis[r["asset"]] += 0
            vol[r["asset"]] += float(r["amount"])

    for trade in trades:
        if trade["type"] == "buy" and trade["cost_usd"]:
            costbasis[trade["pair"][0]] += trade["cost_usd"]
            vol[trade["pair"][0]] += trade["vol"]

    print(f"Asset   Costbasis         Vol     Cost/vol")
    for asset in costbasis:
        print(f"{asset.ljust(5)}   {round(costbasis[asset]):8}$     {vol[asset]:7}  {round(costbasis[asset]/vol[asset], 3):10.10}$")


def _filter_trades_by_time(trades, year):
    return list(filter(lambda t: datetime(year, 1, 1) <= t["time"] < datetime(year + 1, 1, 1), trades))


def test_filter_trades_by_time():
    _t1 = {"time": datetime(2017, 12, 30, 23, 42)}
    _t2 = {"time": datetime(2018, 1, 1, 1, 42)}
    assert 1 == len(_filter_trades_by_time([_t1, _t2], 2017))
    assert 1 == len(_filter_trades_by_time([_t1, _t2], 2018))


def _normalize_trade_type(t):
    """
    Normalizes t trades into a buy by flipping the pair
    such that asset1 is always being bought
    """
    # WARNING: NOT WORKING AND NOT TESTED
    # not even sure if it's a good idea to use it even if it worked perfectly
    raise NotImplementedError
    print(t)
    if t["type"] == "sell":
        t["pair"] = tuple(reversed(t["pair"]))
        t["vol"] = t["cost"] / t["price"]
        t["price"] = 1 / t["price"]
    return t


def _test_normalize_trade_type():
    # TODO: Implement? (and remove underscore prefix)
    t1 = {"pair": ("XETH", "ZEUR"),
          "type": "sell",
          "vol": 10,
          "cost": 10}
    t1norm = _normalize_trade_type(t1)
    assert t1norm["pair"] == reversed(t1["pair"])


def _calculate_inout_balances(balances, trades):
    for t in trades:
        if t["type"] == "buy":   # Buy asset1 in pair using asset2
            balances[t["pair"][0]] += t["vol"]
            balances[t["pair"][1]] -= t["cost"]
        elif t["type"] == "sell":
            balances[t["pair"][0]] -= t["vol"]
            balances[t["pair"][1]] += t["cost"]

    return balances


def _aggregate_trades(trades):
    def keyfunc(t):
        return tuple(list(t["pair"]) + [t["type"]])

    trades = deepcopy(trades)
    grouped = groupby(sorted(trades, key=keyfunc), key=keyfunc)

    agg_trades = []
    for k, v in grouped:
        t = reduce(_sum_trades, v)
        del t["time"]
        agg_trades.append(t)

    return list(sorted(agg_trades, key=lambda t: t["pair"]))


def _print_trade_header():
    print(f"{'Pair'.ljust(16)}  {'type'.ljust(5)}  {'vol'.ljust(10)}  {'cost_usd'}")


def _print_trade(t):
    print(f"{' / '.join(t['pair']).ljust(16)}  {t['type'].ljust(5)}  {str(round(t['vol'], 3)).ljust(10)}  ${t['cost_usd']}")


def load_all_trades():
    trades_kraken_csv = _load_csv("data_private/kraken-trades.csv")
    trades_kraken = _format_csv_from_kraken(trades_kraken_csv)

    trades_poloniex_csv = _load_csv("data_private/poloniex-trades.csv")
    trades_poloniex = _format_csv_from_poloniex(trades_poloniex_csv)

    return list(sorted(trades_kraken + trades_poloniex, key=lambda t: t["time"]))


def main():
    """Prints a bunch of useful info"""
    trades = load_all_trades()
    trades = _reduce_trades(trades)
    trades = _calc_cost_usd(trades)
    _print_trades(trades)

    print("\n# Cost basis per asset")
    _cost_basis_per_asset(trades)

    for year in range(2015, 2019):
        balances = defaultdict(lambda: 0)  # type: Dict[str, int]
        trades_for_year = _filter_trades_by_time(trades, year)
        _calculate_inout_balances(balances, trades_for_year)
        print(f"\n# Balance diff for {year}")
        for k, v in balances.items():
            print(f"{k:6.6} {v}")

        print(f"\n# Aggregate trades for {year}")
        _print_trade_header()
        for t in _aggregate_trades(trades_for_year):
            _print_trade(t)


if __name__ == "__main__":
    main()
