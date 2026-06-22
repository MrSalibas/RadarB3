"""
Camada de corretora e segurança.

O programa inicia OBRIGATORIAMENTE em modo seguro (paper trading).
NÃO envia ordens reais por padrão.

Classes:
 - BrokerAdapter: classe base (interface).
 - PaperBroker: simula execução (paper trading), nada vai ao mercado.
 - RealBrokerPlaceholder: esqueleto para integração futura. Mesmo com
   ENABLE_LIVE_TRADING=true, exige confirmação manual no terminal antes de
   qualquer ordem real.
"""

import os
from datetime import datetime

from src.logger import get_logger

logger = get_logger()


class BrokerAdapter:
    """Interface base. Toda corretora deve implementar place_order()."""

    def __init__(self, config=None):
        self.config = config or {}

    def is_live(self):
        return False

    def place_order(self, side, ticker, quantity, price=None, **kwargs):
        raise NotImplementedError("Implemente place_order na subclasse.")


class PaperBroker(BrokerAdapter):
    """Execução simulada. Apenas registra a ordem, sem enviar ao mercado."""

    def __init__(self, config=None):
        super().__init__(config)
        self.ordens = []

    def is_live(self):
        return False

    def place_order(self, side, ticker, quantity, price=None, **kwargs):
        ordem = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "modo": "PAPER",
            "side": side,
            "ticker": ticker,
            "quantity": quantity,
            "price": price,
            "status": "simulada",
        }
        self.ordens.append(ordem)
        logger.info("[PAPER] Ordem simulada: %s %s x%s @ %s",
                    side, ticker, quantity, price)
        return ordem


class RealBrokerPlaceholder(BrokerAdapter):
    """
    Esqueleto para integração futura com corretora real.

    NÃO implementa conexão de verdade. Mesmo que ENABLE_LIVE_TRADING=true,
    qualquer ordem exige confirmação manual digitada no terminal.
    """

    def __init__(self, config=None):
        super().__init__(config)
        self.live_enabled = (
            os.getenv("ENABLE_LIVE_TRADING", "false").strip().lower() == "true")
        self.require_confirmation = bool(
            self.config.get("trading", {}).get("require_manual_confirmation", True))

    def is_live(self):
        return self.live_enabled

    def place_order(self, side, ticker, quantity, price=None, **kwargs):
        if not self.live_enabled:
            logger.warning("Ordem real bloqueada: ENABLE_LIVE_TRADING está false.")
            return {"status": "bloqueada", "motivo": "live trading desativado"}

        # Confirmação manual obrigatória.
        if self.require_confirmation:
            print("\n=========== CONFIRMAÇÃO DE ORDEM REAL ===========")
            print(f"  Operação : {side}")
            print(f"  Ativo    : {ticker}")
            print(f"  Qtde     : {quantity}")
            print(f"  Preço    : {price}")
            print("=================================================")
            resposta = input("Digite EXATAMENTE 'CONFIRMAR' para enviar: ").strip()
            if resposta != "CONFIRMAR":
                logger.info("Ordem real cancelada pelo usuário.")
                return {"status": "cancelada", "motivo": "não confirmada"}

        # ------------------------------------------------------------------
        # AQUI entraria a integração real com a API da corretora.
        # Mantido como placeholder de propósito — não envia nada.
        # ------------------------------------------------------------------
        logger.error("RealBrokerPlaceholder: integração real NÃO implementada. "
                     "Nenhuma ordem foi enviada ao mercado.")
        return {
            "status": "nao_implementado",
            "motivo": "integração real é apenas um esqueleto",
            "side": side, "ticker": ticker, "quantity": quantity, "price": price,
        }


def build_broker(config):
    """
    Fábrica de corretora baseada nas variáveis de ambiente / config.
    Por padrão devolve PaperBroker (modo seguro).
    """
    paper = os.getenv("PAPER_TRADING", "true").strip().lower() == "true"
    live = os.getenv("ENABLE_LIVE_TRADING", "false").strip().lower() == "true"

    if paper or not live:
        logger.info("Corretora em modo PAPER (seguro).")
        return PaperBroker(config)

    logger.warning("Corretora em modo REAL (placeholder, exige confirmação manual).")
    return RealBrokerPlaceholder(config)
