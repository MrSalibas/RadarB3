"""
Logger central do projeto.

Todos os módulos usam get_logger() para registrar:
 - erros;
 - sinais detectados;
 - alertas enviados;
 - simulações;
 - backtests;
 - falhas de coleta;
 - falhas no Telegram.

Os logs vão para logs/app.log e também para o console.
"""

import logging
import os

# Raiz do projeto (pasta acima de src/).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_PROJECT_ROOT, "logs")

# Evita adicionar handlers duplicados se get_logger() for chamado várias vezes.
_CONFIGURED = False


def get_logger(name="b3_parity_scanner"):
    """Devolve um logger configurado para arquivo + console."""
    global _CONFIGURED
    logger = logging.getLogger(name)

    if _CONFIGURED:
        return logger

    os.makedirs(_LOGS_DIR, exist_ok=True)
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de arquivo.
    file_handler = logging.FileHandler(
        os.path.join(_LOGS_DIR, "app.log"), encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    # Handler de console.
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False

    _CONFIGURED = True
    return logger
