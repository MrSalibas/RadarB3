"""
B3 Parity Scanner — ponto de entrada.

Rode com:  python main.py

Menu principal:
 1. Atualizar universo de ações da B3
 2. Encontrar pares ON/PN
 3. Filtrar pares por liquidez
 4. Rodar backtest
 5. Monitorar mercado em tempo real aproximado
 6. Enviar alerta de teste no Telegram
 7. Gerar planilha Excel
 8. Ver ranking dos melhores pares
 9. Sair

O sistema SEMPRE inicia em modo seguro (paper trading) e NÃO envia ordens reais.
"""

import sys
import time
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

# Carrega variáveis do .env antes de tudo.
load_dotenv()

from src import get_paths, load_config           # noqa: E402
from src.logger import get_logger                # noqa: E402

logger = get_logger()


def _print_header():
    print("\n" + "=" * 60)
    print("            B3 PARITY SCANNER  (modo seguro)")
    print("   Arbitragem de paridade ON/PN — paper trading apenas")
    print("=" * 60)


def _print_menu():
    print("""
 1. Atualizar universo de ações da B3
 2. Encontrar pares ON/PN
 3. Filtrar pares por liquidez
 4. Rodar backtest
 5. Monitorar mercado em tempo real aproximado
 6. Enviar alerta de teste no Telegram
 7. Gerar planilha Excel
 8. Ver ranking dos melhores pares
 9. Sair
""")


# ---------------------------------------------------------------------------
# Ações do menu
# ---------------------------------------------------------------------------
def acao_universo(config):
    from src.universe import build_universe
    df = build_universe(config)
    print(f"\nUniverso montado com {len(df)} ações ON/PN.")
    print("Arquivo: data/universo_b3.csv")


def acao_pares(config):
    from src.pair_finder import find_pairs
    df = find_pairs(config)
    print(f"\n{len(df)} pares ON/PN encontrados.")
    print("Arquivo: data/pares_on_pn.csv")
    if not df.empty:
        print(df[["empresa_base", "ticker_on", "ticker_pn"]].head(20).to_string(
            index=False))


def acao_liquidez(config):
    from src.liquidity_filter import filter_pairs
    print("\nFiltrando por liquidez (pode demorar, baixa dados de cada ativo)...")
    df = filter_pairs(config)
    if df.empty:
        print("Nenhum par para filtrar.")
        return
    aprovados = int((df["status"] == "aprovado").sum())
    print(f"\n{aprovados} pares aprovados de {len(df)}.")
    print("Arquivo: data/pares_filtrados_liquidez.csv")


def acao_backtest(config):
    from src.backtest import run_backtest
    print("\nRodando backtest em todos os pares (pode demorar)...")
    ranking = run_backtest(config)
    if ranking.empty:
        print("Sem resultados de backtest.")
        return
    print("\nTop 10 pares por score:")
    print(ranking.head(10).to_string(index=False))
    print("\nArquivo: data/ranking_pares.csv")


def acao_excel(config):
    from src.spreadsheet import build_spreadsheet
    caminho = build_spreadsheet(config)
    print(f"\nPlanilha gerada: {caminho}")


def acao_ranking(config):
    paths = get_paths(config)
    try:
        ranking = pd.read_csv(paths["ranking"])
    except Exception:
        print("\nRanking ainda não gerado. Rode o backtest (opção 4) primeiro.")
        return
    if ranking.empty:
        print("\nRanking vazio.")
        return
    print("\n=== RANKING DOS MELHORES PARES ===")
    print(ranking.head(20).to_string(index=False))


def acao_teste_telegram(config):
    from src.alerts import TelegramAlert
    alert = TelegramAlert(config)
    print("\nEnviando mensagem de teste ao Telegram...")
    if alert.send_test_message():
        print("Mensagem de teste enviada com sucesso! Verifique seu Telegram.")
    else:
        print("Falha ao enviar. Verifique o .env (TELEGRAM_BOT_TOKEN/CHAT_ID) "
              "e os logs em logs/app.log.")


def _carregar_pares_monitor(config):
    """Carrega os pares aprovados na liquidez; se não houver, usa todos."""
    paths = get_paths(config)
    try:
        pares = pd.read_csv(paths["pares_filtrados"])
        if "status" in pares.columns:
            pares = pares[pares["status"] == "aprovado"].reset_index(drop=True)
    except Exception:
        pares = pd.DataFrame()

    if pares.empty:
        try:
            pares = pd.read_csv(paths["pares"])
        except Exception:
            pares = pd.DataFrame()
    return pares


def _scan_once(config, pares, engine, alert, verbose=True):
    """Faz UMA varredura completa: avalia cada par, registra e alerta."""
    from src.spreadsheet import registrar_operacao_csv, registrar_sinal_csv

    alertas = 0
    agora = datetime.now()
    for _, par in pares.iterrows():
        try:
            signal, simulation = engine.evaluate_pair(par)
        except Exception as exc:
            logger.error("Falha avaliando %s/%s: %s",
                         par.get("ticker_on"), par.get("ticker_pn"), exc)
            continue

        # Registra todo sinal com z-score calculado.
        if signal.get("z_score") is not None:
            registrar_sinal_csv(config, signal)

        if signal.get("alertar"):
            msg = alert.format_signal_message(signal, simulation)
            enviado = alert.send_message(msg)
            registrar_operacao_csv(config, signal, simulation)
            alertas += 1
            status = "ENVIADO" if enviado else "NAO enviado (rate/erro/sem token)"
            if verbose:
                print(f"[{agora:%H:%M:%S}] ALERTA {signal['ticker_on']}/"
                      f"{signal['ticker_pn']} z={signal['z_score']} -> {status}")
        elif verbose:
            print(f"[{agora:%H:%M:%S}] {signal['ticker_on']}/"
                  f"{signal['ticker_pn']} z={signal.get('z_score')} "
                  f"({signal.get('decisao')})")
    return alertas


def acao_monitorar(config, single_pass=False):
    """
    Monitoramento em tempo real aproximado.

     - single_pass=False: loop contínuo (uso interativo / opção 5).
     - single_pass=True : uma única varredura e sai (uso em rotina/Agendador).
    """
    from src.alerts import TelegramAlert
    from src.signal_engine import SignalEngine

    pares = _carregar_pares_monitor(config)
    if pares.empty:
        print("\nNenhum par disponível. Rode as opções 1, 2 e 3 primeiro.")
        return

    engine = SignalEngine(config)
    alert = TelegramAlert(config)

    mon = config.get("monitor", {})
    refresh = int(mon.get("refresh_seconds", 60))
    abre = int(mon.get("market_open_hour", 10))
    fecha = int(mon.get("market_close_hour", 18))
    enforce = bool(mon.get("enforce_market_hours", False))

    # Modo rotina: uma varredura só.
    if single_pass:
        agora = datetime.now()
        if enforce and not (abre <= agora.hour < fecha):
            logger.info("Scan fora do horário de mercado (%dh-%dh). Ignorado.",
                        abre, fecha)
            print(f"[{agora:%H:%M:%S}] Fora do horário de mercado. Scan ignorado.")
            return
        n = _scan_once(config, pares, engine, alert, verbose=True)
        print(f"Scan concluído. {n} alerta(s) gerado(s).")
        return

    # Modo interativo: loop contínuo.
    print(f"\nMonitorando {len(pares)} pares. Atualização a cada {refresh}s.")
    print("Pressione CTRL+C para parar.\n")
    try:
        while True:
            agora = datetime.now()
            if enforce and not (abre <= agora.hour < fecha):
                print(f"[{agora:%H:%M:%S}] Fora do horário de mercado "
                      f"({abre}h-{fecha}h). Aguardando...")
                time.sleep(refresh)
                continue
            _scan_once(config, pares, engine, alert, verbose=True)
            time.sleep(refresh)
    except KeyboardInterrupt:
        print("\nMonitoramento interrompido pelo usuário.")
        logger.info("Monitoramento interrompido (CTRL+C).")


def acao_update(config):
    """Atualiza tudo em sequência: universo -> pares -> liquidez -> backtest."""
    from src.backtest import run_backtest
    from src.liquidity_filter import filter_pairs
    from src.pair_finder import find_pairs
    from src.universe import build_universe

    logger.info("Rotina de atualização iniciada.")
    build_universe(config)
    find_pairs(config)
    filter_pairs(config)
    run_backtest(config)
    logger.info("Rotina de atualização concluída.")
    print("Atualização completa: universo, pares, liquidez e backtest.")


def run_cli(config, mode):
    """
    Modo não-interativo, para uso em rotina (Agendador de Tarefas do Windows):
        py main.py update    -> atualiza universo/pares/liquidez/backtest
        py main.py scan      -> uma varredura do mercado (envia alertas)
        py main.py monitor   -> loop contínuo de monitoramento
    """
    get_paths(config)
    mode = mode.strip().lower().lstrip("-")
    logger.info("Execução não-interativa: modo '%s'.", mode)

    if mode == "monitor":
        acao_monitorar(config, single_pass=False)
    elif mode in ("scan", "once"):
        acao_monitorar(config, single_pass=True)
    elif mode in ("update", "atualizar"):
        acao_update(config)
    elif mode in ("chatid", "chat-id", "id"):
        _mostrar_chat_id(config)
    elif mode in ("teste", "test", "testar"):
        acao_teste_telegram(config)
    else:
        print(f"Modo desconhecido: '{mode}'. "
              "Use: update | scan | monitor | chatid | teste")
        return 1
    return 0


def _mostrar_chat_id(config):
    """Descobre e imprime o(s) chat ID(s) do bot do Telegram."""
    from src.alerts import TelegramAlert
    achados = TelegramAlert(config).discover_chat_ids()
    if not achados:
        print("Nenhum chat encontrado.")
        print("1) Confirme o TELEGRAM_BOT_TOKEN no arquivo .env")
        print("2) Abra o Telegram e mande qualquer mensagem para o SEU bot")
        print("3) Rode de novo:  py main.py chatid")
        return
    print("\nChats encontrados (copie o numero para TELEGRAM_CHAT_ID no .env):")
    for a in achados:
        print(f"  TELEGRAM_CHAT_ID = {a['chat_id']}    ({a['nome']})")


# ---------------------------------------------------------------------------
# Loop principal
# ---------------------------------------------------------------------------
def main():
    try:
        config = load_config()
    except Exception as exc:
        print(f"Erro ao carregar config.yaml: {exc}")
        sys.exit(1)

    # Garante que as pastas existam.
    get_paths(config)
    logger.info("B3 Parity Scanner iniciado (paper trading).")

    acoes = {
        "1": acao_universo,
        "2": acao_pares,
        "3": acao_liquidez,
        "4": acao_backtest,
        "5": acao_monitorar,
        "6": acao_teste_telegram,
        "7": acao_excel,
        "8": acao_ranking,
    }

    while True:
        _print_header()
        _print_menu()
        try:
            opcao = input("Escolha uma opção (1-9): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaindo.")
            break

        if opcao == "9":
            print("Encerrando. Até logo!")
            break

        acao = acoes.get(opcao)
        if not acao:
            print("Opção inválida. Tente novamente.")
            continue

        try:
            acao(config)
        except KeyboardInterrupt:
            print("\nOperação cancelada.")
        except Exception as exc:
            logger.exception("Erro ao executar a opção %s", opcao)
            print(f"Ocorreu um erro: {exc} (veja logs/app.log).")

        input("\nPressione ENTER para voltar ao menu...")


if __name__ == "__main__":
    # Com argumento -> modo rotina (não-interativo). Sem argumento -> menu.
    if len(sys.argv) > 1:
        try:
            _cfg = load_config()
        except Exception as exc:
            print(f"Erro ao carregar config.yaml: {exc}")
            sys.exit(1)
        sys.exit(run_cli(_cfg, sys.argv[1]))
    main()
