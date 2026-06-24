"""
Motor de sinais.

NÃO basta o z-score passar de 2 para alertar. Antes do alerta de ENTRADA, o
sinal passa por vários filtros (liquidez, custo, slippage, imposto, ganho
mínimo, ganho líquido positivo, distância mínima e cooldown).

Além da ENTRADA (quando as ações se afastam), o motor também avisa a SAÍDA
(quando o ratio volta para perto da média) — a hora de desfazer a troca.

Pares com histórico ruim no backtest NÃO são ignorados: o alerta é enviado
mesmo assim, mas com um aviso de que a recomendação histórica é ruim.

evaluate_pair() devolve um dicionário com a decisão final.
"""

import json
import os
import time
from datetime import datetime

import pandas as pd

from src import get_paths
from src.logger import get_logger
from src.market_data import MarketDataProvider
from src.ratio_engine import analyze_ratio, classify_signal, compute_ratio_series
from src.simulator import simulate_trade
from src.tax import TaxEstimator

logger = get_logger()

ENTRY_SIGNALS = ("VENDER_PN_COMPRAR_ON", "VENDER_ON_COMPRAR_PN")


class SignalEngine:
    """Avalia pares e decide se vale um alerta de entrada ou de saída."""

    def __init__(self, config, provider=None, state_file=None):
        self.config = config
        self.provider = provider or MarketDataProvider()
        self.tax = TaxEstimator(config)

        strat = config["strategy"]
        self.short_window = strat["short_window"]
        self.long_window = strat["long_window"]
        self.entry_zscore = strat["entry_zscore"]
        self.exit_zscore = strat["exit_zscore"]
        self.cooldown_minutes = strat.get("alert_cooldown_minutes", 30)

        ds = config["data_source"]
        self.period = ds["historical_period"]
        self.interval = ds["historical_interval"]

        paths = get_paths(config)
        # Estado persistido (cooldown + posições abertas) num único arquivo.
        # Assim a rotina (várias execuções separadas) não repete alertas e
        # consegue avisar a saída de uma distorção aberta antes.
        self.state_file = state_file or os.path.join(
            paths["data_dir"], "alert_cooldown.json")
        self._state = self._load_state()

        # Recomendação do backtest por par (RECOMENDADO / NEUTRO / EVITAR).
        self._recomendacoes = self._carregar_recomendacoes(paths)

    # ---------------------------------------------------------------
    # Persistência de estado
    # ---------------------------------------------------------------
    def _load_state(self):
        estado = {"cooldown": {}, "abertas": {}}
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "cooldown" in data or "abertas" in data:
                        estado["cooldown"] = data.get("cooldown", {})
                        estado["abertas"] = data.get("abertas", {})
                    else:
                        # Formato antigo (plano) = só cooldown.
                        estado["cooldown"] = data
        except Exception as exc:
            logger.warning("Não foi possível ler o estado: %s", exc)
        return estado

    def _save_state(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._state, f)
        except Exception as exc:
            logger.warning("Não foi possível salvar o estado: %s", exc)

    # --- cooldown ---
    def _em_cooldown(self, chave):
        ultimo = self._state["cooldown"].get(chave)
        if ultimo is None:
            return False
        return (time.time() - ultimo) < self.cooldown_minutes * 60

    def _marca_alerta(self, chave):
        self._state["cooldown"][chave] = time.time()
        self._save_state()

    # --- posições abertas ---
    def _posicao_aberta(self, chave):
        return self._state["abertas"].get(chave)

    def _abrir_posicao(self, chave, side, ratio):
        self._state["abertas"][chave] = {
            "side": side, "ratio_entrada": ratio, "ts": time.time()}
        self._save_state()

    def _fechar_posicao(self, chave):
        self._state["abertas"].pop(chave, None)
        self._save_state()

    # ---------------------------------------------------------------
    def _carregar_recomendacoes(self, paths):
        """Lê data/ranking_pares.csv (se existir) -> {(on, pn): recomendacao}."""
        rec = {}
        try:
            if os.path.exists(paths["ranking"]):
                df = pd.read_csv(paths["ranking"])
                for _, r in df.iterrows():
                    chave = (str(r.get("ticker_on", "")),
                             str(r.get("ticker_pn", "")))
                    rec[chave] = str(r.get("recomendacao", "N/D"))
        except Exception as exc:
            logger.warning("Não foi possível ler o ranking: %s", exc)
        return rec

    def _recomendacao(self, ticker_on, ticker_pn):
        return self._recomendacoes.get((str(ticker_on), str(ticker_pn)), "N/D")

    # ---------------------------------------------------------------
    def evaluate_pair(self, pair):
        """
        Avalia um par. Devolve (signal, simulation).
        signal["tipo"] = "entrada" ou "saida" quando alertar=True.
        """
        ticker_on = str(pair["ticker_on"])
        ticker_pn = str(pair["ticker_pn"])
        empresa_base = str(pair.get("empresa_base", ""))
        chave = f"{empresa_base}:{ticker_on}/{ticker_pn}"

        signal = {
            "empresa_base": empresa_base,
            "ticker_on": ticker_on,
            "ticker_pn": ticker_pn,
            "ratio_atual": None,
            "media_ratio": None,
            "z_score": None,
            "sinal": "SEM_SINAL",
            "tipo": "entrada",
            "recomendacao": "",
            "ativo_vender": "",
            "ativo_comprar": "",
            "decisao": "ignorar",
            "alertar": False,
            "motivo": "",
            "preco_on": None,
            "preco_pn": None,
            "ganho_estimado": 0.0,
            "custo_estimado": 0.0,
            "imposto_estimado": 0.0,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 1) Série histórica do ratio.
        pn_hist = self.provider.close_series(ticker_pn, self.period, self.interval)
        on_hist = self.provider.close_series(ticker_on, self.period, self.interval)
        ratio_series = compute_ratio_series(pn_hist, on_hist)

        if ratio_series.empty or len(ratio_series) < 5:
            signal["motivo"] = "dados históricos insuficientes"
            signal["decisao"] = "sem_dados"
            return signal, None

        # 2) Preço atual (intradiário aproximado) para o ratio corrente.
        preco_pn = self.provider.last_price(ticker_pn)
        preco_on = self.provider.last_price(ticker_on)
        ratio_atual = None
        if preco_pn and preco_on and preco_on > 0:
            ratio_atual = preco_pn / preco_on
        else:
            preco_pn = float(pn_hist.iloc[-1]) if not pn_hist.empty else None
            preco_on = float(on_hist.iloc[-1]) if not on_hist.empty else None

        stats = analyze_ratio(
            ratio_series,
            short_window=self.short_window,
            long_window=self.long_window,
            entry_zscore=self.entry_zscore,
            ratio_atual=ratio_atual,
        )

        if not stats.get("valido"):
            signal["motivo"] = stats.get("motivo", "estatística inválida")
            signal["decisao"] = "sem_dados"
            return signal, None

        sinal = classify_signal(stats["z_score"], self.entry_zscore,
                                self.exit_zscore)

        signal.update({
            "ratio_atual": round(stats["ratio_atual"], 6),
            "media_ratio": round(stats["media_ref"], 6),
            "z_score": round(stats["z_score"], 4),
            "sinal": sinal,
            "preco_on": preco_on,
            "preco_pn": preco_pn,
        })

        # ============================================================
        # SAÍDA: se há posição aberta neste par e o ratio voltou para
        # perto da média, avisa a hora de desfazer a troca.
        # ============================================================
        aberta = self._posicao_aberta(chave)
        if aberta and abs(stats["z_score"]) < self.exit_zscore:
            side_entrada = aberta.get("side")
            # Para desfazer, inverte a operação da entrada.
            if side_entrada == "VENDER_PN_COMPRAR_ON":
                signal["ativo_vender"], signal["ativo_comprar"] = ticker_on, ticker_pn
            else:
                signal["ativo_vender"], signal["ativo_comprar"] = ticker_pn, ticker_on
            self._fechar_posicao(chave)
            signal["tipo"] = "saida"
            signal["alertar"] = True
            signal["decisao"] = "saida_normalizada"
            signal["motivo"] = ("o ratio voltou para perto da média — "
                                "hora de desfazer a troca")
            return signal, None

        # ============================================================
        # ENTRADA
        # ============================================================
        # 3) Só seguem adiante sinais de entrada.
        if sinal not in ENTRY_SIGNALS:
            signal["motivo"] = "z-score dentro da faixa neutra"
            signal["decisao"] = "ignorar"
            return signal, None

        # Confirmação extra: distância mínima da média (filtra ruído pequeno).
        min_dev = self.config["strategy"].get("min_ratio_deviation_pct", 0.0)
        if abs(stats["distancia_pct"]) < min_dev:
            signal["motivo"] = "distância do ratio abaixo do mínimo"
            signal["decisao"] = "ignorar"
            return signal, None

        # Define ativos e preços de venda/compra.
        if sinal == "VENDER_PN_COMPRAR_ON":
            ativo_vender, ativo_comprar = ticker_pn, ticker_on
            preco_venda, preco_compra = preco_pn, preco_on
        else:
            ativo_vender, ativo_comprar = ticker_on, ticker_pn
            preco_venda, preco_compra = preco_on, preco_pn

        signal["ativo_vender"] = ativo_vender
        signal["ativo_comprar"] = ativo_comprar

        if not preco_venda or not preco_compra:
            signal["motivo"] = "preços indisponíveis para simulação"
            signal["decisao"] = "sem_dados"
            return signal, None

        # 4) Simulação com custos, slippage e imposto.
        simulation = simulate_trade(
            side=sinal,
            preco_venda=preco_venda,
            preco_compra=preco_compra,
            ratio_atual=stats["ratio_atual"],
            ratio_media=stats["media_ref"],
            config=self.config,
            tax_estimator=self.tax,
            par=f"{ticker_on}/{ticker_pn}",
            ativo_vendido=ativo_vender,
            ativo_comprado=ativo_comprar,
        )

        signal["ganho_estimado"] = simulation["ganho_financeiro_estimado"]
        signal["custo_estimado"] = round(
            simulation["custo_operacional"] + simulation["slippage_estimado"], 2)
        signal["imposto_estimado"] = simulation["imposto_estimado"]
        signal["ganho_liquido_acoes"] = simulation["ganho_liquido_acoes"]
        signal["recomendacao"] = self._recomendacao(ticker_on, ticker_pn)

        # 5) Decisão final.
        if not simulation["compensa"]:
            signal["decisao"] = "sem_oportunidade"
            signal["motivo"] = simulation["motivo"]
            return signal, simulation

        if self._em_cooldown(chave):
            signal["decisao"] = "cooldown"
            signal["motivo"] = "alerta recente para este par (cooldown ativo)"
            return signal, simulation

        # Aprovado: alerta de ENTRADA e marca a posição como aberta.
        self._marca_alerta(chave)
        self._abrir_posicao(chave, sinal, stats["ratio_atual"])
        signal["tipo"] = "entrada"
        signal["decisao"] = "oportunidade_valida"
        signal["alertar"] = True
        signal["motivo"] = ("z-score extremo com ganho líquido positivo "
                            "após custos e imposto")
        return signal, simulation

    # ---------------------------------------------------------------
    def evaluate_all(self, pares_df):
        """Avalia uma lista/DataFrame de pares. Devolve lista de (signal, sim)."""
        resultados = []
        for _, par in pares_df.iterrows():
            try:
                resultados.append(self.evaluate_pair(par))
            except Exception as exc:
                logger.error("Erro avaliando par %s/%s: %s",
                             par.get("ticker_on"), par.get("ticker_pn"), exc)
        return resultados
