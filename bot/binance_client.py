from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bot.config import BotConfig


class BinanceDataClient:
    BASE_URL = "https://api.binance.com"

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies.clear()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._cache: dict[tuple[str, str, int], pd.DataFrame] = {}

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        cache_key = (symbol.upper(), interval, limit)
        try:
            if limit <= 1000:
                df = self._get_klines_page(symbol, interval, limit)
            else:
                remaining = limit
                end_time = None
                pages = []
                while remaining > 0:
                    page_limit = min(remaining, 1000)
                    page = self._get_klines_page(
                        symbol, interval, page_limit, end_time=end_time
                    )
                    if page.empty:
                        break
                    pages.append(page)
                    first_open_ms = int(page["open_time"].iloc[0].timestamp() * 1000)
                    end_time = first_open_ms - 1
                    remaining -= len(page)
                    if len(page) < page_limit:
                        break

                if not pages:
                    raise RuntimeError("No kline data returned by Binance.")

                df = pd.concat(pages, ignore_index=True)
                df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time")
                df = df.tail(limit).reset_index(drop=True)
        except requests.exceptions.RequestException:
            cached = self._cache.get(cache_key)
            if cached is not None and not cached.empty:
                return cached.copy()
            raise

        self._cache[cache_key] = df.copy()
        return df

    def _get_klines_page(
        self,
        symbol: str,
        interval: str,
        limit: int,
        end_time: int | None = None,
    ) -> pd.DataFrame:
        url = f"{self.BASE_URL}/api/v3/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time

        import time
        import logging

        response = None
        for attempt in range(4):
            try:
                response = self.session.get(url, params=params, timeout=20)
                response.raise_for_status()
                break
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt == 3:
                    raise
                logging.warning(f"Conexión fallida al descargar klines para {symbol}. Reintentando en {attempt + 1}s... Error: {e}")
                time.sleep(attempt + 1)

        if response is None:
            raise RuntimeError("No kline data returned by Binance.")

        raw = response.json()
        if not raw:
            raise RuntimeError("No kline data returned by Binance.")

        cols = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ]
        df = pd.DataFrame(raw, columns=cols)
        numeric_cols = ["open", "high", "low", "close", "volume"]
        for c in numeric_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        df = df.dropna(subset=numeric_cols).reset_index(drop=True)
        return df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]


@dataclass(frozen=True)
class SymbolFilters:
    min_qty: float = 0.0
    max_qty: float = 0.0
    step_size: float = 0.0
    min_notional: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0
    tick_size: float = 0.0


@dataclass
class BinanceExecutionClient:
    cfg: BotConfig
    QUOTE_ASSET_SUFFIXES = (
        "USDT",
        "USDC",
        "FDUSD",
        "TUSD",
        "BUSD",
        "BTC",
        "ETH",
        "BNB",
        "EUR",
        "TRY",
        "BRL",
    )

    def __post_init__(self) -> None:
        try:
            from binance.client import Client
        except ImportError as exc:
            raise RuntimeError(
                "python-binance is required for live trading. Install requirements first."
            ) from exc

        self.client = Client(
            self.cfg.binance_api_key,
            self.cfg.binance_api_secret,
            testnet=self.cfg.use_testnet,
            requests_params={'timeout': 20}
        )
        self.client.REQUEST_RECVWINDOW = 60000
        self.sync_clock()

    def sync_clock(self) -> None:
        import logging
        try:
            import time
            res = self.client.get_server_time()
            server_time = res['serverTime']
            local_time = int(time.time() * 1000)
            self.client.timestamp_offset = server_time - local_time
            logging.info(f"Reloj sincronizado con Binance. Offset: {self.client.timestamp_offset}ms.")
        except Exception as e:
            logging.warning(f"No se pudo sincronizar el offset del reloj con Binance: {e}")

    def _call_signed(self, fn, *args, **kwargs) -> Any:
        """Ejecuta una llamada API firmada con captura de error -1021 (recvWindow) y re-sincronización instantánea."""
        import logging
        import time
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            err_msg = str(e)
            if "-1021" in err_msg or "recvWindow" in err_msg:
                logging.warning("Desfase de reloj detectado (-1021 / recvWindow). Re-sincronizando con Binance y reintentando...")
                self.sync_clock()
                time.sleep(0.2)
                return fn(*args, **kwargs)
            raise

    def create_market_buy(self, symbol: str, quantity: float) -> dict[str, Any]:
        return self._call_signed(
            self.client.create_order,
            symbol=symbol.upper(), side="BUY", type="MARKET", quantity=quantity
        )

    def create_market_sell(self, symbol: str, quantity: float) -> dict[str, Any]:
        return self._call_signed(
            self.client.create_order,
            symbol=symbol.upper(), side="SELL", type="MARKET", quantity=quantity
        )

    def normalize_price(self, price: float, filters: SymbolFilters) -> float:
        p = max(price, 0.0)
        if filters.min_price > 0:
            p = max(p, filters.min_price)
        if filters.max_price > 0:
            p = min(p, filters.max_price)
        if filters.tick_size > 0:
            p = self.floor_to_step(p, filters.tick_size)
        return max(p, 0.0)

    def format_price(self, price: float, filters: SymbolFilters) -> str:
        normalized = self.normalize_price(price, filters)
        tick_str = f"{filters.tick_size:.10f}".rstrip('0')
        if '.' in tick_str:
            decimals = len(tick_str.split('.')[1])
        else:
            decimals = 0
        return f"{normalized:.{decimals}f}"

    def format_quantity(self, quantity: float, filters: SymbolFilters) -> str:
        normalized = self.normalize_quantity(quantity, filters)
        step_str = f"{filters.step_size:.10f}".rstrip('0')
        if '.' in step_str:
            decimals = len(step_str.split('.')[1])
        else:
            decimals = 0
        return f"{normalized:.{decimals}f}"

    def create_stop_loss_limit(self, symbol: str, quantity: float, stop_price: float, limit_price: float, filters: SymbolFilters) -> dict[str, Any]:
        qty_str = self.format_quantity(quantity, filters)
        stop_str = self.format_price(stop_price, filters)
        limit_str = self.format_price(limit_price, filters)
        return self._call_signed(
            self.client.create_order,
            symbol=symbol.upper(),
            side="SELL",
            type="STOP_LOSS_LIMIT",
            timeInForce="GTC",
            quantity=qty_str,
            price=limit_str,
            stopPrice=stop_str
        )

    def create_take_profit_limit(self, symbol: str, quantity: float, stop_price: float, limit_price: float, filters: SymbolFilters) -> dict[str, Any]:
        qty_str = self.format_quantity(quantity, filters)
        stop_str = self.format_price(stop_price, filters)
        limit_str = self.format_price(limit_price, filters)
        return self._call_signed(
            self.client.create_order,
            symbol=symbol.upper(),
            side="SELL",
            type="TAKE_PROFIT_LIMIT",
            timeInForce="GTC",
            quantity=qty_str,
            price=limit_str,
            stopPrice=stop_str
        )

    def cancel_order(self, symbol: str, order_id: str | int) -> dict[str, Any]:
        return self._call_signed(
            self.client.cancel_order,
            symbol=symbol.upper(),
            orderId=str(order_id)
        )

    def get_order_status(self, symbol: str, order_id: str | int) -> dict[str, Any]:
        return self._call_signed(
            self.client.get_order,
            symbol=symbol.upper(),
            orderId=str(order_id)
        )

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return self._call_signed(self.client.get_open_orders, symbol=symbol.upper())

    @classmethod
    def split_symbol(cls, symbol: str) -> tuple[str, str]:
        upper = symbol.upper()
        for quote in cls.QUOTE_ASSET_SUFFIXES:
            if upper.endswith(quote) and len(upper) > len(quote):
                return upper[: -len(quote)], quote
        raise ValueError(f"Could not infer base/quote assets from symbol {symbol!r}.")

    def get_asset_balance_values(self, asset: str) -> tuple[float, float]:
        try:
            payload = self._call_signed(self.client.get_asset_balance, asset=asset.upper())
            if not payload:
                return 0.0, 0.0
            free = self._to_float(payload.get("free"), 0.0)
            locked = self._to_float(payload.get("locked"), 0.0)
            return free, locked
        except Exception as e:
            import logging
            logging.error(f"Error al consultar balance de {asset}: {e}")
            return 0.0, 0.0

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def floor_to_step(value: float, step: float) -> float:
        if step <= 0:
            return max(value, 0.0)
        d_value = Decimal(str(max(value, 0.0)))
        d_step = Decimal(str(step))
        return float((d_value // d_step) * d_step)

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        info = self.client.get_symbol_info(symbol.upper())
        if not info:
            raise RuntimeError(f"Could not fetch symbol info for {symbol.upper()}.")

        filter_map = {f.get("filterType"): f for f in info.get("filters", [])}
        lot_size = filter_map.get("LOT_SIZE", {})
        price_filter = filter_map.get("PRICE_FILTER", {})
        min_notional_filter = filter_map.get("MIN_NOTIONAL", {})
        notional_filter = filter_map.get("NOTIONAL", {})

        min_notional = self._to_float(min_notional_filter.get("minNotional"), 0.0)
        if min_notional <= 0:
            min_notional = self._to_float(notional_filter.get("minNotional"), 0.0)

        return SymbolFilters(
            min_qty=self._to_float(lot_size.get("minQty"), 0.0),
            max_qty=self._to_float(lot_size.get("maxQty"), 0.0),
            step_size=self._to_float(lot_size.get("stepSize"), 0.0),
            min_notional=min_notional,
            min_price=self._to_float(price_filter.get("minPrice"), 0.0),
            max_price=self._to_float(price_filter.get("maxPrice"), 0.0),
            tick_size=self._to_float(price_filter.get("tickSize"), 0.0),
        )

    def normalize_quantity(self, quantity: float, filters: SymbolFilters) -> float:
        qty = max(quantity, 0.0)
        if filters.max_qty > 0:
            qty = min(qty, filters.max_qty)
        if filters.step_size > 0:
            qty = self.floor_to_step(qty, filters.step_size)
        return max(qty, 0.0)

    @staticmethod
    def quantity_is_valid(quantity: float, filters: SymbolFilters) -> bool:
        if quantity <= 0:
            return False
        if filters.min_qty > 0 and quantity < filters.min_qty:
            return False
        if filters.max_qty > 0 and quantity > filters.max_qty:
            return False
        return True

    @staticmethod
    def notional_is_valid(quantity: float, price: float, filters: SymbolFilters) -> bool:
        if quantity <= 0 or price <= 0:
            return False
        if filters.min_notional > 0 and (quantity * price) < filters.min_notional:
            return False
        return True
