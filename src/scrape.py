"""
Etapa 1 — coleta de dados de tabuademares.com/br/paraiba/joao-pessoa.

Objetivo: gravar em data/raw/ os CSVs que as Etapas 2 e 3 vão consumir.

    mares_<ano>.csv      tábua de marés do ano (4 marés por dia)
    mares_previsao.csv   marés do mês corrente, para cruzar com a previsão
    ondas.csv            altura de onda hora a hora (~7 dias à frente)
    vento.csv            velocidade do vento hora a hora (~7 dias à frente)

As funções abaixo estão vazias de propósito. Abra o site no navegador, use o
DevTools para descobrir onde cada dado vive no HTML e implemente o parse.
As colunas de cada CSV são sua decisão — só precisam sustentar as etapas
seguintes (ver README).

Rodar com: uv run python src/scrape.py
"""

import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# Os imports acima são o ponto de partida: você vai usar todos eles.
# Seu editor pode marcá-los como não usados até você preencher as funções.

URL_BASE = "https://tabuademares.com/br/paraiba/joao-pessoa"
URL_ONDAS = f"{URL_BASE}/previsao/ondas"
URL_VENTO = f"{URL_BASE}/previsao/vento"

# Servidores rejeitam clientes sem User-Agent. Identifique-se.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# Segundos de pausa entre requests. Não tire isso.
PAUSA = 1.5

DIR_RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

# Abreviações de mês que aparecem nos blocos de dia das previsões ("02 OUT").
MESES = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}


def baixar_html(url: str, dados_post: dict | None = None) -> str:
    """Baixa uma página e devolve o HTML.

    Use POST (passando `dados_post`) quando a página só devolver o conteúdo
    que você quer em resposta a um formulário; GET no resto.

    """
    if dados_post is None:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    else:
        resp = requests.post(url, headers=HEADERS, data=dados_post, timeout=30)
    resp.raise_for_status()
    return resp.text


def parsear_mares(html: str, ano: int, mes: int) -> list[dict]:
    """Extrai da tábua mensal uma linha por evento de maré.

    Cada dia tem cerca de 4 eventos (duas altas, duas baixas). Além de
    horário e altura, a página traz informação de coeficiente de maré e de
    fase da lua — decida o que vale a pena capturar.

    Estrutura (nomes em espanhol): table#tabla_mareas tem uma <tr> por dia com
    onclick="Day('AAAA-M-D')"; dentro dela, 4 td.tabla_mareas_marea, cada um
    com hora, tipo (classe ..._bajamar = baixa, ..._pleamar = alta) e altura.
    A fase da lua vem como classe do ícone, icon-hsN, onde N (0 a 29) é a
    idade da lua em dias: 0 = nova, ~15 = cheia.
    """
    soup = BeautifulSoup(html, "lxml")
    tabela = soup.find("table", id="tabla_mareas")
    eventos = []
    for linha in tabela.select("tr[onclick^='Day(']"):
        # "Day('2026-10-1');" -> date(2026, 10, 1)
        data = datetime.strptime(linha["onclick"].split("'")[1], "%Y-%m-%d").date()
        if (data.year, data.month) != (ano, mes):
            continue  # garante que o POST devolveu o mês pedido

        icone_lua = linha.select_one("td.tabla_mareas_luna [class*='icon-hs']")
        idade_lua = next(
            int(c.removeprefix("icon-hs"))
            for c in icone_lua["class"]
            if c.startswith("icon-hs")
        )
        # o número vem junto do texto "médio" do div filho; o primeiro token é o número
        coef = linha.select_one(".tabla_mareas_coeficiente_numero")
        coeficiente = int(coef.get_text(" ", strip=True).split()[0])

        for celula in linha.select("td.tabla_mareas_marea"):
            hora = celula.select_one(".tabla_mareas_marea_hora")
            altura = celula.select_one(".tabla_mareas_marea_altura_numero")
            if hora is None or altura is None:
                continue  # dias com só 3 marés deixam a 4ª célula vazia
            tipo = "alta" if celula.select_one(".tabla_mareas_marea_pleamar") else "baixa"
            eventos.append(
                {
                    "data": data.isoformat(),
                    "hora": hora.get_text(strip=True),
                    "tipo": tipo,
                    "altura_m": float(altura.get_text(strip=True).replace(",", ".")),
                    "coeficiente": coeficiente,
                    "idade_lua": idade_lua,
                }
            )
    return eventos


def parsear_previsao(html: str, ano: int) -> list[dict]:
    """Extrai leituras horárias das páginas de previsão de onda e de vento.

    As duas páginas têm o mesmo layout: um bloco por dia, com uma linha por
    hora dentro. Uma função só deve dar conta das duas.

    Estrutura: um div.ficha por dia, com .dia ("02") e .mes ("OUT"). Cada
    hora é um div.f_temp_horas com dois .f_temp_hora (hora e direção) e a
    barra .grafico_temp_barra_relleno, cujo texto traz o valor com unidade
    ("1,2 m" na de ondas, "15 km/h" na de vento) — por isso a regex.

    Atenção à data: o bloco mostra dia e mês abreviado, sem o ano. Começamos
    em `ano` e, se a data andar para trás (DEZ -> JAN), avançamos um ano.
    """
    soup = BeautifulSoup(html, "lxml")
    leituras = []
    data_anterior = None
    for bloco in soup.select("div.ficha"):
        dia = int(bloco.select_one(".dia").get_text(strip=True))
        mes = MESES[bloco.select_one(".mes").get_text(strip=True).upper()]
        data = date(ano, mes, dia)
        if data_anterior and data < data_anterior:
            ano += 1
            data = date(ano, mes, dia)
        data_anterior = data

        for linha in bloco.select("div.f_temp_horas"):
            hora, direcao = (el.get_text(strip=True) for el in linha.select(".f_temp_hora")[:2])
            texto_valor = linha.select_one(".grafico_temp_barra_relleno").get_text(" ", strip=True)
            numero = re.search(r"\d+(?:,\d+)?", texto_valor).group()
            leituras.append(
                {
                    "data": data.isoformat(),
                    "hora": hora,
                    "valor": float(numero.replace(",", ".")),
                    "direcao": direcao,
                }
            )
    return leituras


def coletar_mares_do_ano(ano: int) -> pd.DataFrame:
    """Junta os 12 meses da tábua de marés de `ano` num DataFrame.

    """
    eventos = []
    for mes in range(1, 13):
        eventos += coletar_mares_do_mes(ano, mes)
    return pd.DataFrame(eventos)


def coletar_mares_do_mes(ano: int, mes: int) -> list[dict]:
    """Pede um mês da tábua (POST com o campo `fecha` do formulário da página)."""
    html = baixar_html(URL_BASE, {"fecha": f"{ano}-{mes:02d}-01"})
    eventos = parsear_mares(html, ano, mes)
    print(f"  marés {ano}-{mes:02d}: {len(eventos)} eventos")
    time.sleep(PAUSA)
    return eventos


def coletar_previsao(url: str, ano: int, coluna_valor: str, coluna_direcao: str) -> pd.DataFrame:
    html = baixar_html(url)
    time.sleep(PAUSA)
    df = pd.DataFrame(parsear_previsao(html, ano))
    return df.rename(columns={"valor": coluna_valor, "direcao": coluna_direcao})


def main(ano: int = 2025) -> None:
    DIR_RAW.mkdir(parents=True, exist_ok=True)
    hoje = datetime.now()

    # TODO: montar os quatro CSVs em DIR_RAW.
    #
    #   1. tábua de marés do ano inteiro   -> mares_{ano}.csv
    #   2. marés do mês corrente           -> mares_previsao.csv
    #   3. previsão de ondas               -> ondas.csv
    #   4. previsão de vento               -> vento.csv
    #
    # Imprima quantas linhas cada arquivo recebeu: um CSV vazio é o erro mais
    # comum e o mais silencioso.
    arquivos = {}

    print(f"Tábua de marés de {ano}...")
    arquivos[f"mares_{ano}.csv"] = coletar_mares_do_ano(ano)

    # A previsão cobre ~7 dias e pode atravessar a virada do mês, então
    # baixamos o mês corrente e o seguinte para não faltar maré na junção.
    print("Marés do período da previsão...")
    proximo = date(hoje.year + hoje.month // 12, hoje.month % 12 + 1, 1)
    arquivos["mares_previsao.csv"] = pd.DataFrame(
        coletar_mares_do_mes(hoje.year, hoje.month)
        + coletar_mares_do_mes(proximo.year, proximo.month)
    )

    print("Previsões de onda e vento...")
    arquivos["ondas.csv"] = coletar_previsao(URL_ONDAS, hoje.year, "altura_m", "direcao_onda")
    arquivos["vento.csv"] = coletar_previsao(URL_VENTO, hoje.year, "vento_kmh", "direcao_vento")

    print()
    for nome, df in arquivos.items():
        if df.empty:
            raise RuntimeError(f"{nome} saiu vazio — o layout do site mudou?")
        df.to_csv(DIR_RAW / nome, index=False)
        print(f"{nome:<20} {len(df):>5} linhas")


if __name__ == "__main__":
    main()
