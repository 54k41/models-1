#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comparar_modelos.py — Compara os rankings "Smart Models" do modelgrep com três
catálogos de APIs:

  Rankings (modelgrep):
    Tier 1 — https://modelgrep.com/best/smartest  (top 25 geral)
    Tier 2 — https://modelgrep.com/free-llm-api   (top 3 com score > 30 pontos)

  Catálogos:
  1. NVIDIA Build  — build.nvidia.com/models (microserviços NIM)
  2. OpenCode Zen  — opencode.ai/docs/zen (apenas modelos 'free')
  3. OpenRouter    — openrouter.ai/models (apenas modelos free, flag is_free)

Como funciona:
  1. Baixa o ranking Tier 1 e extrai, do JSON-LD embutido na página (schema.org
     ItemList), o top 25 do ranking "Smartest LLMs".
  2. Baixa o ranking Tier 2 (modelos com API gratuita), pega o top 3 e consulta
     a página individual de cada um para extrair o Intelligence Index; mantém
     apenas os que pontuam acima de 30.
  3. Baixa os catálogos: NVIDIA via API de catálogo do NGC (endpoints
     hospedados com o label 'nimType' — Free/Partner Endpoint, Download
     Available), Zen e OpenRouter via API pública ($0).
  4. Normaliza os nomes (minúsculas, pontuação unificada) e cruza cada ranking
     com cada catálogo: correspondência exata + aproximada (fuzzy).

Uso:
    python comparar_modelos.py            # compara e imprime na tela
    python comparar_modelos.py --json     # também salva resultados.json
    python comparar_modelos.py --limiar 0.90   # ajusta o corte do fuzzy

Sem dependências externas — apenas a biblioteca padrão do Python.
"""

import argparse
import difflib
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime

NVIDIA_MODELS_URL = "https://build.nvidia.com/models.md"
NGC_SEARCH_URL = "https://api.ngc.nvidia.com/v2/search/catalog/resources"
ZEN_MODELS_URL = "https://opencode.ai/zen/v1/models"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/frontend/v1/catalog/models"
MODELGREP_TIER1_URL = "https://modelgrep.com/best/smartest"
MODELGREP_TIER2_URL = "https://modelgrep.com/free-llm-api"

TIER2_TOP_N = 3     # quantos modelos do ranking free considerar
TIER2_MIN_SCORE = 30.0  # pontuação mínima (Intelligence Index) para entrar

# palavras-chave que caracterizam um modelo TTS (text-to-speech)
TTS_PADROES = ("tts", "text-to-speech", "speech synthesis", "speech generation")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# contexto TLS tolerante (evita falhas de verificação em algumas instalações Windows)
SSL_CTX = ssl.create_default_context()


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ordem define a ordem de exibição; o 3º elemento é a nota exibida no título
CATALOGOS = {
    "nvidia": ("NVIDIA BUILD", "build.nvidia.com/models", ""),
    "zen": ("OPENCODE ZEN", "opencode.ai/docs/zen", " — apenas modelos 'free'"),
    "openrouter": ("OPENROUTER", "openrouter.ai/models", " — apenas modelos free (is_free)"),
}

# rankings do modelgrep comparados contra os catálogos
TIERS = [
    ("tier1", "SMART MODELS (TIER 1)", "https://modelgrep.com/best/smartest"),
    ("tier2", "SMART MODELS (TIER 2)",
     "https://modelgrep.com/free-llm-api — top 3, somente acima de 30 pontos"),
]


# --------------------------------------------------------------------------
# 1. Catálogo da NVIDIA
# --------------------------------------------------------------------------

def fetch_nvidia_models():
    """Endpoints hospedados da NVIDIA Build via API do NGC.

    Usa https://api.ngc.nvidia.com/v2/search/catalog/resources com
    resource-type=ENDPOINT — a mesma fonte que alimenta build.nvidia.com.
    Cada registro traz o label 'nimType', que diferencia:
      'Free Endpoint'      — endpoint público com uso gratuito (trial)
      'Partner Endpoint'   — endpoint via parceiros de cloud
      'Download Available' — apenas download do NIM (sem endpoint free)
    """
    corpo = {
        "query": "",
        "filters": [],
        "orderBy": [{"field": "score", "value": "DESC"}],
        "page": 0,
        "pageSize": 200,
        "scoredSize": 200,
    }
    url = NGC_SEARCH_URL + "?" + urllib.parse.urlencode({
        "resource-type": "ENDPOINT",
        "q": json.dumps(corpo),
        "group-labels-by-labelset": "true",
    })
    dados = json.loads(_get(url))
    modelos, vistos = [], set()
    for grp in dados.get("results", []):
        for r in grp.get("resources", []):
            nome = r.get("name") or ""
            rid = r.get("resourceId") or nome
            if not nome or nome.startswith("test_endpoint") or rid in vistos:
                continue
            vistos.add(rid)
            nim = next((l.get("values", []) for l in r.get("labels", [])
                        if l.get("key") == "nimType"), [])
            modelos.append({
                "nome": r.get("displayName") or nome,
                "slug": nome,
                "nimType": nim,
                "descricao": r.get("description") or "",
            })
    return modelos


# --------------------------------------------------------------------------
# 2. Catálogo do OpenCode Zen
# --------------------------------------------------------------------------

def fetch_zen_models(apenas_free=True):
    """Retorna os modelos disponíveis no OpenCode Zen via
    https://opencode.ai/zen/v1/models (formato compatível com OpenAI).

    Por padrão considera apenas os modelos 'free' (id contém 'free');
    o sufixo '-free' / '-contributor-free' é removido para o match com
    o ranking, mas o id completo é preservado em 'id'.
    """
    dados = json.loads(_get(ZEN_MODELS_URL))
    modelos = []
    for m in dados.get("data", []):
        mid = m["id"]
        if apenas_free and "free" not in mid.lower():
            continue
        base = re.sub(r"(?:-contributor)?-free$", "", mid.lower())
        modelos.append({"nome": mid, "slug": mid, "slug_base": base, "descricao": ""})
    return modelos


# --------------------------------------------------------------------------
# 3. Catálogo do OpenRouter (apenas modelos free)
# --------------------------------------------------------------------------

def fetch_openrouter_models():
    """Modelos GRATUITOS do OpenRouter via catálogo completo do frontend.

    Fonte: https://openrouter.ai/api/frontend/v1/catalog/models (~1000 modelos).

    ATENÇÃO: a API pública /api/v1/models NÃO serve — ela lista só os modelos
    de chat/completions e omite os modelos TTS (ex.: fish-audio/s2.1-pro-free,
    deepgram/flux-tts). Gratuito aqui = flag endpoint.is_free do próprio
    OpenRouter; o par pricing 0/0 sozinho é enganoso (o Google Lyria tem
    pricing 0/0 mas cobra por música gerada — is_free=False).
    """
    dados = json.loads(_get(OPENROUTER_MODELS_URL))
    modelos, vistos = [], set()
    for m in dados.get("data", []):
        ep = m.get("endpoint") or {}
        if not ep.get("is_free"):
            continue
        mid = m.get("slug") or ""
        if not mid or mid in vistos:
            continue
        vistos.add(mid)
        base = mid.split("/")[-1]
        base = re.sub(r"(?::-free)$", "", base)
        modelos.append({
            "nome": m.get("name") or mid,
            "slug": mid,
            "slug_base": base,
            "descricao": (m.get("description") or "").strip(),
            "url": f"https://openrouter.ai/{mid}:free",
            "speech_out": "speech" in (m.get("output_modalities") or []),
        })
    return modelos


# --------------------------------------------------------------------------
# 4. Rankings "Smart Models" do Modelgrep
# --------------------------------------------------------------------------

def _extrair_itemlist(html):
    """Extrai os itens de todos os JSON-LD schema.org ItemList da página."""
    itens = []
    for m in re.finditer(
            r'<script\s+type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for bloco in (obj if isinstance(obj, list) else [obj]):
            if not isinstance(bloco, dict) or bloco.get("@type") != "ItemList":
                continue
            for el in bloco.get("itemListElement", []):
                url = el.get("url", "")
                murl = re.search(r"/models/([^/]+)/([^/]+)$", url)
                if not murl:
                    continue
                nome = el.get("name", "")
                fabricante = nome.split(":", 1)[0].strip() if ":" in nome else murl.group(1)
                itens.append({
                    "posicao": el.get("position"),
                    "nome": nome,
                    "fabricante": fabricante,
                    "slug": murl.group(2).lower(),
                    "url": url,
                })
    return itens


def _dedup(itens):
    vistos, unicos = set(), []
    for mod in sorted(itens, key=lambda x: x["posicao"] or 999):
        if mod["slug"] in vistos:
            continue
        vistos.add(mod["slug"])
        unicos.append(mod)
    return unicos


def fetch_smartest_models():
    """Tier 1: top 25 do ranking 'Smartest LLMs' (best/smartest)."""
    return _dedup(_extrair_itemlist(_get(MODELGREP_TIER1_URL)))


def _score_do_modelo(url_modelo):
    """Busca o Intelligence Index na página individual do modelo no modelgrep."""
    html = _get(url_modelo)
    m = re.search(
        r"scores?\s+(\d+(?:\.\d+)?)\s+on the Artificial Analysis Intelligence Index",
        html)
    return float(m.group(1)) if m else None


def fetch_free_top_models():
    """Tier 2: top N do ranking de APIs gratuitas (free-llm-api) que pontuam
    acima de TIER2_MIN_SCORE no Intelligence Index.

    A listagem não traz a pontuação, então a página individual de cada modelo
    do top N é consultada.
    """
    top = _dedup(_extrair_itemlist(_get(MODELGREP_TIER2_URL)))[:TIER2_TOP_N]
    selecionados = []
    for mod in top:
        score = _score_do_modelo(mod["url"])
        if score is None or score <= TIER2_MIN_SCORE:
            continue
        mod["score"] = score
        mod["slug_base"] = re.sub(r":free$", "", mod["slug"])
        selecionados.append(mod)
    return selecionados


# --------------------------------------------------------------------------
# 3. Normalização e comparação
# --------------------------------------------------------------------------

def normalizar(s: str) -> str:
    """'GLM 5.3 Flash' -> 'glm-5-3-flash' ; 'claude-opus-5.5' -> 'claude-opus-5-5'."""
    s = s.lower().strip()
    s = re.sub(r"[_.\s]+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def comparar(catalogo, smartest, fuzzy=False, limiar=0.90):
    """Cruza o ranking com um catálogo.

    Retorna (confirmadas, provaveis):
      confirmadas — normalização exata dos slugs/nomes
      provaveis   — correspondência fuzzy acima do limiar (apenas com --fuzzy)
    """
    indice = {}
    for mod in catalogo:
        indice[normalizar(mod["slug"])] = mod
        if mod.get("slug_base"):
            indice[normalizar(mod["slug_base"])] = mod
        indice.setdefault(normalizar(mod["nome"]), mod)

    confirmadas, provaveis = [], []
    usados = set()

    for rank in smartest:
        alvo = normalizar(rank.get("slug_base") or rank["slug"])
        alvos = [alvo]
        if alvo.endswith("-free"):
            alvos.append(alvo[:-len("-free")])  # 'xxx-free' também casa 'xxx'
        mod = next((indice[a] for a in alvos if a in indice), None)
        if mod is not None:
            confirmadas.append((rank, mod, 1.0))
            usados.add(id(mod))
            continue

        if not fuzzy:
            continue

        # fuzzy: melhor candidato não usado ainda
        melhor, nota = None, 0.0
        for mod in catalogo:
            if id(mod) in usados:
                continue
            for chave in (mod["slug"], mod["nome"]):
                r = difflib.SequenceMatcher(None, alvo, normalizar(chave)).ratio()
                if r > nota:
                    melhor, nota = mod, r
        if melhor is not None and nota >= limiar:
            provaveis.append((rank, melhor, nota))
            usados.add(id(melhor))

    return confirmadas, provaveis


# --------------------------------------------------------------------------
# 5. Classificação TTS — modelos free com text-to-speech em cada catálogo
# --------------------------------------------------------------------------

def _eh_tts(texto):
    t = texto.lower()
    return any(p in t for p in TTS_PADROES)


def buscar_tts(catalogo, modelos):
    """Filtra os modelos TTS gratuitos de um catálogo.

    - NVIDIA: exige 'Free Endpoint' no nimType — modelos apenas 'Download
      Available' são downloads, não endpoints gratuitos (falsos positivos).
    - Zen: catálogo já vem filtrado para os modelos free; busca por palavras-
      chave no id.
    - OpenRouter: catálogo já vem filtrado para os $0; casa palavras-chave OU
      modelos com saída de áudio (text->audio), ex. geradores de fala/áudio.
    """
    if catalogo == "nvidia":
        return [m for m in modelos
                if "Free Endpoint" in m.get("nimType", [])
                and _eh_tts(" ".join([m["nome"], m["slug"], m["descricao"]]))]
    if catalogo == "openrouter":
        return [m for m in modelos
                if m.get("speech_out")
                or _eh_tts(" ".join([m["nome"], m["slug"], m["descricao"]]))]
    return [m for m in modelos
            if _eh_tts(" ".join([m["nome"], m["slug"]]))]


# --------------------------------------------------------------------------
# 6. Saída
# --------------------------------------------------------------------------

def _url_modelo(catalogo, mod):
    if mod.get("url"):
        return mod["url"]
    if catalogo == "nvidia":
        return f"https://build.nvidia.com/{mod['slug']}"
    if catalogo == "zen":
        return f"https://opencode.ai/zen/?model={mod['slug']}"
    return mod["slug"]


def imprimir_ranking_rank(rank):
    extra = ""
    if rank.get("score") is not None:
        extra = f"   [{rank['score']:.1f} pts]"
    tag = "  [OpenAI]" if rank["fabricante"].lower() == "openai" else ""
    print(f"  #{rank['posicao']:>2}  {rank['nome']}{tag}{extra}")


def imprimir_catalogo(indice_titulo, catalogo, matches, apenas_free_zen=False):
    titulo, url_base, nota = CATALOGOS[catalogo]
    extra = nota if (catalogo != "zen" or apenas_free_zen) else ""
    print(f"\n  ◆ {indice_titulo}. {titulo}  ({url_base}){extra}")
    if not matches:
        print("    Nenhum modelo do ranking encontrado neste catálogo.")
        return
    print(f"    ENCONTRADOS: {len(matches)}\n")
    for rank, mod, nota in matches:
        exato = "correspondência exata" if nota == 1.0 else f"similaridade {nota:.0%}"
        imprimir_ranking_rank(rank)
        print(f"        id/slug : {mod['slug']}   ({exato})")
        print(f"        URL     : {_url_modelo(catalogo, mod)}")
        if mod["descricao"]:
            print(f"        {mod['descricao'][:100]}")
        print()


# nomes curtos dos catálogos para a listagem "Modelo - site"
SITE_NOMES = {"nvidia": "Nvidia", "zen": "OpenCode Zen", "openrouter": "OpenRouter"}


def imprimir(resultados_por_tier, rankings, tts_resultados):
    print("\nSmart Models:\n")
    i = 0
    for tier_id, _, _ in TIERS:
        ranking = rankings.get(tier_id, [])
        resultados = resultados_por_tier.get(tier_id, {})
        for rank in ranking:
            # em quais catálogos este modelo do ranking foi encontrado?
            plataformas = []
            for catalogo in CATALOGOS:
                for r_rank, _, _ in resultados.get(catalogo, []):
                    if r_rank["slug"] == rank["slug"]:
                        plataformas.append(SITE_NOMES[catalogo])
                        break
            if not plataformas:
                continue  # só lista o que foi encontrado ("até o último que achar")
            i += 1
            print(f"  {i}. {rank['nome']} - {', '.join(plataformas)}")
    if i == 0:
        print("  (nenhum modelo do ranking foi encontrado nos catálogos)")

    print("\nTTS:\n")
    j = 0
    for catalogo in CATALOGOS:
        for mod in tts_resultados.get(catalogo, []):
            j += 1
            print(f"  {j}. {mod['nome']} - {SITE_NOMES[catalogo]}")
    if j == 0:
        print("  (nenhum modelo TTS free foi encontrado nos catálogos)")


def imprimir_detalhado(resultados_por_tier, rankings, tts_resultados,
                       apenas_free_zen=False):
    """Saída detalhada (antiga), com slugs, URLs, descrições e pontuações."""
    larg = 78
    print("=" * larg)
    print("MODO DETALHADO")
    print("=" * larg)

    for tier_id, tier_titulo, tier_url in TIERS:
        print(f"\n{'#' * larg}")
        print(f"# {tier_titulo}")
        print(f"# {tier_url}")
        print(f"{'#' * larg}")

        ranking = rankings.get(tier_id, [])
        if tier_id == "tier2" and ranking:
            print("Modelos qualificados (top %d, > %.0f pts): %s" % (
                TIER2_TOP_N, TIER2_MIN_SCORE,
                ", ".join(f"{r['nome'].replace(' (free)', '')} ({r['score']:.1f})"
                          for r in ranking)))
        if tier_id == "tier2" and not ranking:
            print(f"Nenhum modelo do top {TIER2_TOP_N} pontuou acima de "
                  f"{TIER2_MIN_SCORE:.0f} pontos.")
            continue

        resultados = resultados_por_tier.get(tier_id, {})
        for i, catalogo in enumerate(CATALOGOS, start=1):
            imprimir_catalogo(i, catalogo, resultados.get(catalogo, []),
                              apenas_free_zen=apenas_free_zen)

    print(f"\n{'#' * larg}")
    print("# CLASSIFICAÇÃO: TTS (TEXT-TO-SPEECH) — apenas modelos free")
    print(f"# critério: nome/id/descrição contém {', '.join(TTS_PADROES)}")
    print(f"{'#' * larg}")
    for i, catalogo in enumerate(CATALOGOS, start=1):
        titulo, url_base, _ = CATALOGOS[catalogo]
        modelos_tts = tts_resultados.get(catalogo, [])
        print(f"\n  ◆ {i}. {titulo}  ({url_base})")
        if not modelos_tts:
            print("    Nenhum modelo TTS free neste catálogo.")
            continue
        print(f"    ENCONTRADOS: {len(modelos_tts)}\n")
        for mod in modelos_tts:
            print(f"    - {mod['nome']}")
            print(f"        id/slug : {mod['slug']}")
            print(f"        URL     : {_url_modelo(catalogo, mod)}")
            if mod["descricao"]:
                print(f"        {mod['descricao'][:100]}")
            print()


def escrever_html(resultados_por_tier, rankings, tts_resultados, gerado_em):
    """Gera um index.html autocontido com os resultados (dados embutidos)."""
    smart = []
    for tier_id, _, _ in TIERS:
        for rank in rankings.get(tier_id, []):
            plataformas = []
            for catalogo in CATALOGOS:
                for r_rank, _, _ in resultados_por_tier.get(tier_id, {}).get(catalogo, []):
                    if r_rank["slug"] == rank["slug"]:
                        plataformas.append(SITE_NOMES[catalogo])
                        break
            if plataformas:
                smart.append({
                    "nome": rank["nome"],
                    "posicao": rank.get("posicao"),
                    "score": rank.get("score"),
                    "plataformas": plataformas,
                })
    tts = [{"nome": m["nome"], "plataforma": SITE_NOMES[c],
            "descricao": (m.get("descricao") or "")[:140]}
           for c in CATALOGOS for m in tts_resultados.get(c, [])]

    dados = json.dumps({"gerado_em": gerado_em.isoformat(timespec="seconds"),
                        "smart": smart, "tts": tts}, ensure_ascii=False)
    chip = {"Nvidia": "#76b900", "OpenCode Zen": "#f5a623",
            "OpenRouter": "#8b5cf6"}
    chips_css = "\n".join(
        f'.chip[data-site="{nome}"]{{background:{cor}22;color:{cor};border:1px solid {cor}55}}'
        for nome, cor in chip.items())
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart Models & TTS — Nvidia / OpenCode Zen / OpenRouter</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 40px 20px; font: 15px/1.5 system-ui, "Segoe UI", sans-serif;
         background: #0d1117; color: #e6edf3; }
  main { max-width: 760px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin: 0 0 4px; }
  h2 { font-size: 1.15rem; margin: 36px 0 12px; padding-bottom: 8px;
       border-bottom: 1px solid #21262d; }
  .sub { color: #8b949e; font-size: .85rem; margin: 0 0 8px; }
  ol { list-style: none; margin: 0; padding: 0; }
  li { display: flex; align-items: baseline; gap: 10px; padding: 9px 12px;
       border-radius: 8px; }
  li:nth-child(odd) { background: #161b22; }
  .num { color: #8b949e; min-width: 26px; text-align: right; font-variant-numeric: tabular-nums; }
  .nome { flex: 1; }
  .score { color: #f5a623; font-size: .8rem; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .chip { font-size: .72rem; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
  """ + chips_css + """
  .vazio { color: #8b949e; font-style: italic; padding: 9px 12px; }
  footer { margin-top: 40px; color: #8b949e; font-size: .78rem;
           border-top: 1px solid #21262d; padding-top: 12px; }
</style>
</head>
<body>
<main>
  <h1>Smart Models &amp; TTS</h1>
  <p class="sub">Rankings do modelgrep cruzados com os catálogos gratuitos de
     Nvidia Build, OpenCode Zen e OpenRouter.</p>
  <section id="smart">
    <h2>Smart Models</h2>
    <ol id="smart-list"></ol>
  </section>
  <section id="tts">
    <h2>TTS</h2>
    <ol id="tts-list"></ol>
  </section>
  <footer id="rodape"></footer>
</main>
<script>
const DADOS = """ + dados + """;

const smart = document.getElementById("smart-list");
if (DADOS.smart.length === 0) {
  smart.innerHTML = '<li class="vazio">nenhum modelo encontrado nos catálogos</li>';
}
for (const [i, m] of DADOS.smart.entries()) {
  const li = document.createElement("li");
  const score = m.score ? ` <span class="score">· ${m.score.toFixed(1)} pts</span>` : "";
  li.innerHTML = `<span class="num">${i + 1}.</span>
    <span class="nome">${m.nome}${score}</span>
    <span class="chips">${m.plataformas.map(p =>
      `<span class="chip" data-site="${p}">${p}</span>`).join("")}</span>`;
  smart.appendChild(li);
}

const tts = document.getElementById("tts-list");
if (DADOS.tts.length === 0) {
  tts.innerHTML = '<li class="vazio">nenhum modelo TTS free encontrado</li>';
}
for (const [i, m] of DADOS.tts.entries()) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="num">${i + 1}.</span>
    <span class="nome">${m.nome}
      ${m.descricao ? `<br><small style="color:#8b949e">${m.descricao}</small>` : ""}</span>
    <span class="chips"><span class="chip" data-site="${m.plataforma}">${m.plataforma}</span></span>`;
  tts.appendChild(li);
}

document.getElementById("rodape").textContent =
  "Gerado em " + new Date(DADOS.gerado_em).toLocaleString("pt-BR") +
  " por comparar_modelos.py";
</script>
</body>
</html>
"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--json", action="store_true", help="salva resultados.json")
    ap.add_argument("--detalhado", action="store_true",
                    help="saída detalhada (slugs, URLs, descrições, pontuações)")
    ap.add_argument("--zen-todos", action="store_true",
                    help="no OpenCode Zen, considera TODOS os modelos (padrão: apenas 'free')")
    ap.add_argument("--fuzzy", action="store_true",
                    help="ativa correspondência aproximada (pode gerar falsos positivos)")
    ap.add_argument("--limiar", type=float, default=0.90,
                    help="corte de similaridade do match fuzzy (0-1, padrão 0.90)")
    args = ap.parse_args()

    catalogos_dados = {
        "nvidia": fetch_nvidia_models(),
        "zen": fetch_zen_models(apenas_free=not args.zen_todos),
        "openrouter": fetch_openrouter_models(),
    }
    rankings = {"tier1": fetch_smartest_models()}
    rankings["tier2"] = fetch_free_top_models()

    resultados_por_tier = {}
    for tier_id, ranking in rankings.items():
        resultados_por_tier[tier_id] = {
            nome: confirmadas + provaveis
            for nome, dados in catalogos_dados.items()
            for confirmadas, provaveis in [comparar(dados, ranking, args.fuzzy, args.limiar)]
        }

    tts_resultados = {
        nome: buscar_tts(nome, dados)
        for nome, dados in catalogos_dados.items()
    }

    escrever_html(resultados_por_tier, rankings, tts_resultados, datetime.now())

    if args.detalhado:
        imprimir_detalhado(resultados_por_tier, rankings, tts_resultados,
                           apenas_free_zen=not args.zen_todos)
    else:
        imprimir(resultados_por_tier, rankings, tts_resultados)

    if args.json:
        saida = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "tiers": {}}
        for tier_id, resultados in resultados_por_tier.items():
            saida["tiers"][tier_id] = {}
            for nome, matches in resultados.items():
                saida["tiers"][tier_id][nome] = [
                    {"ranking_posicao": r["posicao"], "modelgrep": r["nome"],
                     "fabricante": r["fabricante"],
                     **({"score": r["score"]} if "score" in r else {}),
                     "id_slug": m["slug"],
                     "exato": n == 1.0, "similaridade": round(n, 3),
                     "url": _url_modelo(nome, m)}
                    for r, m, n in matches]
        saida["tts"] = {
            nome: [{"id_slug": m["slug"], "nome": m["nome"],
                    "url": _url_modelo(nome, m), "descricao": m["descricao"]}
                   for m in tts_resultados.get(nome, [])]
            for nome in catalogos_dados
        }
        with open("resultados.json", "w", encoding="utf-8") as f:
            json.dump(saida, f, ensure_ascii=False, indent=2)
        print("\nResultados salvos em resultados.json")


if __name__ == "__main__":
    main()
