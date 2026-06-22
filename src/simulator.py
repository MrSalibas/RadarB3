"""
Simulador da troca de paridade (ratio trading).

Ideia da estratégia: você possui a classe relativamente CARA da empresa.
Quando o ratio está distorcido, você vende a cara e compra a barata. Quando o
ratio volta para a média, você desfaz a troca e termina com MAIS ações da
classe original do que tinha no começo.

O simulador estima, para um capital padrão (R$ 50.000):
 - quantidades vendidas/compradas;
 - custos (corretagem + emolumentos);
 - slippage;
 - imposto estimado;
 - ganho bruto e líquido em QUANTIDADE de ações;
 - ganho financeiro;
 - percentual de vantagem;
 - se a operação COMPENSA.

Modelo do ganho em ações (ida e volta, assumindo reversão à média):
 - Vender PN / comprar ON  (ratio acima da média):
       fator = ratio_atual / ratio_media - 1
 - Vender ON / comprar PN  (ratio abaixo da média):
       fator = ratio_media / ratio_atual - 1
O 'fator' é o ganho percentual em quantidade de ações da classe original
quando o ratio retorna exatamente para a média.
"""

import math

from src.logger import get_logger
from src.tax import TaxEstimator

logger = get_logger()


def _ajusta_quantidade(qtd, usar_fracionario, lote):
    """Arredonda a quantidade conforme uso (ou não) de lote fracionário."""
    if usar_fracionario:
        return math.floor(qtd)  # ações inteiras (não fracionamos centavos)
    lote = max(1, int(lote))
    return (int(qtd) // lote) * lote


def simulate_trade(side, preco_venda, preco_compra, ratio_atual, ratio_media,
                   config, tax_estimator=None, par="", ativo_vendido="",
                   ativo_comprado=""):
    """
    Simula uma troca de paridade.

    Parâmetros:
     - side: "VENDER_PN_COMPRAR_ON" ou "VENDER_ON_COMPRAR_PN".
     - preco_venda: preço do ativo que será VENDIDO agora.
     - preco_compra: preço do ativo que será COMPRADO agora.
     - ratio_atual, ratio_media: ratio corrente e média de referência.
     - config: dicionário de configuração.
     - tax_estimator: instância de TaxEstimator (criada do config se None).

    Devolve um dicionário com todos os campos da simulação.
    """
    sim = config["simulation"]
    capital = float(sim["capital_inicial"])
    usar_frac = bool(sim["usar_lote_fracionario"])
    lote = sim["lote_padrao"]
    corretagem = float(sim["corretagem"])
    emol_pct = float(sim["emolumentos_pct"])
    slippage_pct = float(sim["slippage_pct"])
    ganho_min_acoes = float(sim["ganho_minimo_acoes"])
    ganho_min_pct = float(sim["ganho_minimo_pct"])

    if tax_estimator is None:
        tax_estimator = TaxEstimator(config)

    resultado = {
        "par": par,
        "ativo_vendido": ativo_vendido,
        "ativo_comprado": ativo_comprado,
        "preco_venda": preco_venda,
        "preco_compra": preco_compra,
        "quantidade_vendida": 0,
        "quantidade_comprada": 0,
        "total_vendido": 0.0,
        "total_comprado": 0.0,
        "custo_operacional": 0.0,
        "slippage_estimado": 0.0,
        "imposto_estimado": 0.0,
        "ganho_bruto_acoes": 0.0,
        "ganho_liquido_acoes": 0.0,
        "ganho_financeiro_estimado": 0.0,
        "percentual_vantagem": 0.0,
        "compensa": False,
        "motivo": "",
    }

    # Validações básicas.
    if not preco_venda or not preco_compra or preco_venda <= 0 or preco_compra <= 0:
        resultado["motivo"] = "preços inválidos"
        return resultado
    if not ratio_media or ratio_media <= 0 or not ratio_atual or ratio_atual <= 0:
        resultado["motivo"] = "ratio inválido"
        return resultado

    # Fator de ganho em quantidade de ações (ida e volta até a média).
    if side == "VENDER_PN_COMPRAR_ON":
        fator = ratio_atual / ratio_media - 1.0
    elif side == "VENDER_ON_COMPRAR_PN":
        fator = ratio_media / ratio_atual - 1.0
    else:
        resultado["motivo"] = "sem direção de troca"
        return resultado

    if fator <= 0:
        resultado["motivo"] = "distorção não favorável à troca"
        return resultado

    # Quantidade vendida do ativo que possuímos hoje.
    qtd_vendida = _ajusta_quantidade(capital / preco_venda, usar_frac, lote)
    if qtd_vendida <= 0:
        resultado["motivo"] = "capital insuficiente para 1 ação"
        return resultado

    total_vendido = qtd_vendida * preco_venda

    # Quantidade comprada do ativo barato (aprox., sem custos no display).
    qtd_comprada = _ajusta_quantidade(total_vendido / preco_compra, usar_frac, lote)
    total_comprado = qtd_comprada * preco_compra

    # Ganho bruto em ações (na unidade da classe original/ vendida).
    ganho_bruto_acoes = qtd_vendida * fator
    ganho_financeiro_bruto = ganho_bruto_acoes * preco_venda

    # Custos: a estratégia completa tem ~4 pernas (vender A, comprar B agora;
    # depois vender B, comprar A na reversão). Notional ~ capital por perna.
    notional = total_vendido
    n_pernas = 4
    custo_operacional = n_pernas * (corretagem + notional * emol_pct)
    slippage_estimado = n_pernas * (notional * slippage_pct)

    # Imposto estimado (operação comum / swing por padrão).
    # vendas_mes ~ 2 vendas (perna de venda agora + perna de venda na reversão).
    vendas_mes = 2 * notional
    info_imposto = tax_estimator.estimate(
        ganho_financeiro_bruto, vendas_mes=vendas_mes, operation="swing")
    imposto_estimado = info_imposto["imposto_estimado"]

    # Resultado líquido.
    ganho_financeiro_liquido = (ganho_financeiro_bruto
                                - custo_operacional
                                - slippage_estimado
                                - imposto_estimado)
    ganho_liquido_acoes = ganho_financeiro_liquido / preco_venda
    percentual_vantagem = (ganho_financeiro_liquido / capital * 100.0
                           if capital else 0.0)

    compensa = (ganho_liquido_acoes >= ganho_min_acoes
                and percentual_vantagem >= ganho_min_pct)

    resultado.update({
        "quantidade_vendida": qtd_vendida,
        "quantidade_comprada": qtd_comprada,
        "total_vendido": round(total_vendido, 2),
        "total_comprado": round(total_comprado, 2),
        "custo_operacional": round(custo_operacional, 2),
        "slippage_estimado": round(slippage_estimado, 2),
        "imposto_estimado": round(imposto_estimado, 2),
        "ganho_bruto_acoes": round(ganho_bruto_acoes, 2),
        "ganho_liquido_acoes": round(ganho_liquido_acoes, 2),
        "ganho_financeiro_estimado": round(ganho_financeiro_liquido, 2),
        "percentual_vantagem": round(percentual_vantagem, 4),
        "compensa": bool(compensa),
        "motivo": ("ganho líquido positivo após custos e imposto"
                   if compensa else "ganho líquido abaixo do mínimo exigido"),
        "info_imposto": info_imposto,
    })
    return resultado
