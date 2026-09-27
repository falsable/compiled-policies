"""Deriva de DATOS con la política intacta (H3).

El texto de la política es idéntico al del bloque (el router por hash diría "reutiliza"), pero las
entradas cambian de forma que la política sigue definiendo la respuesta correcta sin ambigüedad.
Tipos fijados antes de mirar el código de los bloques (ver README §8).

La verdad se calcula SOLO con lo visible (resoluble por construcción) y se comprueba:
  - N1: la ventana de 7 días cruza de mes y un cálculo ingenuo por día del mes da otra respuesta;
  - N2: hay importes con ≥ 2 separadores de miles;
  - D1/D3/D4: la verdad coincide con la de la tarea base (la deriva es solo de formato).

Uso:  python -m benchmark.deriva --por-tipo 5 --salida benchmark/out
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from benchmark import extremo as X
from benchmark import variantes as V
from benchmark.generar import _hash

TIPOS_NEG = ["N1_cambio_mes", "N2_importes_grandes", "N3_lote_grande", "N4_dep_prov_nuevos"]
TIPOS_DAT = ["D1_separador_miles", "D2_region_nueva", "D3_csv_punto_y_coma", "D4_columna_texto"]


# =============================================================== utilidades

def leer_importe(texto: str) -> tuple[float, str, bool]:
    """(base en su moneda, moneda, '+ IVA') a partir del texto visible."""
    sin_iva = "+ IVA" in texto
    t = texto.replace("(IVA incluido)", "").replace("+ IVA", "").strip()
    moneda = "EUR"
    for mon in ("USD", "GBP", "EUR", "€"):
        if mon in t:
            moneda = "EUR" if mon == "€" else mon
            t = t.replace(mon, "").strip()
    t = t.replace(".", "").replace(",", ".") if (moneda == "EUR" and "," in t) else t.replace(",", "")
    return float(t), moneda, sin_iva


def leer_csv_visible(ruta: Path) -> pd.DataFrame:
    """Lectura de referencia: detecta ';' o ',' y entiende coma decimal con punto de miles."""
    primera = ruta.read_text(encoding="utf-8").splitlines()[0]
    sep = csv.Sniffer().sniff(primera, delimiters=",;").delimiter
    df = pd.read_csv(ruta, sep=sep, dtype=str)
    for c in df.columns:
        if c in ("id", "region"):
            continue
        s = df[c].str.strip()
        es = s.str.contains(",", na=False)
        s = s.where(~es, s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
        df[c] = pd.to_numeric(s, errors="coerce")
    return df


def resolver_neg_dias_ingenuos(entradas: dict) -> dict:
    """Como variantes.resolver_neg, pero la diferencia de días se calcula con el día del mes
    (bug típico cuando todo lo visto en validación era del mismo mes). El orden sí es correcto."""
    import benchmark.variantes as VV
    original = VV.date

    class _Fecha(date):
        @classmethod
        def fromisoformat(cls, s):   # solo cuenta el día del mes
            return date(2026, 1, int(s[-2:]))

    try:
        VV.date = _Fecha
        return VV.resolver_neg(entradas, VV.BASE_NEG)
    finally:
        VV.date = original


# =============================================================== negocio

def _neg_base(semilla: int) -> dict:
    t, _ = X.generar_negocio_extremo(semilla)
    return copy.deepcopy(t)


def deriva_negocio(tipo: str, semilla: int, rng) -> dict | None:
    t = _neg_base(semilla)
    e = t["entradas"]
    if tipo == "N1_cambio_mes":
        inicio = date(2026, 9, 20)
        for x in e["entradas"]:
            x["fecha"] = (inicio + timedelta(days=int(x["fecha"][-2:]) - 1)).isoformat()
        real = V.resolver_neg(e, V.BASE_NEG)
        if resolver_neg_dias_ingenuos(e) == real:
            return None   # en esta instancia el bug de meses no cambiaría nada: no sirve
    elif tipo == "N2_importes_grandes":
        grandes = 0
        for x in e["entradas"]:
            if x["tipo"] != "solicitud" or rng.random() > 0.4:
                continue
            base, moneda, sin_iva = leer_importe(x["importe"])
            x["importe"] = X._texto_importe(round(base * 1000, 2), moneda, sin_iva, rng)
            grandes += x["importe"].count(".") + x["importe"].count(",") >= 3
        e["presupuesto_inicial"] = {k: v * 400 for k, v in e["presupuesto_inicial"].items()}
        if grandes < 2:
            return None
    elif tipo == "N3_lote_grande":
        eventos, pres = [], {k: 0.0 for k in X.DEPARTAMENTOS}
        n = 0
        for j in range(7):
            tj = _neg_base(semilla * 10 + j)
            mapa = {}
            for x in tj["entradas"]["entradas"]:
                if x["tipo"] == "solicitud":
                    mapa[x["id"]] = f"SC-{semilla:03d}-{n:03d}"
                    n += 1
            for x in tj["entradas"]["entradas"]:
                y = dict(x)
                if y["tipo"] == "solicitud":
                    y["id"] = mapa[x["id"]]
                else:
                    y["id"] = f"CX-{semilla:03d}-{j}{x['id'][-1]}"
                    y["referencia"] = mapa[x["referencia"]]
                eventos.append(y)
            for k, v in tj["entradas"]["presupuesto_inicial"].items():
                pres[k] += v
        rng.shuffle(eventos)
        e["entradas"], e["presupuesto_inicial"] = eventos, pres
    elif tipo == "N4_dep_prov_nuevos":
        e["presupuesto_inicial"]["legal"] = float(round(rng.uniform(3000, 6000), -2))
        e["proveedores_homologados"] = e["proveedores_homologados"] + ["Lex Proveedores"]
        for x in e["entradas"]:
            if x["tipo"] != "solicitud":
                continue
            if rng.random() < 0.3:
                x["departamento"] = "legal"
            if x["proveedor"] in X.HOMOLOGADOS and rng.random() < 0.25:
                x["proveedor"] = "Lex Proveedores"
    return t


# =============================================================== datos

def _dat_base(semilla: int, dir_datos: Path) -> tuple[dict, pd.DataFrame, Path]:
    t, _ = X.generar_datos_extremo(semilla, dir_datos)
    ruta = dir_datos / t["entradas"]["csv"]
    return t, pd.read_csv(ruta), ruta


def _es_float(v) -> str:
    return f"{v:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")


def deriva_datos(tipo: str, semilla: int, dir_datos: Path, rng) -> tuple[dict, Path, dict] | None:
    t, df, ruta = _dat_base(semilla, dir_datos)
    verdad_base = V.verdad_dat(leer_csv_visible(ruta), V.BASE_DAT)
    num = [c for c in df.columns if c not in ("id", "region")]
    coma = [c for c in num if not pd.api.types.is_numeric_dtype(df[c])]
    if tipo == "D1_separador_miles":
        miles = 0
        for c in coma:
            s = df[c]
            era_coma = s.str.contains(",", na=False)
            v = pd.to_numeric(s.str.replace(",", ".", regex=False), errors="coerce") * 10
            nueva = v.map(lambda x: "" if pd.isna(x) else f"{x:.3f}").astype(object)
            nueva[era_coma] = [_es_float(x) for x in v[era_coma]]
            miles += int(sum("." in x and "," in x for x in nueva[era_coma]))
            df[c] = nueva
        if miles < 20:
            ruta.unlink()
            return None
        df.to_csv(ruta, index=False)
    elif tipo == "D2_region_nueva":
        filas = rng.choice(len(df), size=15, replace=False)
        df.loc[filas, "region"] = "centro"
        df.to_csv(ruta, index=False)
    elif tipo == "D3_csv_punto_y_coma":
        for c in coma:
            df[c] = pd.to_numeric(df[c].str.replace(",", ".", regex=False), errors="coerce")
        df.to_csv(ruta, index=False, sep=";", decimal=",")
    elif tipo == "D4_columna_texto":
        frases = ["revisar con proveedor", "dato validado", "pendiente de cierre", "ok", "sin incidencias",
                  "ver nota 3b", "duplicado en origen?", "cliente VIP", "revisar unidades", "N/D"]
        df.insert(int(rng.integers(2, len(df.columns))), "comentario",
                  [str(rng.choice(frases)) for _ in range(len(df))])
        df.to_csv(ruta, index=False)
    verdad = V.verdad_dat(leer_csv_visible(ruta), V.BASE_DAT)
    if tipo in ("D1_separador_miles", "D3_csv_punto_y_coma", "D4_columna_texto") and verdad != verdad_base:
        raise AssertionError(f"{tipo}/{semilla}: la deriva de formato cambió la verdad")
    return t, ruta, verdad


# =============================================================== main

def generar_deriva(por_tipo: int, salida: Path) -> tuple[list, list]:
    tareas, objetivos = [], []
    dir_datos = salida / "datos"
    for tipo in TIPOS_NEG:
        semilla, k = 300, 0
        rng = np.random.default_rng(55 + TIPOS_NEG.index(tipo))
        while k < por_tipo:
            t = deriva_negocio(tipo, semilla, rng)
            semilla += 1
            if t is None:
                continue
            obj = V.resolver_neg(t["entradas"], V.BASE_NEG)
            tid = f"negocio-deriva-{tipo[:2]}-{k:02d}"
            tareas.append(t | {"id": tid, "nivel": "deriva", "deriva": {"tipo": tipo}, "hash_objetivo": _hash(obj)})
            objetivos.append({"id": tid, "objetivo": obj})
            k += 1
    for tipo in TIPOS_DAT:
        semilla, k = 300, 0
        rng = np.random.default_rng(77 + TIPOS_DAT.index(tipo))
        while k < por_tipo:
            r = deriva_datos(tipo, semilla, dir_datos, rng)
            semilla += 1
            if r is None:
                continue
            t, ruta, obj = r
            tid = f"datos-deriva-{tipo[:2]}-{k:02d}"
            ruta.rename(dir_datos / f"{tid}.csv")
            tareas.append(t | {"id": tid, "nivel": "deriva", "deriva": {"tipo": tipo},
                               "entradas": {"csv": f"{tid}.csv"}, "hash_objetivo": _hash(obj)})
            objetivos.append({"id": tid, "objetivo": obj})
            k += 1
    with open(salida / "deriva.jsonl", "w", encoding="utf-8") as f:
        for t in tareas:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(salida / "objetivos_deriva.jsonl", "w", encoding="utf-8") as f:
        for o in objetivos:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return tareas, objetivos


def autotest(salida: Path) -> dict:
    from benchmark.verificar import verificar
    tareas = [json.loads(l) for l in open(salida / "deriva.jsonl", encoding="utf-8")]
    obj = {o["id"]: o["objetivo"] for o in map(json.loads, open(salida / "objetivos_deriva.jsonl", encoding="utf-8"))}
    for t in tareas:
        texto = t["entradas"]["politica"] if t["dominio"] == "negocio" else t["enunciado"]
        base = X.POLITICA_EXTREMO if t["dominio"] == "negocio" else X.ENUNCIADO_DATOS
        assert texto == base, f"{t['id']}: la política debería ser idéntica a la del bloque"
        ok, det = verificar(t, obj[t["id"]], obj[t["id"]])
        assert ok, (t["id"], det)
    return {"tareas_deriva": len(tareas)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--por-tipo", type=int, default=5)
    ap.add_argument("--salida", type=Path, default=Path("benchmark/out"))
    a = ap.parse_args()
    generar_deriva(a.por_tipo, a.salida)
    print(autotest(a.salida))
