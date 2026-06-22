"""
Estimativa tributária (apenas ESTIMATIVA operacional).

ATENÇÃO: este cálculo é uma aproximação para ajudar na decisão. As regras
tributárias mudam e têm exceções. Confirme tudo com um contador ou fonte
oficial antes de operar dinheiro real.

Considera:
 - operação comum (swing trade);
 - day trade;
 - limite mensal de vendas para possível isenção (operação comum);
 - alíquota de IR (swing e day trade);
 - IRRF ("dedo-duro");
 - prejuízos acumulados e compensação;
 - lucro tributável estimado.
"""

from src.logger import get_logger

logger = get_logger()


class TaxEstimator:
    """Calcula o imposto estimado de uma operação."""

    def __init__(self, config):
        tax_cfg = config.get("tax", {})
        self.swing_rate = float(tax_cfg.get("swing_trade_tax_rate", 0.15))
        self.day_rate = float(tax_cfg.get("day_trade_tax_rate", 0.20))
        self.exemption_limit = float(tax_cfg.get("monthly_exemption_limit", 20000))
        self.irrf_swing = float(tax_cfg.get("irrf_swing_trade_pct", 0.00005))
        self.irrf_day = float(tax_cfg.get("irrf_day_trade_pct", 0.01))
        self.prejuizo_acumulado = float(tax_cfg.get("prejuizo_acumulado", 0))

        sim_cfg = config.get("simulation", {})
        self.considerar_imposto = bool(sim_cfg.get("considerar_imposto", True))
        self.considerar_limite_isencao = bool(
            sim_cfg.get("considerar_limite_isencao", True))

    def estimate(self, lucro_bruto, vendas_mes=0.0, operation="swing"):
        """
        Estima o imposto.

        Parâmetros:
         - lucro_bruto: lucro financeiro estimado da operação (R$).
         - vendas_mes: total de vendas no mês (R$), usado na regra de isenção.
         - operation: "swing" (operação comum) ou "day" (day trade).

        Devolve um dicionário com a decomposição do cálculo.
        """
        resultado = {
            "operacao": operation,
            "lucro_bruto": round(float(lucro_bruto), 2),
            "isento": False,
            "aliquota": 0.0,
            "base_calculo": 0.0,
            "irrf": 0.0,
            "imposto_estimado": 0.0,
            "observacao": "",
        }

        if not self.considerar_imposto:
            resultado["observacao"] = "cálculo de imposto desligado no config"
            return resultado

        # Sem lucro, sem imposto a pagar (mas pode gerar prejuízo a compensar).
        if lucro_bruto <= 0:
            resultado["observacao"] = "sem lucro tributável"
            return resultado

        if operation == "day":
            aliquota = self.day_rate
            irrf_pct = self.irrf_day
            isento = False
        else:
            aliquota = self.swing_rate
            irrf_pct = self.irrf_swing
            # Isenção de operação comum: vendas no mês <= limite.
            isento = (self.considerar_limite_isencao
                      and vendas_mes <= self.exemption_limit)

        resultado["aliquota"] = aliquota

        if isento:
            resultado["isento"] = True
            resultado["observacao"] = (
                "possível isenção (vendas no mês abaixo do limite)")
            # IRRF "dedo-duro" ainda pode incidir, mas em operação comum
            # isenta o impacto é desprezível; mantemos 0 para simplicidade.
            return resultado

        # Compensa prejuízo acumulado antes de tributar.
        base = max(0.0, lucro_bruto - self.prejuizo_acumulado)
        imposto = base * aliquota
        irrf = vendas_mes * irrf_pct

        resultado["base_calculo"] = round(base, 2)
        resultado["irrf"] = round(irrf, 2)
        resultado["imposto_estimado"] = round(imposto, 2)
        return resultado
