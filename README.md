# models-free

App em Python (sem dependências externas) que cruza os rankings **"Smart Models"**
do [modelgrep](https://modelgrep.com) com os catálogos **gratuitos** de três
plataformas de API e mostra, em ordem de inteligência, onde cada modelo está
disponível:

| Ranking (modelgrep) | Fonte |
|---|---|
| Smart Models (Tier 1) | [modelgrep.com/best/smartest](https://modelgrep.com/best/smartest) — top 25 geral |
| Smart Models (Tier 2) | [modelgrep.com/free-llm-api](https://modelgrep.com/free-llm-api) — top 3, somente acima de 30 pts |

| Catálogo | Critério de gratuito |
|---|---|
| **Nvidia Build** | endpoints com selo `Free Endpoint` no `nimType` (via API de catálogo do NGC) |
| **OpenCode Zen** | modelos com `free` no id ([API pública](https://opencode.ai/zen/v1/models)) |
| **OpenRouter** | modelos com flag `is_free` ([catálogo completo](https://openrouter.ai/api/frontend/v1/catalog/models)) |

Também classifica os modelos **TTS gratuitos** de cada plataforma:
- **Nvidia** — exige o selo `Free Endpoint` (modelos apenas "Download Available" são downloads, não endpoints);
- **OpenCode Zen** — palavras-chave de TTS no id dos modelos free;
- **OpenRouter** — modalidade de saída `speech` (ex.: Fish Audio S2.1 Pro Free, Deepgram Flux TTS).

## Exemplo de saída

```
Smart Models:

  1. Meta: Muse Spark 1.3 - OpenCode Zen
  2. Z.ai: GLM 5.3 - Nvidia
  3. MoonshotAI: Kimi K3 - Nvidia
  ...
  8. Z.ai: GLM 5.2 (free) - OpenRouter

TTS:

  1. magpie-tts-zeroshot - Nvidia
  2. Deepgram: Flux TTS (free) - OpenRouter
  3. Fish Audio: S2.1 Pro Free (free) - OpenRouter
```

## Uso

```bash
python comparar_modelos.py              # saída simples: "Modelo - Onde está"
python comparar_modelos.py --detalhado  # slugs, URLs, descrições e pontuações
python comparar_modelos.py --json       # também salva resultados.json
python comparar_modelos.py --zen-todos  # inclui os modelos pagos do Zen
python comparar_modelos.py --fuzzy      # ativa correspondência aproximada
```

A cada execução o app também gera um **`index.html`** autocontido (dados
embutidos, sem servidor) com os resultados em formato visual — basta abrir
com duplo clique no navegador.

## Página publicada (GitHub Pages)

Este repositório inclui um workflow do GitHub Actions
(`.github/workflows/atualizar-pagina.yml`) que **roda o script duas vezes ao
dia** (06:00 e 18:00, horário de Brasília) e publica o `index.html` gerado no
**GitHub Pages** — a página fica sempre atualizada, sem ninguém rodar nada.

Para ativar:

1. Faça push deste repositório para o GitHub;
2. Em **Settings → Pages → Build and deployment**, escolha
   **Source: GitHub Actions**;
3. Pronto — a página ficará em `https://<seu-usuário>.github.io/<repo>/`
   e o workflow também pode ser executado manualmente na aba **Actions**
   (botão *Run workflow*).

## Como funciona

1. **Tier 1** — extrai o JSON-LD (schema.org `ItemList`) embutido em
   `modelgrep.com/best/smartest` (top 25 "Smartest LLMs").
2. **Tier 2** — extrai o JSON-LD de `modelgrep.com/free-llm-api`, pega o top 3
   e consulta a página individual de cada modelo no modelgrep para obter o
   Intelligence Index (a listagem não traz a pontuação); mantém só os que
   pontuam acima de 30.
3. **Nvidia** — consulta a API de catálogo do NGC
   (`api.ngc.nvidia.com/v2/search/catalog/resources`, a mesma fonte de
   build.nvidia.com) e obtém os endpoints hospedados com o selo `nimType`
   ("Free Endpoint", "Partner Endpoint", "Download Available").
4. **OpenCode Zen** — baixa `https://opencode.ai/zen/v1/models`; por padrão
   considera apenas os modelos "free" (`--zen-todos` desativa o filtro).
5. **OpenRouter** — baixa o catálogo completo do frontend e mantém apenas os
   modelos com flag `endpoint.is_free`. A API pública `/api/v1/models` não
   serve: ela omite os modelos TTS (ex.: fish-audio, deepgram) e o par
   pricing 0/0 é enganoso (Lyria tem 0/0 mas cobra por música).
6. **Comparação** — normaliza os nomes (`GLM 5.3 Flash` → `glm-5-3-flash`) e
   cruza cada ranking com cada catálogo por correspondência exata. Com
   `--fuzzy` ativa correspondência aproximada (`--limiar` ajusta o corte).
7. **TTS** — filtra em cada catálogo os modelos com palavras-chave de
   text-to-speech, mantendo apenas os gratuitos: na Nvidia exige o selo
   "Free Endpoint"; no OpenRouter também aceita free com saída de áudio
   (`speech`).

## Requisitos

- Python 3.9+ (usa apenas a biblioteca padrão — `urllib`, `json`, `difflib`, `re`)
