"""
Identificação automática de pares ON/PN da mesma empresa.

Fluxo:
 1. Lê data/universo_b3.csv.
 2. Agrupa tickers pela empresa base (ex.: ITSA, PETR, BBDC).
 3. Encontra empresas com pelo menos uma ON e uma PN.
 4. Gera todos os pares possíveis ON x PN da mesma empresa.

Saída: data/pares_on_pn.csv
"""

import pandas as pd

from src import get_paths
from src.logger import get_logger

logger = get_logger()

PAIR_COLUMNS = [
    "empresa_base", "ticker_on", "ticker_pn",
    "ticker_on_yahoo", "ticker_pn_yahoo",
    "tipo_pn", "nome_empresa", "status", "motivo_exclusao",
]


def build_pairs_from_df(universo_df):
    """
    Recebe o DataFrame do universo e devolve o DataFrame de pares.
    Função pura (sem I/O) — facilita os testes.
    """
    if universo_df is None or universo_df.empty:
        return pd.DataFrame(columns=PAIR_COLUMNS)

    df = universo_df.copy()

    # Normaliza colunas booleanas que podem vir como string de CSV.
    for col in ("is_on", "is_pn"):
        if col in df.columns:
            df[col] = df[col].apply(_as_bool)

    pares = []
    for empresa, grupo in df.groupby("empresa_base"):
        ons = grupo[grupo["is_on"]]
        pns = grupo[grupo["is_pn"]]

        if ons.empty or pns.empty:
            continue  # empresa não tem o par ON/PN

        nome_empresa = ""
        nomes = grupo["nome_empresa"].dropna().astype(str)
        nomes = nomes[nomes.str.strip() != ""]
        if not nomes.empty:
            nome_empresa = nomes.iloc[0]

        # Todos os pares possíveis ON x PN.
        for _, on_row in ons.iterrows():
            for _, pn_row in pns.iterrows():
                pares.append({
                    "empresa_base": empresa,
                    "ticker_on": on_row["ticker"],
                    "ticker_pn": pn_row["ticker"],
                    "ticker_on_yahoo": on_row.get("ticker_yahoo",
                                                  str(on_row["ticker"]) + ".SA"),
                    "ticker_pn_yahoo": pn_row.get("ticker_yahoo",
                                                  str(pn_row["ticker"]) + ".SA"),
                    "tipo_pn": str(pn_row.get("final_ticker", "")),
                    "nome_empresa": nome_empresa,
                    "status": "ok",
                    "motivo_exclusao": "",
                })

    return pd.DataFrame(pares, columns=PAIR_COLUMNS)


def _as_bool(value):
    """Converte valores diversos (True/'True'/'true'/1) em booleano."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "sim", "yes")


def find_pairs(config):
    """Lê o universo do disco, monta os pares e salva em data/pares_on_pn.csv."""
    paths = get_paths(config)

    try:
        universo = pd.read_csv(paths["universo"])
    except Exception as exc:
        logger.error("Não foi possível ler o universo (%s): %s",
                     paths["universo"], exc)
        logger.error("Rode primeiro a opção 'Atualizar universo de ações da B3'.")
        return pd.DataFrame(columns=PAIR_COLUMNS)

    pares = build_pairs_from_df(universo)
    pares.to_csv(paths["pares"], index=False, encoding="utf-8")
    logger.info("Pares ON/PN salvos em %s (%d pares).", paths["pares"], len(pares))
    return pares


if __name__ == "__main__":
    from src import load_config

    find_pairs(load_config())
