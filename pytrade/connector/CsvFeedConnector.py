import collections
import logging
from datetime import datetime

import pandas as pd

from model.feed.Asset import Asset
from model.feed.Level2 import Level2
from model.feed.Level2Item import Level2Item
from model.feed.Ohlcv import Ohlcv
from model.feed.Quote import Quote


class CsvFeedConnector:
    """
    Reads the data from csv
    """

    def __init__(self, config):
        self._logger = logging.getLogger(__name__)
        self._logger.info("Init " + __name__)
        self.candles_path = config["csv_feed_candles"]
        self.quotes_path = config["csv_feed_quotes"]
        self.level2_path = config["csv_feed_level2"]

        # Subscribers for data feed. {(class_code, sec_code): callback_func}
        self._feed_subscribers = collections.defaultdict(list)
        self.candles, self.quotes, self.level2 = None, None, None

        # self.level2 = self.level2[~self.level2.index.duplicated(keep='first')].sort_index()

    def subscribe_feed(self, asset, subscriber):
        self._feed_subscribers[asset].append(subscriber)

    def read_csvs(self):
        # Read candles
        self._logger.info(f"Read candles from {self.candles_path}")
        self.candles = pd.read_csv(self.candles_path, parse_dates=True,
                                   names=['datetime', 'ticker', 'open', 'high', 'low', 'close', 'volume'],
                                   index_col=['datetime'])
        self.candles = self.candles[~self.candles.index.duplicated(keep='first')].sort_index()
        self._logger.info(f"Read quotes from {self.quotes_path}")

        # Read quotes
        self.quotes = pd.read_csv(self.quotes_path, parse_dates=True,
                                  names=['datetime', 'ticker', 'bid', 'ask', 'last', 'last_change'],
                                  index_col=['datetime'])
        self.quotes = self.quotes[~self.quotes.index.duplicated(keep='first')].sort_index()

        # Read level2
        self._logger.info(f"Read level2 from {self.level2_path}")
        self.level2 = pd.read_csv(self.level2_path, parse_dates=['datetime'],
                                  names=['datetime', 'ticker', 'price', 'bid_vol', 'ask_vol'])

    def run(self):
        # Read csvs to dataframes
        self.read_csvs()
        self._logger.info("Producing the data from csvs")
        # Merge all datetimes
        ticks = pd.concat([self.quotes.index.to_series(),
                           self.candles.index.to_series(),
                           self.level2['datetime']]).sort_values()
        # Process each time tick and produce candle, level2 or quote
        for dt, tick in ticks.iteritems():
            # Produce next quote
            if tick in self.quotes.index:
                quote_dict = self.quotes.iloc[self.quotes.index.get_loc(dt)].to_dict()
                quote = self._quote_of(dt, quote_dict)
                for subscriber in self._feed_subscribers[quote.asset] + self._feed_subscribers[Asset.any_asset()]:
                    subscriber.on_quote(quote)
            # Produce next candle
            if tick in self.candles.index:
                candle_dict = self.candles.iloc[self.candles.index.get_loc(dt)].to_dict()
                candle = self._ohlcv_of(dt, candle_dict)
                for subscriber in self._feed_subscribers[candle.asset] + self._feed_subscribers[Asset.any_asset()]:
                    subscriber.on_candle(candle)
            # Produce next level2
            level2_items = self.level2[self.level2['datetime'] == tick].to_numpy()
            if level2_items.size > 0:
                level2 = CsvFeedConnector._level2_of(level2_items)
                for subscriber in self._feed_subscribers[level2.asset] + self._feed_subscribers[Asset.any_asset()]:
                    subscriber.on_level2(level2)

    @staticmethod
    def _level2_of(data) -> Level2:
        dt = pd.to_datetime(data[0][0])
        asset = Asset.of(data[0][1])
        # Price, bid, ask
        level2_items = [Level2Item(data_item[2], data_item[3], data_item[4]) for data_item in data]
        return Level2(dt, asset, level2_items)


    @staticmethod
    def _ohlcv_of(dt: datetime, data: dict) -> Ohlcv:
        return Ohlcv(dt=dt,
                     asset=Asset.of(data['ticker']),
                     o=data['open'],
                     h=data['high'],
                     l=data['low'],
                     c=data['close'],
                     v=data['volume']
                     )

    @staticmethod
    def _quote_of(dt: datetime, data: dict) -> Quote:
        return Quote(
            dt=dt,
            asset=Asset.of(data['ticker']),
            bid=data['bid'],
            ask=data['ask'],
            last=data['last'],
            last_change=data['last_change'])

    #
    # @staticmethod
    # def _level2_of(dt, quik_level2: dict):
    #     # {'22806': {'b': 234, 's': 0, 'by': 0, 'sy': 0}
    #     level2 = Level2(dt)
    #     for key in quik_level2.keys():
    #         # Parse price, bid volume, ask volume
    #         price = float(key)
    #         bid_vol = quik_level2[key]['b']
    #         if bid_vol == 0:
    #             bid_vol = None
    #         ask_vol = quik_level2[key]['s']
    #         if ask_vol == 0:
    #             ask_vol = None
    #         # Add price, bid vol, ask vol to level 2 prices
    #         level2.items.add(Level2Item(price, bid_vol, ask_vol))
    #
    #     return level2

    def preprocess(self):
        # Pivot to create feature columns of level2 prices and volumes
        df = self.level2
        df["num"] = df.groupby("datetime").cumcount() + 1
        price_pivoted = df.pivot(index="datetime", columns="num", values="price")
        price_pivoted.columns = "price" + price_pivoted.columns.astype(str)
        price_pivoted["base"] = (price_pivoted["price10"] + price_pivoted["price11"]) / 2
        for n in range(1, len([c for c in price_pivoted.columns if c.startswith("price")]) + 1):
            col = "price" + str(n)
            price_pivoted[col] = price_pivoted[col] - price_pivoted["base"]

        bid_vol_pivoted = df.pivot(index="datetime", columns="num", values="bid_vol")
        bid_vol_pivoted.columns = "bid_vol" + bid_vol_pivoted.columns.astype(str)

        ask_vol_pivoted = df.pivot(index="datetime", columns="num", values="ask_vol")
        ask_vol_pivoted.columns = "as_vol" + ask_vol_pivoted.columns.astype(str)

        pivoted = price_pivoted.join(bid_vol_pivoted).join(ask_vol_pivoted)
        # Form level2

        # Reverse, rolling and reverse back to calc future max and min
        # Add future min/max
        predict_window = "2min"
        self.quotes["nextmax"] = self.quotes["ask"][::-1].rolling(predict_window).max()[::-1]
        self.quotes["nextmin"] = self.quotes["ask"][::-1].rolling(predict_window).min()[::-1]

        # Add last closest
        m = pd.merge_asof(self.quotes, self.candles, left_index=True, right_index=True, tolerance=pd.Timedelta("1 min"))
        print(m.head(10))

# # # Test code todo: remove
# base_dir = "../data/"
# csv_feed = CsvFeedConnector(base_dir + "QJSIM_SBER_candles_2021-11-07.csv",
#                             base_dir + "QJSIM_SBER_quotes_2021-11-07.csv",
#                             base_dir + "QJSIM_SBER_level2_2021-11-07.csv")
# csv_feed.run()
