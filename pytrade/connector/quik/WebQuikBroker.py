import json
import logging
import random

from connector.quik.MsgConverter import MsgConverter
from connector.quik.MsgId import MsgId
from connector.quik.WebQuikConnector import WebQuikConnector


class WebQuikBroker:
    """
    Broker facade for QuikConnector. Holds account info, can  make orders.
    Supports only simple buy/sell at the moment
    Todo: add different types of orders: stop, market ...
    """

#    def __init__(self, connector: WebQuikConnector, client_code, trade_account):
    def __init__(self, config):
        self._logger = logging.getLogger(__name__)

        self.client_code = config['client_code']
        self.trade_account = config['trade_account']

        # 21001:"Заявки",
        # 21003:"Сделки",
        # 21004:"Денежн.лимиты"
        # 21005:"Бумаж.лимиты",
        self.callbacks = {
            MsgId.ORDERS: self.on_orders,
            MsgId.TRADES: self.on_trades,
            MsgId.TRADE_ACCOUNTS: self.on_trade_accounts,
            MsgId.TRADES_FX: self.on_trades_fx,
            MsgId.MONEY_LIMITS: self.on_money_limits,
            MsgId.STOCK_LIMITS: self.on_stock_limits,
            MsgId.LIMITS: self.on_limits,
            MsgId.LIMIT_HAS_RECEIVED: self.on_limit_received,

            # Reply messages
            MsgId.SERVER_MSG: self.on_reply,
            MsgId.REMOVE_STOP_ORDER_REPLY: self.on_reply,
            MsgId.REMOVE_ORDER_REPLY: self.on_reply,
            MsgId.ORDER_REPLY: self.on_reply,
            MsgId.STOP_ORDER_REPLY: self.on_reply,
            MsgId.LINKED_STOP_ORDER_REPLY: self.on_reply,
            MsgId.FX_ORDER_REPLY: self.on_reply,
            MsgId.CONDITIONAL_STOP_ORDER_REPLY: self.on_reply,
            MsgId.TRANS_REPLY: self.on_reply,
            MsgId.HEARTBEAT: self.on_heartbeat
        }
        # Create web quik connector
        self._connector = WebQuikConnector(config)
        self._connector.subscribe(self.callbacks)
        self._broker_subscribers = []
        # accounts dictionary class_code -> account info
        # Usually different accounts for securities, futures, forex ??

        self._logger.info("Initialized")

    def run(self):
        self._connector.run_once()

    def on_order_answer(self, msg):
        self._logger.info(f"Got msg: {msg}")

    def on_trades_fx(self, msg):
        self._logger.debug(f"On trades fx. msg={msg}")

    def on_trade_accounts(self, msg):
        # Information about my account. Usually one for stocks, one for futures.
        # {"msgid":21022,"trdacc":"NL0011100043","firmid":"NC0011100000","classList":["QJSIM"],"mainMarginClasses":["QJSIM"],"limitsInLots":0,"limitKinds":["0","1","2"]}
        # Just push the message to rabbitmq
        self._logger.debug(f"On trade accounts. msg={msg}")
        for s in self._broker_subscribers:
            s.on_trade_accounts(msg)

    def on_orders(self, msg):
        # Information about my orders
        self._logger.debug(f"On orders. msg={msg}")
        order = MsgConverter.msg2order(msg)
        for s in self._broker_subscribers:
            s.on_orders(order)

    def on_trades(self, msg):
        self._logger.debug(f"On trades. msg={msg}")
        for s in self._broker_subscribers:
            s.on_trades(msg)

    def on_limits(self, msg):
        self._logger.debug(f"On limits. msg={msg}")
        for s in self._broker_subscribers:
            s.on_limits(msg)

    def on_money_limits(self, msg):
        self._logger.debug(f"On money limits. msg={msg}")
        for s in self._broker_subscribers:
            s.on_money_limits(msg)

    def on_stock_limits(self, msg):
        self._logger.debug(f"On stock limits. msg={msg}")
        for s in self._broker_subscribers:
            s.on_stock_limits(msg)

    def on_limit_received(self, msg):
        self._logger.debug(f"Limit has received. msg={msg}")
        for s in self._broker_subscribers:
            s.on_limit_received(msg)

    def subscribe_broker(self, subscriber):
        """
        Subscribe to broker events - trades, account etc.
        :param subscriber broker class, inherited from broker'
        """
        # Register given feed callback
        self._broker_subscribers.append(subscriber)

    def on_reply(self, msg: str):
        """
        Responce to my order
        ToDo: add order to history if successful
        """
        # Successful transaction should look like this:
        # {"msgid":21009,"request":1,"status":3,"ordernum":5736932911,"datetime":"2021-03-08 22:05:35","text":"(161) Заявка N 5736932911 зарегистрирована. Удовлетворено 1"}
        self._logger.debug(f"Got msg: {msg}")
        for s in self._broker_subscribers:
            s.on_reply(msg)

    def get_order_msg(self, class_code: str, sec_code: str, is_buy: bool, price: float, quantity: int) -> str:
        """
        Prepares buy or sell order json for quik
        """
        msgdict = {
            "transid": random.randint(1, 2 ^ 64),
            "msgid": MsgId.ORDER,
            "ccode": class_code,
            "scode": sec_code,
            "sell": 0 if is_buy else 1,
            "quantity": quantity,
            "clientcode": self.client_code,
            "account": self.trade_account,
            "price": price
        }
        msg = json.dumps(msgdict)
        return msg

    def send_raw_msg(self, msg):
        self._logger.debug(f"Sending raw msg: {msg}")
        self._connector.send(msg)

    def buy(self, class_code, sec_code, price, quantity):
        """
        Send buy order to broker
        """
        msg = self.get_order_msg(class_code=class_code, sec_code=sec_code, is_buy=True, price=price, quantity=quantity)
        self._logger.info(f"Sending buy message {msg}")
        self._connector.send(msg)

    def sell(self, class_code, sec_code, price, quantity):
        """
        Send sell order to broker
        """
        msg = self.get_order_msg(class_code=class_code, sec_code=sec_code, is_buy=False, price=price, quantity=quantity)
        self._logger.info(f"Sending sell message {msg}")
        self._connector.send(msg)

    def kill_all_orders(self):
        """
        Kill all orders in trade system
        """
        raise NotImplementedError

    def on_heartbeat(self, *args):
        """
        Heartbeating reaction
        """
        for subscriber in self._broker_subscribers:
            subscriber.on_heartbeat()
