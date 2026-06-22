"""
Filtro de liquidez dos pares.

Para cada ativo (ON e PN) calcula:
 - volume financeiro médio diário (R$);
 - quantidade média negociada;
 - número de dias com negociação;
 - número de pontos de dados;
 - spread estimado (proxy).

Exclui o par se QUALQUER uma das pontas tiver liquidez ruim.

Saída: data/pares_filtrados_liquidez.csv
"""

import numpy as np
import pandas as pd

from src import get_paths
from src.logger import get_logger
from src.market_data import MarketDataProvider

logger = get_logger()


def _metrics_for_ticker(provider, ticker, period, interval):
    """Calcula as métricas de liquidez de um ticker. Devolve dict."""
    df = provider.historical(ticker, period=period, interval=interval)

    if df is None or df.empty or "Close" not in df.columns:
        return {
            "ok": False,
            "avg_daily_volume_brl": 0.0,
            "avg_volume": 0.0,
            "trading_days": 0,
            "data_points": 0,
            "spread_pct": None,
            "motivo": "sem dados",
        }

    close = pd.to_numeric(df["Close"], errors="coerce")
    volume = pd.to_numeric(df.get("Volume", pd.Series(dtype="float64")),
                           errors="coerce")
    high = pd.to_numeric(df.get("High", close), errors="coerce")
    low = pd.to_numeric(df.get("Low", close), errors="coerce")

    fin_volume = (close * volume).dropna()
    avg_daily_volume_brl = float(fin_volume.mean()) if not fin_volume.empty else 0.0
    avg_volume = float(volume.dropna().mean()) if not volume.dropna().empty else 0.0
    trading_days = int((volume.fillna(0) > 0).sum())
    data_points = int(close.dropna().shape[0])

    # Proxy de spread: amplitude diária média (High-Low)/Close.
    # Atenção: NÃO é o spread bid/ask real (yfinance não fornece).
    with np.errstate(divide="ignore", invalid="ignore"):
        ampl = ((high - low) / close).replace([np.inf, -np.inf], np.nan).dropna()
    spread_pct = float(ampl.mean() * 100.0) if not ampl.empty else None

    return {
        "ok": True,
        "avg_daily_volume_brl": avg_daily_volume_brl,
        "avg_volume": avg_volume,
        "trading_days": trading_days,
        "data_points": data_points,
        "spread_pct": spread_pct,
        "motivo": "",
    }


def _check_side(metrics, liq_cfg):
    """Verifica se uma ponta passa nos critérios. Devolve (passou, motivo)."""
    if not metrics["ok"]:
        return False, metrics["motivo"]

    if metrics["avg_daily_volume_brl"] < liq_cfg["min_avg_daily_volume_brl"]:
        return False, "volume financeiro abaixo do mínimo"
    if metrics["trading_days"] < liq_cfg["min_trading_days"]:
        return False, "poucos dias de negociação"
    if metrics["data_points"] < liq_cfg["min_data_points"]:
        return False, "poucos pontos de dados"

    # Filtro de spread é opcional (proxy não é confiável no yfinance).
    if liq_cfg.get("apply_spread_filter", False):
        spread = metrics["spread_pct"]
        if spread is not None and spread > liq_cfg["max_allowed_spread_pct"]:
            return False, "spread estimado acima do máximo"

    return True, ""


def filter_pairs(config, provider=None):
    """
    Filtra data/pares_on_pn.csv por liquidez e salva
    data/pares_filtrados_liquidez.csv.
    """
    paths = get_paths(config)
    liq_cfg = config["liquidity"]
    period = config["data_source"]["historical_period"]
    interval = config["data_source"]["historical_interval"]

    if provider is None:
        provider = MarketDataProvider(cache_dir=paths["cache_dir"])

    try:
        pares = pd.read_csv(paths["pares"])
    except Exception as exc:
        logger.error("Não foi possível ler os pares (%s): %s", paths["pares"], exc)
        logger.error("Rode primeiro a opção 'Encontrar pares ON/PN'.")
        return pd.DataFrame()

    if pares.empty:
        logger.warning("Nenhum par para filtrar.")
        pares.to_csv(paths["pares_filtrados"], index=False, encoding="utf-8")
        return pares

    # Coleta métricas uma vez por ticker (evita baixar duas vezes o mesmo).
    cache_metricas = {}

    def metrics(ticker):
        if ticker not in cache_metricas:
            cache_metricas[ticker] = _metrics_for_ticker(
                provider, ticker, period, interval)
        return cache_metricas[ticker]

    linhas = []
    for _, par in pares.iterrows():
        m_on = metrics(par["ticker_on"])
        m_pn = metrics(par["ticker_pn"])

        ok_on, motivo_on = _check_side(m_on, liq_cfg)
        ok_pn, motivo_pn = _check_side(m_pn, liq_cfg)

        aprovado = ok_on and ok_pn
        motivo = ""
        if not aprovado:
            partes = []
            if not ok_on:
                partes.append(f"ON({par['ticker_on']}): {motivo_on}")
            if not ok_pn:
                partes.append(f"PN({par['ticker_pn']}): {motivo_pn}")
            motivo = "; ".join(partes)

        linha = par.to_dict()
        linha.update({
            "status": "aprovado" if aprovado else "reprovado",
            "motivo_exclusao": motivo,
            "vol_brl_on": round(m_on["avg_daily_volume_brl"], 2),
            "vol_brl_pn": round(m_pn["avg_daily_volume_brl"], 2),
            "dias_on": m_on["trading_days"],
            "dias_pn": m_pn["trading_days"],
            "pontos_on": m_on["data_points"],
            "pontos_pn": m_pn["data_points"],
            "spread_on_pct": m_on["spread_pct"],
            "spread_pn_pct": m_pn["spread_pct"],
        })
        linhas.append(linha)

    resultado = pd.DataFrame(linhas)
    resultado.to_csv(paths["pares_filtrados"], index=False, encoding="utf-8")

    aprovados = int((resultado["status"] == "aprovado").sum())
    logger.info("Filtro de liquidez: %d aprovados de %d pares. Salvo em %s.",
                aprovados, len(resultado), paths["pares_filtrados"])
    return resultado


def get_approved_pairs(config):
    """Utilitário: devolve só os pares aprovados na liquidez."""
    paths = get_paths(config)
    try:
        df = pd.read_csv(paths["pares_filtrados"])
    except Exception:
        return pd.DataFrame()
    if "status" in df.columns:
        return df[df["status"] == "aprovado"].reset_index(drop=True)
    return df


if __name__ == "__main__":
    from src import load_config

    filter_pairs(load_config())
