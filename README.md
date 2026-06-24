# B3 Parity Scanner

Monitor automático de **arbitragem de paridade (ratio trading)** entre ações
**ordinárias (ON)** e **preferenciais (PN)** da mesma empresa na bolsa
brasileira (B3): ITSA3/ITSA4, PETR3/PETR4, BBDC3/BBDC4, ITUB3/ITUB4,
GGBR3/GGBR4, CMIG3/CMIG4 e **quaisquer outras que existirem na B3**.

> O sistema **não usa lista fixa de pares**. Ele monta o universo de ações
> automaticamente e descobre sozinho quais empresas têm par ON/PN comparável.

---

## 1. O que o programa faz

O objetivo **não** é apostar na alta do preço, e sim **aumentar a quantidade de
ações**: vender a classe relativamente **cara** e comprar a relativamente
**barata** da mesma empresa. Quando o ratio (PN/ON) volta para a média, você
desfaz a troca e termina com **mais ações** do que tinha.

Fluxo completo:

1. Monta o universo de ações da B3 (online ou via arquivo local).
2. Identifica automaticamente ações ON e PN da mesma empresa.
3. Exclui FIIs, ETFs, BDRs, units e ativos sem liquidez.
4. Cria os pares ON/PN comparáveis.
5. Calcula o **ratio = preço_PN / preço_ON**.
6. Calcula média móvel, desvio padrão e **z-score** do ratio.
7. Detecta distorções estatísticas.
8. **Simula** se a troca compensa (custos, slippage, spread, imposto).
9. Envia alerta por **Telegram** somente quando faz sentido.
10. Registra sinais e operações simuladas em **CSV e Excel**.
11. Roda **backtest** para ranquear os melhores pares.
12. Opera **sempre em modo seguro / paper trading** (não envia ordens reais).

---

## 2. Como instalar o Python

1. Baixe em <https://www.python.org/downloads/> (Python 3.10+).
2. No instalador (Windows), marque **"Add Python to PATH"**.
3. Confirme no terminal:

```powershell
python --version
```

---

## 3. Criar ambiente virtual

No Windows (PowerShell), dentro da pasta do projeto:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

> Se o PowerShell bloquear o script, rode uma vez:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

No Linux/macOS:

```bash
python3 -m venv venv
source venv/bin/activate
```

---

## 4. Instalar dependências

```powershell
pip install -r requirements.txt
```

---

## 5. Configurar o Telegram

O alerta principal é por **Telegram** (não usamos WhatsApp nem Twilio).

### 6. Criar um bot pelo BotFather

1. No Telegram, procure por **@BotFather**.
2. Envie `/newbot` e siga as instruções (nome e @username do bot).
3. O BotFather devolve um **token** parecido com
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.
4. Guarde esse token.

### 7. Descobrir o seu chat ID

1. Envie qualquer mensagem para o **seu** bot (procure pelo @username dele).
2. Acesse no navegador (troque `SEU_TOKEN`):
   `https://api.telegram.org/botSEU_TOKEN/getUpdates`
3. Procure por `"chat":{"id":XXXXXXXX`. Esse número é o seu **chat ID**.

> Alternativa: converse com **@userinfobot** no Telegram para ver seu ID.

### 8. Preencher o `.env`

Copie o exemplo e edite:

```powershell
Copy-Item .env.example .env
```

Conteúdo do `.env`:

```
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=seu_token_aqui
TELEGRAM_CHAT_ID=seu_chat_id_aqui

PAPER_TRADING=true
ENABLE_LIVE_TRADING=false
```

---

## 9. Rodar o programa

```powershell
python main.py
```

Menu:

```
 1. Atualizar universo de ações da B3
 2. Encontrar pares ON/PN
 3. Filtrar pares por liquidez
 4. Rodar backtest
 5. Monitorar mercado em tempo real aproximado
 6. Enviar alerta de teste no Telegram
 7. Gerar planilha Excel
 8. Ver ranking dos melhores pares
 9. Sair
```

### Ordem recomendada na primeira vez

1. **Opção 1** — Atualizar universo (gera `data/universo_b3.csv`).
2. **Opção 2** — Encontrar pares (gera `data/pares_on_pn.csv`).
3. **Opção 3** — Filtrar liquidez (gera `data/pares_filtrados_liquidez.csv`).
4. **Opção 4** — Backtest (gera `data/ranking_pares.csv`).
5. **Opção 6** — Teste do Telegram.
6. **Opção 5** — Monitorar mercado.
7. **Opção 7** — Gerar planilha Excel de controle.

---

## 10–14. Detalhes de cada etapa

- **10. Atualizar universo:** tenta uma fonte pública (brapi.dev); se falhar,
  usa `data/tickers_b3.csv`. Você pode editar esse CSV para incluir mais ações.
- **11. Encontrar pares ON/PN:** agrupa por empresa base (as 4 primeiras letras
  do ticker) e cria pares onde existem ON **e** PN.
- **12. Filtrar liquidez:** usa `config.yaml` (`min_avg_daily_volume_brl`,
  `min_trading_days`, `min_data_points`, `max_allowed_spread_pct`).
- **13. Backtest:** simula entradas/saídas pelo z-score em todo o histórico,
  considera custos e imposto, e gera um **ranking** dos melhores pares.
- **14. Monitorar mercado:** loop que avalia os pares, respeita horário,
  intervalo de atualização e rate limit, e pode ser parado com **CTRL+C**.

---

## 15. Como interpretar os alertas

```
🚨 ALERTA DE PARIDADE B3
Par: ITSA3 / ITSA4
Situação: ITSA4 está relativamente CARA contra ITSA3.
Ação sugerida: Vender ITSA4 / Comprar ITSA3
Ratio atual: 1.082 | Média: 1.045 | Z-score: 2.31
Simulação: ganho estimado +142 ações, custos R$ 22,40, imposto R$ 0,00
```

- **Z-score > +2:** PN cara → vender PN, comprar ON.
- **Z-score < −2:** PN barata → vender ON, comprar PN.
- **|Z-score| < 0,5:** zona neutra / possível saída.

O alerta **só** é enviado quando, após custos, slippage e imposto, a troca
**aumenta a quantidade de ações** acima do mínimo configurado.

---

## 16. Limitações do yfinance

- Os dados são gratuitos e podem ter **atraso** (não é tempo real de verdade).
- Pode haver **falhas/instabilidade** e ausência de alguns tickers.
- **Não fornece spread bid/ask histórico confiável** — por isso o filtro de
  spread vem **desligado** por padrão (`liquidity.apply_spread_filter: false`)
  e o valor exibido é apenas uma **estimativa** (amplitude diária).
- "Tempo real aproximado" significa: o melhor dado disponível na fonte gratuita.

---

## 17. Riscos da estratégia

- O ratio pode **não reverter** (a distorção pode virar tendência).
- Liquidez baixa gera **slippage** alto e dificulta executar os dois lados.
- Eventos corporativos (dividendos, JCP, grupamentos) afetam o ratio.
- Custos e impostos podem **anular** o ganho teórico.
- Resultados de backtest **não garantem** desempenho futuro.

---

## 18. Aviso — não é recomendação de investimento

Este software é **educacional/ferramental**. Nada aqui é recomendação de compra
ou venda. As decisões são de inteira responsabilidade do usuário.

## 19. Aviso — cálculo tributário é estimativo

O cálculo de imposto (`src/tax.py`) é apenas uma **estimativa operacional**.
As regras tributárias mudam e têm exceções (isenção mensal de R$ 20.000 em
operação comum, day trade, IRRF, compensação de prejuízo etc.).
**Confira tudo com um contador ou fonte oficial antes de operar dinheiro real.**

## 20. Aviso — ordens reais não são enviadas automaticamente

O sistema inicia sempre em **paper trading** (`PAPER_TRADING=true`). A camada de
corretora real (`RealBrokerPlaceholder`) é apenas um **esqueleto**: mesmo com
`ENABLE_LIVE_TRADING=true`, exige **confirmação manual** digitada no terminal e
**não** possui integração real implementada.

---

## 21. Testes

```powershell
pytest -q
```

Cobrem: cálculo de ratio, identificação de pares, simulação de operação e o
filtro de ganho mínimo.

---

## Estrutura do projeto

```
b3-parity-scanner/
├── main.py
├── requirements.txt
├── .env.example
├── config.yaml
├── README.md
├── src/
│   ├── __init__.py
│   ├── universe.py
│   ├── market_data.py
│   ├── pair_finder.py
│   ├── liquidity_filter.py
│   ├── ratio_engine.py
│   ├── signal_engine.py
│   ├── simulator.py
│   ├── tax.py
│   ├── alerts.py
│   ├── spreadsheet.py
│   ├── broker_adapter.py
│   ├── backtest.py
│   └── logger.py
├── data/
│   └── tickers_b3.csv
├── logs/
│   └── .gitkeep
└── tests/
    ├── test_ratio_engine.py
    ├── test_pair_finder.py
    └── test_simulator.py
```
