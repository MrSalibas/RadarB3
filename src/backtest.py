"""
Backtest e ranking dos pares.

Para cada par encontrado:
 - calcula o histórico do ratio;
 - calcula z-score móvel;
 - simula entradas (|z| >= entry) e saídas (|z| <= exit);
 - considera custos, slippage e imposto estimado;
 - mede taxa de reversão e tempo médio até reverter;
 - calcula ganho líquido e ganho em quantidade de ações.

Saída: data/ranking_pares.csv
"""

import os

import numpy as np
import pandas as pd

from src import get_paths
from src.logger import get_logger
from src.market_data import MarketDataProvider
from src.ratio_engine import compute_ratio_series
from src.tax import TaxEstimator

logger = get_logger()

RANKING_COLUMNS = [
    "empresa_base", "ticker_on", "ticker_pn", "numero_sinais", "taxa_reversao",
    "tempo_medio_reversao", "ganho_medio_estimado", "ganho_medio_acoes",
    "custo_medio", "imposto_medio", "score_final", "recomendacao",
]


def _custos_round_trip(notional, config):
    """Custos + slippage de uma operação completa (4 pernas)."""
    sim = config["simulation"]
    n = 4
    custo = n * (float(sim["corretagem"]) + notional * float(sim["emolumentos_pct"]))
    slip = n * (notional * float(sim["slippage_pct"]))
    return custo + slip


def _backtest_pair(ratio, pn_prices, on_prices, config, tax):
    """
    Roda o backtest de um único par a partir da série de ratio + preços.
    Devolve um dicionário com as métricas agregadas.
    """
    strat = config["strategy"]
    long_window = int(strat["long_window"])
    entry = float(strat["entry_zscore"])
    exit_z = float(strat["exit_zscore"])
    capital = float(config["simulation"]["capital_inicial"])

    ratio = pd.Series(ratio).dropna()
    if len(ratio) < long_window + 5:
        return None

    media = ratio.rolling(long_window).mean()
    std = ratio.rolling(long_window).std()
    z = (ratio - media) / std
    z = z.replace([np.inf, -np.inf], np.nan)

    # Alinha preços ao índice do ratio.
    pn = pd.Series(pn_prices).reindex(ratio.index).ffill()
    on = pd.Series(on_prices).reindex(ratio.index).ffill()

    idx = list(ratio.index)
    in_position = False
    side = None
    entry_pos = 0
    entry_ratio = 0.0
    entry_media = 0.0
    entry_price_sell = 0.0

    trades = []          # trades fechados (reverteram)
    sinais_abertos = 0   # quantas entradas houve

    for k in range(long_window, len(idx)):
        zk = z.iloc[k]
        if not np.isfinite(zk):
            continue

        if not in_position:
            if zk >= entry:
                in_position = True
                side = "VENDER_PN_COMPRAR_ON"
                entry_pos = k
                entry_ratio = float(ratio.iloc[k])
                entry_media = float(media.iloc[k])
                entry_price_sell = float(pn.iloc[k])
                sinais_abertos += 1
            elif zk <= -entry:
                in_position = True
                side = "VENDER_ON_COMPRAR_PN"
                entry_pos = k
                entry_ratio = float(ratio.iloc[k])
                entry_media = float(media.iloc[k])
                entry_price_sell = float(on.iloc[k])
                sinais_abertos += 1
        else:
            # Saída quando o z-score volta para a faixa neutra.
            if abs(zk) <= exit_z:
                exit_ratio = float(ratio.iloc[k])
                if entry_media and exit_ratio > 0 and entry_price_sell > 0:
                    if side == "VENDER_PN_COMPRAR_ON":
                        fator = entry_ratio / exit_ratio - 1.0
                    else:
                        fator = exit_ratio / entry_ratio - 1.0

                    qtd = capital / entry_price_sell
                    ganho_acoes_bruto = qtd * max(0.0, fator)
                    ganho_fin_bruto = ganho_acoes_bruto * entry_price_sell

                    custo = _custos_round_trip(capital, config)
                    info_tax = tax.estimate(ganho_fin_bruto, vendas_mes=2 * capital,
                                            operation="swing")
                    imposto = info_tax["imposto_estimado"]

                    ganho_fin_liq = ganho_fin_bruto - custo - imposto
                    ganho_acoes_liq = ganho_fin_liq / entry_price_sell

                    trades.append({
                        "barras": k - entry_pos,
                        "ganho_financeiro": ganho_fin_liq,
                        "ganho_acoes": ganho_acoes_liq,
                        "custo": custo,
                        "imposto": imposto,
                    })
                in_position = False
                side = None

    if sinais_abertos == 0:
        return {
            "numero_sinais": 0,
            "taxa_reversao": 0.0,
            "tempo_medio_reversao": 0.0,
            "ganho_medio_estimado": 0.0,
            "ganho_medio_acoes": 0.0,
            "custo_medio": 0.0,
            "imposto_medio": 0.0,
        }

    fechados = len(trades)
    taxa_reversao = fechados / sinais_abertos
    if fechados > 0:
        tdf = pd.DataFrame(trades)
        tempo_medio = float(tdf["barras"].mean())
        ganho_medio = float(tdf["ganho_financeiro"].mean())
        ganho_medio_acoes = float(tdf["ganho_acoes"].mean())
        custo_medio = float(tdf["custo"].mean())
        imposto_medio = float(tdf["imposto"].mean())
    else:
        tempo_medio = ganho_medio = ganho_medio_acoes = 0.0
        custo_medio = imposto_medio = 0.0

    return {
        "numero_sinais": sinais_abertos,
        "taxa_reversao": round(taxa_reversao, 4),
        "tempo_medio_reversao": round(tempo_medio, 2),
        "ganho_medio_estimado": round(ganho_medio, 2),
        "ganho_medio_acoes": round(ganho_medio_acoes, 2),
        "custo_medio": round(custo_medio, 2),
        "imposto_medio": round(imposto_medio, 2),
    }


def _score_e_recomendacao(m):
    """Calcula o score final e a recomendação textual."""
    if m["numero_sinais"] == 0:
        return 0.0, "SEM SINAIS"

    # Score premia reversão alta e ganho positivo, penaliza demora.
    score = (m["taxa_reversao"] * m["ganho_medio_estimado"]
             / (m["tempo_medio_reversao"] + 1.0))
    score = round(score, 4)

    if m["ganho_medio_estimado"] > 0 and m["taxa_reversao"] >= 0.6 \
            and m["numero_sinais"] >= 2:
        rec = "RECOMENDADO"
    elif m["ganho_medio_estimado"] > 0:
        rec = "NEUTRO"
    else:
        rec = "EVITAR"
    return score, rec


def run_backtest(config, provider=None, pares_df=None):
    """
    Roda o backtest em todos os pares e gera data/ranking_pares.csv.
    Por padrão usa os pares aprovados na liquidez; se não houver, usa todos.
    """
    paths = get_paths(config)
    period = config["data_source"]["historical_period"]
    interval = config["data_source"]["historical_interval"]

    if provider is None:
        provider = MarketDataProvider(cache_dir=paths["cache_dir"])

    tax = TaxEstimator(config)

    if pares_df is None:
        pares_df = _carregar_pares(paths)

    if pares_df is None or pares_df.empty:
        logger.warning("Nenhum par para backtest.")
        pd.DataFrame(columns=RANKING_COLUMNS).to_csv(
            paths["ranking"], index=False, encoding="utf-8")
        return pd.DataFrame(columns=RANKING_COLUMNS)

    linhas = []
    for _, par in pares_df.iterrows():
        ticker_on = str(par["ticker_on"])
        ticker_pn = str(par["ticker_pn"])
        try:
            pn = provider.close_series(ticker_pn, period, interval)
            on = provider.close_series(ticker_on, period, interval)
            ratio = compute_ratio_series(pn, on)
            m = _backtest_pair(ratio, pn, on, config, tax)
            if m is None:
                continue
            score, rec = _score_e_recomendacao(m)
            linhas.append({
                "empresa_base": par.get("empresa_base", ""),
                "ticker_on": ticker_on,
                "ticker_pn": ticker_pn,
                "numero_sinais": m["numero_sinais"],
                "taxa_reversao": m["taxa_reversao"],
                "tempo_medio_reversao": m["tempo_medio_reversao"],
                "ganho_medio_estimado": m["ganho_medio_estimado"],
                "ganho_medio_acoes": m["ganho_medio_acoes"],
                "custo_medio": m["custo_medio"],
                "imposto_medio": m["imposto_medio"],
                "score_final": score,
                "recomendacao": rec,
            })
        except Exception as exc:
            logger.error("Erro no backtest de %s/%s: %s",
                         ticker_on, ticker_pn, exc)

    ranking = pd.DataFrame(linhas, columns=RANKING_COLUMNS)
    if not ranking.empty:
        ranking = ranking.sort_values("score_final", ascending=False).reset_index(
            drop=True)
    ranking.to_csv(paths["ranking"], index=False, encoding="utf-8")
    logger.info("Backtest concluído. Ranking salvo em %s (%d pares).",
                paths["ranking"], len(ranking))
    return ranking


def _carregar_pares(paths):
    """Carrega pares aprovados (se houver) ou todos os pares encontrados."""
    if os.path.exists(paths["pares_filtrados"]):
        df = pd.read_csv(paths["pares_filtrados"])
        if "status" in df.columns:
            aprov = df[df["status"] == "aprovado"]
            if not aprov.empty:
                return aprov.reset_index(drop=True)
        if not df.empty:
            return df
    if os.path.exists(paths["pares"]):
        return pd.read_csv(paths["pares"])
    return pd.DataFrame()


if __name__ == "__main__":
    from src import load_config

    run_backtest(load_config())
