"""
Montagem automática do universo de ações da B3.

O sistema tenta, nesta ordem:
 1. Buscar de uma fonte pública (brapi.dev /quote/list).
 2. (Espaço para outras fontes gratuitas: Yahoo, Status Invest, Fundamentus...)
 3. Se a busca online falhar, usa o arquivo local data/tickers_b3.csv.

Depois, classifica cada ticker (ON/PN/Unit/FII/ETF/BDR), descobre a empresa
base e salva o resultado em data/universo_b3.csv.

IMPORTANTE: não usamos lista fixa de pares. Os pares são descobertos depois,
em pair_finder.py, a partir deste universo.
"""

import re

import pandas as pd
import requests

from src import get_paths
from src.logger import get_logger

logger = get_logger()

# Sufixos numéricos que costumam indicar BDR na B3.
BDR_SUFFIXES = {"31", "32", "33", "34", "35", "39"}

COLUMNS = [
    "ticker", "ticker_yahoo", "nome_empresa", "empresa_base", "final_ticker",
    "tipo_ativo", "is_on", "is_pn", "is_unit", "is_fii", "is_etf", "is_bdr",
    "fonte",
]


# ---------------------------------------------------------------------------
# Fontes de dados
# ---------------------------------------------------------------------------
def _fetch_from_brapi(timeout=15):
    """
    Tenta listar ativos via brapi.dev (fonte pública gratuita).
    Devolve lista de dicts {ticker, nome_empresa} ou lista vazia em caso de erro.
    """
    url = "https://brapi.dev/api/quote/list"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
        stocks = payload.get("stocks", [])
        result = []
        for item in stocks:
            ticker = (item.get("stock") or item.get("symbol") or "").strip().upper()
            nome = (item.get("name") or "").strip()
            if ticker:
                result.append({"ticker": ticker, "nome_empresa": nome})
        if result:
            logger.info("brapi retornou %d ativos.", len(result))
        return result
    except Exception as exc:
        logger.warning("Falha ao buscar universo na brapi: %s", exc)
        return []


def _load_local(paths):
    """Carrega o arquivo local data/tickers_b3.csv como fallback."""
    local = paths["tickers_local"]
    try:
        df = pd.read_csv(local)
        df.columns = [c.strip().lower() for c in df.columns]
        if "ticker" not in df.columns:
            logger.error("Arquivo local sem coluna 'ticker': %s", local)
            return []
        if "nome_empresa" not in df.columns:
            df["nome_empresa"] = ""
        registros = (
            df[["ticker", "nome_empresa"]]
            .fillna("")
            .to_dict("records")
        )
        logger.info("Arquivo local carregado: %d ativos.", len(registros))
        return registros
    except Exception as exc:
        logger.error("Falha ao ler arquivo local %s: %s", local, exc)
        return []


# ---------------------------------------------------------------------------
# Classificação dos tickers
# ---------------------------------------------------------------------------
def _split_ticker(ticker):
    """
    Separa o ticker em (letras, numero).
    Ex.: 'ITSA4' -> ('ITSA', '4');  'PETR11' -> ('PETR', '11').
    """
    match = re.match(r"^([A-Z]+)(\d+)$", ticker.strip().upper())
    if not match:
        return ticker.strip().upper(), ""
    return match.group(1), match.group(2)


def classify_ticker(ticker, nome_empresa, config, fonte):
    """Classifica um único ticker e devolve um dict com as colunas do universo."""
    ticker = str(ticker).strip().upper()
    letras, numero = _split_ticker(ticker)

    on_suffixes = [str(s) for s in config["universe"]["on_suffixes"]]
    pn_suffixes = [str(s) for s in config["universe"]["pn_suffixes"]]

    is_on = numero in on_suffixes
    is_pn = numero in pn_suffixes
    is_unit = numero == "11"
    is_bdr = numero in BDR_SUFFIXES
    # FII/ETF não são detectáveis com 100% de certeza só pelo ticker.
    # Heurística: terminados em "11" podem ser FII/ETF/Unit. Marcamos a
    # ambiguidade e deixamos as units/11 fora por padrão (excludidas).
    is_fii = False
    is_etf = False

    if is_on:
        tipo = "ON"
    elif is_pn:
        tipo = "PN"
    elif is_unit:
        tipo = "UNIT"
    elif is_bdr:
        tipo = "BDR"
    else:
        tipo = "OUTRO"

    return {
        "ticker": ticker,
        "ticker_yahoo": ticker + ".SA",
        "nome_empresa": nome_empresa,
        "empresa_base": letras[:4] if letras else ticker[:4],
        "final_ticker": numero,
        "tipo_ativo": tipo,
        "is_on": is_on,
        "is_pn": is_pn,
        "is_unit": is_unit,
        "is_fii": is_fii,
        "is_etf": is_etf,
        "is_bdr": is_bdr,
        "fonte": fonte,
    }


def _should_exclude(row, config):
    """Aplica as regras de exclusão do config (units, fiis, etfs, bdrs)."""
    u = config["universe"]
    if u.get("exclude_units", True) and row["is_unit"]:
        return True
    if u.get("exclude_fiis", True) and row["is_fii"]:
        return True
    if u.get("exclude_etfs", True) and row["is_etf"]:
        return True
    if u.get("exclude_bdrs", True) and row["is_bdr"]:
        return True
    # Mantemos apenas ON e PN para a estratégia de paridade.
    if not (row["is_on"] or row["is_pn"]):
        return True
    return False


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------
def build_universe(config, prefer_online=True):
    """
    Monta o universo de ações e salva em data/universo_b3.csv.
    Devolve o DataFrame final (já filtrado).
    """
    paths = get_paths(config)

    registros = []
    fonte = "local"

    if prefer_online:
        online = _fetch_from_brapi()
        if online:
            registros = online
            fonte = "brapi"

    if not registros:
        registros = _load_local(paths)
        fonte = "local"

    if not registros:
        logger.error("Nenhuma fonte de ativos disponível. Universo vazio.")
        empty = pd.DataFrame(columns=COLUMNS)
        empty.to_csv(paths["universo"], index=False, encoding="utf-8")
        return empty

    # Classifica todos.
    classificados = []
    vistos = set()
    for item in registros:
        ticker = str(item.get("ticker", "")).strip().upper()
        if not ticker or ticker in vistos:
            continue
        vistos.add(ticker)
        nome = item.get("nome_empresa", "") or ""
        classificados.append(classify_ticker(ticker, nome, config, fonte))

    df = pd.DataFrame(classificados, columns=COLUMNS)

    # Aplica exclusões e mantém só ON/PN válidos.
    if not df.empty:
        mask_excluir = df.apply(lambda r: _should_exclude(r, config), axis=1)
        df = df[~mask_excluir].reset_index(drop=True)

    df.to_csv(paths["universo"], index=False, encoding="utf-8")
    logger.info("Universo salvo em %s (%d ativos ON/PN).",
                paths["universo"], len(df))
    return df


if __name__ == "__main__":
    from src import load_config

    build_universe(load_config())
