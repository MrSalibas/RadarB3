"""
Coleta de dados de mercado.

Fonte inicial: Yahoo Finance via biblioteca yfinance.
 - Ao buscar, adiciona ".SA" ao ticker (ITSA3 -> ITSA3.SA).
 - Salva cache em CSV dentro de data/cache/.
 - Trata erro de conexão e ativo sem dados.
 - NUNCA quebra o programa inteiro se um ticker falhar (devolve vazio/None).
 - A camada está isolada para permitir troca futura da fonte de dados.

Funções principais:
 - get_historical_data(ticker, period, interval)
 - get_intraday_data(ticker, period, interval)
 - get_last_price(ticker)
 - get_cached_or_download(ticker, period, interval)
 - get_close_series(ticker, period, interval)  (utilitário)
"""

import os
import time

import pandas as pd

from src import PROJECT_ROOT
from src.logger import get_logger

logger = get_logger()

# Importa yfinance de forma defensiva: se não estiver instalado, o programa
# continua de pé e apenas registra o problema.
try:
    import yfinance as yf

    YFINANCE_OK = True
except Exception as exc:  # pragma: no cover - depende do ambiente
    YFINANCE_OK = False
    logger.error("yfinance não disponível: %s", exc)

# Diretório de cache padrão.
DEFAULT_CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "cache")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def to_yahoo(ticker):
    """Converte um ticker B3 para o formato do Yahoo Finance (sufixo .SA)."""
    ticker = str(ticker).strip().upper()
    if ticker.endswith(".SA"):
        return ticker
    return ticker + ".SA"


def _cache_path(ticker, period, interval, cache_dir):
    os.makedirs(cache_dir, exist_ok=True)
    safe = to_yahoo(ticker).replace(".", "_")
    name = f"{safe}__{period}__{interval}.csv"
    return os.path.join(cache_dir, name)


def _cache_is_fresh(path, interval):
    """
    Define se o cache ainda vale.
     - Intervalos intradiários (m/h): validade curta (15 min).
     - Intervalos diários ou maiores: validade de 6 horas.
    """
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    interval = str(interval).lower()
    if interval.endswith("m") or interval.endswith("h"):
        return age < 15 * 60
    return age < 6 * 60 * 60


def _normalize_columns(df):
    """Achata MultiIndex e garante colunas padrão Open/High/Low/Close/Volume."""
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance às vezes devolve colunas multinível mesmo com 1 ticker.
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=lambda c: str(c).strip())
    return df


# ---------------------------------------------------------------------------
# Download bruto
# ---------------------------------------------------------------------------
def _download(ticker, period, interval):
    """Baixa do Yahoo Finance. Devolve DataFrame (vazio em caso de falha)."""
    if not YFINANCE_OK:
        return pd.DataFrame()

    yahoo_ticker = to_yahoo(ticker)
    try:
        df = yf.download(
            yahoo_ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        df = _normalize_columns(df)
        if df.empty:
            logger.warning("Sem dados para %s (period=%s, interval=%s)",
                           yahoo_ticker, period, interval)
        return df
    except Exception as exc:
        logger.error("Falha ao baixar %s: %s", yahoo_ticker, exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def get_cached_or_download(ticker, period="6mo", interval="1d",
                           cache_dir=DEFAULT_CACHE_DIR):
    """Usa o cache em CSV se estiver fresco; caso contrário baixa e salva."""
    path = _cache_path(ticker, period, interval, cache_dir)

    if _cache_is_fresh(path, interval):
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if not df.empty:
                return _normalize_columns(df)
        except Exception as exc:
            logger.warning("Cache inválido para %s (%s). Rebaixando...", ticker, exc)

    df = _download(ticker, period, interval)
    if not df.empty:
        try:
            df.to_csv(path)
        except Exception as exc:
            logger.warning("Não foi possível salvar cache de %s: %s", ticker, exc)
    return df


def get_historical_data(ticker, period="6mo", interval="1d",
                        cache_dir=DEFAULT_CACHE_DIR):
    """Dados históricos (diários por padrão)."""
    return get_cached_or_download(ticker, period, interval, cache_dir)


def get_intraday_data(ticker, period="5d", interval="5m",
                      cache_dir=DEFAULT_CACHE_DIR):
    """Dados intradiários."""
    return get_cached_or_download(ticker, period, interval, cache_dir)


def get_close_series(ticker, period="6mo", interval="1d",
                     cache_dir=DEFAULT_CACHE_DIR):
    """Devolve apenas a série de preços de fechamento (pd.Series)."""
    df = get_cached_or_download(ticker, period, interval, cache_dir)
    if df.empty or "Close" not in df.columns:
        return pd.Series(dtype="float64", name=str(ticker))
    serie = pd.to_numeric(df["Close"], errors="coerce").dropna()
    serie.name = str(ticker)
    return serie


def get_last_price(ticker):
    """
    Último preço aproximado.
    Tenta intradiário (1m); se falhar, usa o último fechamento diário.
    Devolve None se não houver dado.
    """
    if not YFINANCE_OK:
        return None

    yahoo_ticker = to_yahoo(ticker)
    try:
        t = yf.Ticker(yahoo_ticker)
        intraday = t.history(period="1d", interval="1m")
        intraday = _normalize_columns(intraday)
        if not intraday.empty and "Close" in intraday.columns:
            valid = pd.to_numeric(intraday["Close"], errors="coerce").dropna()
            if not valid.empty:
                return float(valid.iloc[-1])

        daily = t.history(period="5d", interval="1d")
        daily = _normalize_columns(daily)
        if not daily.empty and "Close" in daily.columns:
            valid = pd.to_numeric(daily["Close"], errors="coerce").dropna()
            if not valid.empty:
                return float(valid.iloc[-1])
    except Exception as exc:
        logger.error("Falha ao obter último preço de %s: %s", yahoo_ticker, exc)

    return None


# ---------------------------------------------------------------------------
# Camada de abstração (para trocar de fonte no futuro)
# ---------------------------------------------------------------------------
class MarketDataProvider:
    """
    Wrapper orientado a objeto sobre as funções acima.
    Permite, no futuro, criar outras implementações (ex.: brapi, MetaTrader)
    sem mudar o resto do sistema.
    """

    def __init__(self, cache_dir=DEFAULT_CACHE_DIR):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    def historical(self, ticker, period="6mo", interval="1d"):
        return get_historical_data(ticker, period, interval, self.cache_dir)

    def intraday(self, ticker, period="5d", interval="5m"):
        return get_intraday_data(ticker, period, interval, self.cache_dir)

    def close_series(self, ticker, period="6mo", interval="1d"):
        return get_close_series(ticker, period, interval, self.cache_dir)

    def last_price(self, ticker):
        return get_last_price(ticker)
