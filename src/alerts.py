"""
Alertas por Telegram (canal principal).

Usa a API HTTP do Telegram via requests. Lê do ambiente (.env):
 - TELEGRAM_ENABLED
 - TELEGRAM_BOT_TOKEN
 - TELEGRAM_CHAT_ID

Regras:
 - O envio NUNCA quebra o programa inteiro (erros viram log).
 - Rate limit para evitar alertas repetidos em sequência.
"""

import os
import time

import requests

from src.logger import get_logger

logger = get_logger()

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramAlert:
    """Cliente simples de alertas por Telegram com rate limit."""

    def __init__(self, config=None):
        config = config or {}
        alerts_cfg = config.get("alerts", {})

        env_enabled = os.getenv("TELEGRAM_ENABLED", "true").strip().lower() == "true"
        cfg_enabled = bool(alerts_cfg.get("telegram_enabled", True))
        self.enabled = env_enabled and cfg_enabled

        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

        # TELEGRAM_CHAT_ID pode conter VÁRIOS destinos, separados por vírgula
        # (ou ponto e vírgula). Ex.: "12345678,87654321".
        raw_chat = os.getenv("TELEGRAM_CHAT_ID", "").strip().replace(";", ",")
        self.chat_ids = [c.strip() for c in raw_chat.split(",")
                         if c.strip() and "coloque" not in c]

        self.rate_limit_seconds = float(alerts_cfg.get("rate_limit_seconds", 60))

        self._last_sent = 0.0

    # ---------------------------------------------------------------
    def _configurado(self):
        if not self.enabled:
            logger.info("Telegram desativado por configuração.")
            return False
        if not self.token or "coloque" in self.token:
            logger.warning("TELEGRAM_BOT_TOKEN não configurado no .env.")
            return False
        if not self.chat_ids:
            logger.warning("TELEGRAM_CHAT_ID não configurado no .env.")
            return False
        return True

    def _rate_limited(self):
        return (time.time() - self._last_sent) < self.rate_limit_seconds

    # ---------------------------------------------------------------
    def send_message(self, message, ignore_rate_limit=False):
        """Envia uma mensagem. Devolve True se enviou, False caso contrário."""
        if not self._configurado():
            return False

        if not ignore_rate_limit and self._rate_limited():
            logger.info("Alerta suprimido pelo rate limit (%.0fs).",
                        self.rate_limit_seconds)
            return False

        url = TELEGRAM_API.format(token=self.token)
        enviados = 0
        # Envia a MESMA mensagem para cada destino cadastrado.
        for chat_id in self.chat_ids:
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            try:
                resp = requests.post(url, data=payload, timeout=15)
                if resp.status_code == 200 and resp.json().get("ok"):
                    enviados += 1
                else:
                    logger.error("Telegram erro para %s: %s - %s",
                                 chat_id, resp.status_code, resp.text[:200])
            except Exception as exc:
                logger.error("Falha ao enviar para %s: %s", chat_id, exc)

        if enviados > 0:
            self._last_sent = time.time()
            logger.info("Alerta enviado para %d de %d destino(s).",
                        enviados, len(self.chat_ids))
            return True
        return False

    def send_test_message(self):
        """Envia uma mensagem de teste (ignora o rate limit)."""
        msg = ("✅ <b>B3 Parity Scanner</b>\n"
               "Mensagem de teste. Se você recebeu isto, o Telegram está "
               "configurado corretamente.")
        return self.send_message(msg, ignore_rate_limit=True)

    # ---------------------------------------------------------------
    def discover_chat_ids(self):
        """
        Descobre os chat IDs a partir das últimas mensagens recebidas pelo bot.
        Use DEPOIS de mandar qualquer mensagem para o seu bot no Telegram.
        Precisa apenas do TELEGRAM_BOT_TOKEN configurado.
        Devolve uma lista de dicts {chat_id, nome}.
        """
        if not self.token or "coloque" in self.token:
            logger.warning("TELEGRAM_BOT_TOKEN não configurado no .env.")
            return []

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        try:
            resp = requests.get(url, timeout=15)
            data = resp.json()
            if not data.get("ok"):
                logger.error("Telegram getUpdates erro: %s", data)
                return []

            achados = {}
            for upd in data.get("result", []):
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat = msg.get("chat") or {}
                cid = chat.get("id")
                if cid is not None:
                    nome = (chat.get("title")
                            or " ".join(filter(None, [chat.get("first_name"),
                                                      chat.get("last_name")]))
                            or chat.get("username") or "")
                    achados[cid] = nome
            return [{"chat_id": k, "nome": v} for k, v in achados.items()]
        except Exception as exc:
            logger.error("Falha ao descobrir chat_id: %s", exc)
            return []

    # ---------------------------------------------------------------
    @staticmethod
    def format_signal_message(signal, simulation):
        """Monta a mensagem de alerta a partir do sinal + simulação."""
        ticker_on = signal.get("ticker_on", "?")
        ticker_pn = signal.get("ticker_pn", "?")
        z = signal.get("z_score")
        ratio = signal.get("ratio_atual")
        media = signal.get("media_ratio")

        if signal.get("sinal") == "VENDER_PN_COMPRAR_ON":
            situacao = f"{ticker_pn} está relativamente CARA contra {ticker_on}."
        else:
            situacao = f"{ticker_pn} está relativamente BARATA contra {ticker_on}."

        vender = signal.get("ativo_vender", "?")
        comprar = signal.get("ativo_comprar", "?")

        sim = simulation or {}
        capital = 0.0
        qtd_vend = sim.get("quantidade_vendida", 0)
        qtd_comp = sim.get("quantidade_comprada", 0)
        ganho_acoes = sim.get("ganho_liquido_acoes", 0)
        custo = round(sim.get("custo_operacional", 0) + sim.get("slippage_estimado", 0), 2)
        imposto = sim.get("imposto_estimado", 0)
        ganho_fin = sim.get("ganho_financeiro_estimado", 0)

        linhas = [
            "🚨 <b>ALERTA DE PARIDADE B3</b>",
            "",
            f"<b>Par:</b> {ticker_on} / {ticker_pn}",
            f"<b>Situação:</b> {situacao}",
            "",
            "<b>Ação sugerida:</b>",
            f"  • Vender: {vender}",
            f"  • Comprar: {comprar}",
            "",
            f"<b>Ratio atual:</b> {ratio}",
            f"<b>Média do ratio:</b> {media}",
            f"<b>Z-score:</b> {z}",
            "",
            "<b>Simulação:</b>",
            f"  • Quantidade vendida: {qtd_vend}",
            f"  • Quantidade comprada: {qtd_comp}",
            f"  • Ganho estimado: +{ganho_acoes} ações",
            f"  • Ganho financeiro estimado: R$ {ganho_fin}",
            "",
            f"<b>Custos estimados:</b> R$ {custo}",
            f"<b>Imposto estimado:</b> R$ {imposto}",
            "",
            f"<b>Decisão:</b> {signal.get('decisao', '')}",
            f"<b>Motivo:</b> {signal.get('motivo', '')}",
        ]
        return "\n".join(linhas)
