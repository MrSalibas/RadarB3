"""
Motor de cálculo do ratio entre PN e ON.

Para cada par válido:
    ratio = preco_PN / preco_ON

Calcula:
 - média móvel curta;
 - média móvel longa;
 - desvio padrão;
 - z-score;
 - banda superior / inferior;
 - distância percentual da média.

Fórmula do z-score:
    z_score = (ratio_atual - media_movel) / desvio_padrao
"""

import numpy as np
import pandas as pd


def compute_ratio_series(pn_prices, on_prices):
    """
    Recebe duas séries de preços (PN e ON) e devolve a série do ratio PN/ON,
    já alinhada por data e sem valores nulos.
    """
    if pn_prices is None or on_prices is None:
        return pd.Series(dtype="float64")

    df = pd.concat(
        [pd.Series(pn_prices).rename("pn"), pd.Series(on_prices).rename("on")],
        axis=1,
    ).dropna()

    df = df[(df["on"] > 0) & (df["pn"] > 0)]
    if df.empty:
        return pd.Series(dtype="float64")

    ratio = df["pn"] / df["on"]
    ratio.name = "ratio"
    return ratio


def _clamp_window(window, n):
    """Garante que a janela não seja maior que os dados disponíveis."""
    window = int(window)
    if window < 2:
        window = 2
    return min(window, max(2, n))


def analyze_ratio(ratio, short_window=20, long_window=60, entry_zscore=2.0,
                  ratio_atual=None):
    """
    Calcula as estatísticas do ratio.

    Se ratio_atual for informado (ex.: preço de mercado agora), ele é usado no
    cálculo do z-score; caso contrário usa o último valor da série.

    Devolve um dicionário com todas as métricas. Em caso de dados insuficientes,
    devolve um dicionário com 'valido' = False.
    """
    ratio = pd.Series(ratio).dropna()
    n = len(ratio)

    if n < 5:
        return {"valido": False, "motivo": "dados insuficientes", "n": n}

    sw = _clamp_window(short_window, n)
    lw = _clamp_window(long_window, n)

    media_curta = float(ratio.rolling(sw).mean().iloc[-1])
    media_longa = float(ratio.rolling(lw).mean().iloc[-1])

    # Baseline estatístico do z-score = janela longa.
    media_ref = float(ratio.rolling(lw).mean().iloc[-1])
    std_ref = float(ratio.rolling(lw).std().iloc[-1])

    if ratio_atual is None or not np.isfinite(ratio_atual):
        ratio_atual = float(ratio.iloc[-1])
    else:
        ratio_atual = float(ratio_atual)

    if std_ref and np.isfinite(std_ref) and std_ref > 0:
        z_score = (ratio_atual - media_ref) / std_ref
    else:
        z_score = 0.0

    banda_superior = media_ref + entry_zscore * std_ref if std_ref > 0 else media_ref
    banda_inferior = media_ref - entry_zscore * std_ref if std_ref > 0 else media_ref

    distancia_pct = ((ratio_atual - media_ref) / media_ref * 100.0
                     if media_ref else 0.0)

    return {
        "valido": True,
        "n": n,
        "ratio_atual": ratio_atual,
        "media_curta": media_curta,
        "media_longa": media_longa,
        "media_ref": media_ref,
        "desvio_padrao": std_ref,
        "z_score": float(z_score),
        "banda_superior": banda_superior,
        "banda_inferior": banda_inferior,
        "distancia_pct": float(distancia_pct),
    }


def classify_signal(z_score, entry_zscore=2.0, exit_zscore=0.5):
    """
    Classifica o sinal a partir do z-score.

     - z > +entry  -> PN cara contra ON  -> VENDER PN / COMPRAR ON
     - z < -entry  -> PN barata contra ON -> VENDER ON / COMPRAR PN
     - |z| < exit  -> zona neutra / saída
     - caso contrário -> sem sinal de entrada
    """
    if z_score > entry_zscore:
        return "VENDER_PN_COMPRAR_ON"
    if z_score < -entry_zscore:
        return "VENDER_ON_COMPRAR_PN"
    if abs(z_score) < exit_zscore:
        return "NEUTRO_SAIDA"
    return "SEM_SINAL"
