import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

URL_BASE = "https://tabuademares.com/br/paraiba/joao-pessoa"
URL_ONDAS = f"{URL_BASE}/previsao/ondas"
URL_VENTO = f"{URL_BASE}/previsao/vento"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

PAUSA = 1.5

DIR_RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

MESES = {
    "JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12,
}


def baixar_html(url: str, dados_post: dict | None = None) -> str:
    if dados_post is None:
        resp = requests.get(url, headers=HEADERS, timeout=30)
    else:
        resp = requests.post(url, headers=HEADERS, data=dados_post, timeout=30)
    resp.raise_for_status()
    return resp.text


def parsear_mares(html: str, ano: int, mes: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    tabela = soup.find("table", id="tabla_mareas")
    eventos = []
    for linha in tabela.select("tr[onclick^='Day(']"):
        data = datetime.strptime(linha["onclick"].split("'")[1], "%Y-%m-%d").date()
        if (data.year, data.month) != (ano, mes):
            continue

        icone_lua = linha.select_one("td.tabla_mareas_luna [class*='icon-hs']")
        idade_lua = next(
            int(c.removeprefix("icon-hs"))
            for c in icone_lua["class"]
            if c.startswith("icon-hs")
        )
        coef = linha.select_one(".tabla_mareas_coeficiente_numero")
        coeficiente = int(coef.get_text(" ", strip=True).split()[0])
        nascer_sol = linha.select_one(".tabla_mareas_salida_puesta_sol_salida").get_text(strip=True)
        por_sol = linha.select_one(".tabla_mareas_salida_puesta_sol_puesta").get_text(strip=True)

        for celula in linha.select("td.tabla_mareas_marea"):
            hora = celula.select_one(".tabla_mareas_marea_hora")
            altura = celula.select_one(".tabla_mareas_marea_altura_numero")
            if hora is None or altura is None:
                continue
            tipo = "alta" if celula.select_one(".tabla_mareas_marea_pleamar") else "baixa"
            eventos.append(
                {
                    "data": data.isoformat(),
                    "hora": hora.get_text(strip=True),
                    "tipo": tipo,
                    "altura_m": float(altura.get_text(strip=True).replace(",", ".")),
                    "coeficiente": coeficiente,
                    "idade_lua": idade_lua,
                    "nascer_sol": nascer_sol,
                    "por_sol": por_sol,
                }
            )
    return eventos


def parsear_previsao(html: str, ano: int) -> list[dict]:
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
    eventos = []
    for mes in range(1, 13):
        eventos += coletar_mares_do_mes(ano, mes)
    return pd.DataFrame(eventos)


def coletar_mares_do_mes(ano: int, mes: int) -> list[dict]:
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

    arquivos = {}

    print(f"Tábua de marés de {ano}...")
    arquivos[f"mares_{ano}.csv"] = coletar_mares_do_ano(ano)

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
            raise RuntimeError(f"{nome} saiu vazio")
        df.to_csv(DIR_RAW / nome, index=False)
        print(f"{nome:<20} {len(df):>5} linhas")


if __name__ == "__main__":
    main()
