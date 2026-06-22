"""
Motor de sinais.

NÃO basta o z-score passar de 2 para alertar. Antes do alerta, o sinal passa
por vários filtros:
 - liquidez suficiente (já garantida na lista de pares filtrados);
 - custo estimado;
 - slippage estimado;
 - imposto estimado;
 - ganho mínimo de ações;
 - ganho líquido positivo;
 - confirmação (janela maior reduz ruído);
 - cooldown (evita alertas repetidos no mesmo par em intervalo curto).

evaluate_pair() devolve um dicionário com a decisão final.
"""

import json
import os
import time
from datetime import datetime

from src import get_paths
from src.logger import get_logger
from src.market_data import MarketDataProvider
from src.ratio_engine import analyze_ratio, classify_signal, compute_ratio_series
from src.simulator import simulate_trade
from src.tax import TaxEstimator

logger = get_logger()


class SignalEngine:
    """Avalia pares e decide se vale um alerta."""

    def __init__(self, config, provider=None, cooldown_file=None):
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

        # Cooldown persistido em disco: assim a rotina (várias execuções
        # separadas) não repete o mesmo alerta a cada varredura.
        paths = get_paths(config)
        self.cooldown_file = cooldown_file or os.path.join(
            paths["data_dir"], "alert_cooldown.json")
        self._ultimo_alerta = self._load_cooldown()

    # ---------------------------------------------------------------
    def _load_cooldown(self):
        try:
            if os.path.exists(self.cooldown_file):
                with open(self.cooldown_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning("Não foi possível ler o cooldown: %s", exc)
        return {}

    def _save_cooldown(self):
        try:
            with open(self.cooldown_file, "w", encoding="utf-8") as f:
                json.dump(self._ultimo_alerta, f)
        except Exception as exc:
            logger.warning("Não foi possível salvar o cooldown: %s", exc)

    def _em_cooldown(self, chave):
        ultimo = self._ultimo_alerta.get(chave)
        if ultimo is None:
            return False
        decorrido = time.time() - ultimo
        return decorrido < self.cooldown_minutes * 60

    def _marca_alerta(self, chave):
        self._ultimo_alerta[chave] = time.time()
        self._save_cooldown()

    # ---------------------------------------------------------------
    def evaluate_pair(self, pair):
        """
        Avalia um par (dict ou linha de DataFrame com ticker_on/ticker_pn).
        Devolve (signal, simulation). 'signal' sempre é um dict.
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
            # Cai para o último ponto histórico.
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

        sinal = classify_signal(stats["z_score"], self.entry_zscore, self.exit_zscore)

        signal.update({
            "ratio_atual": round(stats["ratio_atual"], 6),
            "media_ratio": round(stats["media_ref"], 6),
            "z_score": round(stats["z_score"], 4),
            "sinal": sinal,
            "preco_on": preco_on,
            "preco_pn": preco_pn,
        })

        # 3) Só seguem adiante sinais de entrada.
        if sinal not in ("VENDER_PN_COMPRAR_ON", "VENDER_ON_COMPRAR_PN"):
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

        # 5) Decisão final.
        if not simulation["compensa"]:
            signal["decisao"] = "sem_oportunidade"
            signal["motivo"] = simulation["motivo"]
            return signal, simulation

        if self._em_cooldown(chave):
            signal["decisao"] = "cooldown"
            signal["motivo"] = "alerta recente para este par (cooldown ativo)"
            return signal, simulation

        # Aprovado: pode alertar.
        self._marca_alerta(chave)
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
