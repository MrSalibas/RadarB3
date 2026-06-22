"""
Pacote principal do B3 Parity Scanner.

Aqui ficam utilidades compartilhadas por todos os módulos:
 - localização da raiz do projeto;
 - carregamento da configuração (config.yaml);
 - resolução/criação de diretórios;
 - montagem dos caminhos padrão de arquivos.
"""

import os

import yaml

# Raiz do projeto = pasta que contém este pacote "src".
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(path=None):
    """Carrega o config.yaml e devolve um dicionário."""
    if path is None:
        path = os.path.join(PROJECT_ROOT, "config.yaml")
    if not os.path.isabs(path):
        path = os.path.join(PROJECT_ROOT, path)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_path(path):
    """Transforma um caminho relativo (do config) em caminho absoluto."""
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


def ensure_dir(path):
    """Garante que um diretório exista, criando se necessário."""
    path = resolve_path(path)
    os.makedirs(path, exist_ok=True)
    return path


def get_paths(config):
    """
    Centraliza todos os caminhos de arquivos usados pelo sistema.
    Cria automaticamente os diretórios base.
    """
    data_dir = ensure_dir(config["paths"]["data_dir"])
    logs_dir = ensure_dir(config["paths"]["logs_dir"])
    cache_dir = ensure_dir(config["paths"]["cache_dir"])

    return {
        "data_dir": data_dir,
        "logs_dir": logs_dir,
        "cache_dir": cache_dir,
        "tickers_local": os.path.join(data_dir, "tickers_b3.csv"),
        "universo": os.path.join(data_dir, "universo_b3.csv"),
        "pares": os.path.join(data_dir, "pares_on_pn.csv"),
        "pares_filtrados": os.path.join(data_dir, "pares_filtrados_liquidez.csv"),
        "ranking": os.path.join(data_dir, "ranking_pares.csv"),
        "sinais": os.path.join(data_dir, "sinais.csv"),
        "operacoes": os.path.join(data_dir, "operacoes.csv"),
        "excel": os.path.join(data_dir, "controle_operacoes.xlsx"),
    }
