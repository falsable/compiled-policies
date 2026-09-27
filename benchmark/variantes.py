"""Variantes de política para probar la reutilización de bloques (H2).

Un bloque compilado para la política BASE se reutiliza en tareas nuevas. Sirve si la política
nueva es LÓGICAMENTE equivalente:
  - cosmética:   otra redacción, misma lógica          → reutilizar es correcto
  - sustantiva:  cambia un umbral, un %, una ventana o
                 una regla (aunque se parezca mucho)    → reutilizar da una respuesta equivocada

Los motores están parametrizados y, con los parámetros base, coinciden con los de extremo.py
(prueba de paridad). En cada variante sustantiva se exige que la respuesta cambie de verdad
respecto a aplicar la política base: si no cambia, se descarta la semilla.

Uso:  python -m benchmark.variantes --n-cosm 10 --n-sust 10 --salida benchmark/out
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import date
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from benchmark import extremo as X
from benchmark.generar import _hash

# =============================================================== NEGOCIO

BASE_NEG = {"iva": 0.21, "umbral_auto": 500, "umbral_jefe": 5000, "ventana": 7,
            "cat_excepcion": "seguridad", "cancel_devuelve": True}


def _pct(iva: float) -> str:
    return f"{iva + 1:.2f}"


def politica_neg(p: dict, estilo: str = "base") -> str:
    iva, a, j, v, cat = _pct(p["iva"]), p["umbral_auto"], p["umbral_jefe"], p["ventana"], p["cat_excepcion"]
    devuelve = ("Si estaba aprobada, su importe vuelve al presupuesto\ndel departamento."
                if p["cancel_devuelve"] else
                "Aunque estuviera aprobada, su importe NO vuelve al presupuesto\ndel departamento.")
    if estilo == "base":
        txt = f"""POLÍTICA DE COMPRAS
Orden: procesa las entradas por fecha y, a igual fecha, por id (orden alfabético). El lote NO viene ordenado.
Hay dos tipos de entrada: "solicitud" y "cancelacion".

Importes: cada solicitud trae su importe en EUR, USD o GBP.
- EUR usa formato español (1.234,56) o plano (1234.56). USD y GBP usan formato anglosajón (1,234.56).
- Convierte a EUR con "tipos_cambio" (EUR por unidad): eur = round(importe × tipo, 2).
- Si el texto dice "+ IVA", el importe con IVA es round(eur × {iva}, 2); si dice "(IVA incluido)", ya lo es.
Todas las reglas usan el importe con IVA en EUR.

Solicitudes (en este orden):
1. Proveedor que no está en "proveedores_homologados" → estado "rechazada", motivo "proveedor_no_homologado".
   Excepción: categoría "{cat}" y urgente → estado "escalada", aprobador "direccion", motivo "excepcion_seguridad".
2. Importe mayor que el presupuesto restante del departamento → estado "escalada", aprobador "finanzas",
   motivo "sin_presupuesto".
3. En otro caso → estado "aprobada", motivo null, y descuenta el importe del presupuesto del departamento.
   Aprobador según el IMPORTE ACUMULADO: el de esta solicitud más el de las solicitudes ya procesadas que
   sigan aprobadas, del mismo proveedor y departamento, con fecha dentro de los {v} días anteriores
   (incluida la misma fecha). Acumulado ≤ {a} € → "auto"; ≤ {j} € → "jefe_departamento"; > {j} € → "direccion".
   (La regla 2 usa solo el importe de esta solicitud.)
4. revision_it = true si la categoría es "software" y el estado es "aprobada" o "escalada".

Cancelaciones: anulan la solicitud "referencia". Esa solicitud queda con estado "cancelada", aprobador null,
motivo "cancelada_por_solicitante", revision_it false. {devuelve} Las cancelaciones no aparecen en "decisiones".

Redondeo a 2 decimales."""
        return txt
    if estilo == "c1":   # reescritura ligera: misma estructura, otras palabras
        return f"""NORMAS DE COMPRA
Las entradas llegan desordenadas: trátalas en orden de fecha y, si la fecha coincide, en orden alfabético de id.
Cada entrada es de tipo "solicitud" o "cancelacion".

Sobre los importes (cada solicitud viene en EUR, USD o GBP):
- En EUR el número puede venir en formato español (1.234,56) o plano (1234.56); en USD y GBP, en formato anglosajón (1,234.56).
- Pásalo a EUR con "tipos_cambio" (euros por unidad): eur = round(importe × tipo, 2).
- Cuando el texto indique "+ IVA", el importe con IVA vale round(eur × {iva}, 2); con "(IVA incluido)" ya está incluido.
En todas las reglas se usa el importe con IVA expresado en EUR.

Para cada solicitud, aplica en este orden:
1. Si el proveedor no figura en "proveedores_homologados": estado "rechazada" y motivo "proveedor_no_homologado".
   Salvo que sea categoría "{cat}" y urgente: entonces estado "escalada", aprobador "direccion", motivo "excepcion_seguridad".
2. Si el importe supera el presupuesto que le queda al departamento: estado "escalada", aprobador "finanzas",
   motivo "sin_presupuesto".
3. Si no, estado "aprobada", motivo null, y se resta el importe del presupuesto del departamento.
   El aprobador se decide con el IMPORTE ACUMULADO: esta solicitud más las ya procesadas que continúen aprobadas,
   del mismo proveedor y del mismo departamento, fechadas dentro de los {v} días previos (la misma fecha cuenta).
   Acumulado ≤ {a} € → "auto"; ≤ {j} € → "jefe_departamento"; > {j} € → "direccion".
   (Para la regla 2 solo cuenta el importe de la propia solicitud.)
4. revision_it = true cuando la categoría es "software" y el estado es "aprobada" o "escalada".

Una cancelación anula la solicitud indicada en "referencia", que pasa a estado "cancelada", aprobador null,
motivo "cancelada_por_solicitante", revision_it false. {devuelve} Las cancelaciones no se incluyen en "decisiones".

Redondea a 2 decimales."""
    if estilo == "c2":   # reescritura fuerte: otra organización, sin enumeración numérica
        return f"""Política de aprobación de compras — versión resumida

Qué hay en el lote: solicitudes y cancelaciones ("solicitud" / "cancelacion"), sin ordenar.
Cómo recorrerlo: por fecha ascendente; empates por id en orden alfabético.

Importe de trabajo (siempre con IVA y en EUR):
  • moneda EUR → formato español (1.234,56) o plano (1234.56); USD/GBP → formato anglosajón (1,234.56)
  • conversión: eur = round(importe × tipo, 2), con "tipos_cambio" en euros por unidad
  • si pone "+ IVA": con IVA = round(eur × {iva}, 2); si pone "(IVA incluido)": se deja igual

Decisión por solicitud (la primera que aplique):
  a) proveedor fuera de "proveedores_homologados" → "rechazada", motivo "proveedor_no_homologado";
     excepto categoría "{cat}" y urgente → "escalada", aprobador "direccion", motivo "excepcion_seguridad"
  b) importe por encima del presupuesto restante del departamento → "escalada", aprobador "finanzas", motivo "sin_presupuesto"
  c) resto → "aprobada", motivo null; se descuenta del presupuesto del departamento
     aprobador según el acumulado (esta solicitud + las ya procesadas aún aprobadas, mismo proveedor y departamento,
     fecha en los {v} días anteriores incluida la misma): hasta {a} € "auto"; hasta {j} € "jefe_departamento"; más de {j} € "direccion"
     (la comprobación de presupuesto de b) usa solo el importe de esta solicitud)
  revision_it: true si categoría "software" y estado "aprobada" o "escalada"; false en otro caso

Cancelación: la solicitud de "referencia" queda "cancelada", aprobador null, motivo "cancelada_por_solicitante",
revision_it false. {devuelve} No se listan cancelaciones en "decisiones".

Redondeo: 2 decimales."""
    raise ValueError(estilo)


def resolver_neg(entradas: dict, p: dict) -> dict:
    """Motor parametrizado (con BASE_NEG coincide con extremo.resolver_negocio_extremo)."""
    tipos = entradas["tipos_cambio"]
    restante = dict(entradas["presupuesto_inicial"])
    homologados = set(entradas["proveedores_homologados"])
    eventos = sorted(entradas["entradas"], key=lambda e: (e["fecha"], e["id"]))
    info, dec = {}, {}

    def importe(texto: str) -> float:
        t = texto.replace("(IVA incluido)", "").replace("+ IVA", "").strip()
        moneda = "EUR"
        for mon in ("USD", "GBP", "EUR", "€"):
            if mon in t:
                moneda = "EUR" if mon == "€" else mon
                t = t.replace(mon, "").strip()
        t = t.replace(".", "").replace(",", ".") if (moneda == "EUR" and "," in t) else t.replace(",", "")
        eur = round(float(t) * tipos[moneda], 2)
        return round(eur * (1 + p["iva"]), 2) if "+ IVA" in texto else eur

    for e in eventos:
        if e["tipo"] == "cancelacion":
            ref = e["referencia"]
            if ref in dec:
                if dec[ref]["estado"] == "aprobada" and p["cancel_devuelve"]:
                    s = info[ref]
                    restante[s["departamento"]] = round(restante[s["departamento"]] + s["importe"], 2)
                dec[ref] = {"estado": "cancelada", "aprobador": None,
                            "motivo": "cancelada_por_solicitante", "revision_it": False}
            continue
        imp = importe(e["importe"])
        info[e["id"]] = {"departamento": e["departamento"], "proveedor": e["proveedor"],
                         "fecha": e["fecha"], "importe": imp}
        d = {"estado": None, "aprobador": None, "motivo": None, "revision_it": False}
        if e["proveedor"] not in homologados:
            if e["categoria"] == p["cat_excepcion"] and e["urgente"]:
                d.update(estado="escalada", aprobador="direccion", motivo="excepcion_seguridad")
            else:
                d.update(estado="rechazada", motivo="proveedor_no_homologado")
        elif imp > restante[e["departamento"]] + 1e-9:
            d.update(estado="escalada", aprobador="finanzas", motivo="sin_presupuesto")
        else:
            acum = imp
            f0 = date.fromisoformat(e["fecha"])
            for sid, s in info.items():
                if sid == e["id"] or dec.get(sid, {}).get("estado") != "aprobada":
                    continue
                if (s["proveedor"], s["departamento"]) == (e["proveedor"], e["departamento"]) \
                        and 0 <= (f0 - date.fromisoformat(s["fecha"])).days <= p["ventana"]:
                    acum += s["importe"]
            acum = round(acum, 2)
            ap = "auto" if acum <= p["umbral_auto"] else "jefe_departamento" if acum <= p["umbral_jefe"] else "direccion"
            d.update(estado="aprobada", aprobador=ap)
            restante[e["departamento"]] = round(restante[e["departamento"]] - imp, 2)
        d["revision_it"] = e["categoria"] == "software" and d["estado"] in ("aprobada", "escalada")
        dec[e["id"]] = d
    return {"decisiones": dec, "presupuesto_final": {k: round(v, 2) for k, v in restante.items()}}


SUSTANTIVAS_NEG = {
    "S1_umbrales": {"umbral_auto": 750, "umbral_jefe": 4000},        # cambian números
    "S2_iva_ventana": {"iva": 0.10, "ventana": 14},                   # cambian números
    "S3_cancelacion": {"cancel_devuelve": False},                     # cambia la lógica, no los números
    "S4_categoria": {"cat_excepcion": "hardware"},                    # cambia un término entre comillas
}

# =============================================================== DATOS

BASE_DAT = {"z": 4.5, "max_vacios": 0.20, "ambito": "region", "dedup": "first"}


def enunciado_dat(p: dict, estilo: str = "base") -> str:
    z, mv = p["z"], int(round(p["max_vacios"] * 100))
    primera = "PRIMERA" if p["dedup"] == "first" else "ÚLTIMA"
    if p["ambito"] == "region":
        ambito_b = "calculada DENTRO DE SU 'region' (ignorando ausentes)"
        mad_b = "Si en una region la MAD de una columna es 0, esa columna no marca anomalías en esa region."
    else:
        ambito_b = "calculada sobre TODA la columna, sin separar por 'region' (ignorando ausentes)"
        mad_b = "Si la MAD de una columna es 0, esa columna no marca anomalías."
    if estilo == "base":
        return ("En el CSV adjunto encuentra el estado final {\"par\": [col_a, col_b], \"anomalias\": [ids]} "
                "(listas ordenadas) aplicando EXACTAMENTE estas reglas:\n"
                f"0. Si un 'id' aparece repetido, quédate solo con su {primera} aparición. Las celdas vacías son "
                "valores ausentes. Algunos valores numéricos usan coma decimal (p. ej. \"12,5\"): son números.\n"
                f"1. Relación principal: entre las columnas numéricas con como mucho un {mv} % de valores ausentes, "
                "el par con mayor correlación de Pearson en valor absoluto, calculada con las filas donde ambas "
                "columnas tienen valor.\n"
                "2. Filas anómalas: una fila es anómala si alguno de sus valores no ausentes, en CUALQUIER columna "
                f"numérica, se aleja más de {z} desviaciones robustas (MAD × 1.4826) de la mediana de esa columna "
                f"{ambito_b}. {mad_b}")
    if estilo == "c1":
        return ("Con el CSV adjunto, devuelve {\"par\": [col_a, col_b], \"anomalias\": [ids]} con ambas listas "
                "ordenadas, siguiendo al pie de la letra estas reglas:\n"
                f"0. Ante un 'id' repetido, conserva únicamente su {primera} aparición. Una celda vacía es un valor "
                "ausente. Hay números escritos con coma decimal (por ejemplo \"12,5\"): trátalos como números.\n"
                f"1. Relación principal: de las columnas numéricas cuyo porcentaje de ausentes no pase del {mv} %, "
                "elige el par con la correlación de Pearson más alta en valor absoluto, usando solo las filas en las "
                "que las dos columnas tienen dato.\n"
                "2. Filas anómalas: es anómala la fila en la que algún valor presente, de CUALQUIER columna numérica, "
                f"dista más de {z} desviaciones robustas (MAD × 1.4826) de la mediana de su columna, {ambito_b}. {mad_b}")
    if estilo == "c2":
        return ("Tarea sobre el CSV adjunto. Salida: {\"par\": [col_a, col_b], \"anomalias\": [ids]} (listas ordenadas).\n"
                f"• Duplicados de 'id': vale la {primera} fila; el resto se ignora.\n"
                "• Vacío = ausente. Valores como \"12,5\" usan coma decimal y son números.\n"
                f"• Par principal: máximo |Pearson| (pairwise, filas con ambos valores) entre columnas numéricas con ≤ {mv} % de ausentes.\n"
                f"• Anomalía: fila con algún valor presente, en cualquier columna numérica, a más de {z} × (MAD × 1.4826) "
                f"de la mediana de la columna, {ambito_b}. {mad_b}")
    raise ValueError(estilo)


def verdad_dat(df: pd.DataFrame, p: dict):
    """Criterio parametrizado (con BASE_DAT coincide con extremo.verdad_datos_extremo)."""
    df = df.copy()
    for c in df.columns:
        if c not in ("id", "region"):
            df[c] = X._a_numero(df[c], True)
    df = df.drop_duplicates(subset="id", keep=p["dedup"])
    num = [c for c in df.columns if c not in ("id", "region")]
    eleg = [c for c in num if df[c].isna().mean() <= p["max_vacios"] + 1e-12]
    corr = df[eleg].corr()
    pares = sorted(((abs(corr.loc[a, b]), a, b) for a, b in combinations(eleg, 2)), reverse=True)
    par = sorted([pares[0][1], pares[0][2]])
    if p["ambito"] == "region":
        z = pd.concat([df.groupby("region")[c].transform(X._zrob) for c in num], axis=1)
    else:
        z = pd.concat([X._zrob(df[c]) for c in num], axis=1)
    z = z.where(df[num].notna().values, 0.0)
    anom = sorted(df.loc[(z > p["z"]).any(axis=1), "id"].unique().tolist())
    return {"par": par, "anomalias": anom}


SUSTANTIVAS_DAT = {
    "S1_umbral_z": {"z": 3.5},                 # cambia un número
    "S2_vacios": {"max_vacios": 0.35},         # cambia un número (el señuelo pasa a ser elegible)
    "S3_ambito": {"ambito": "global"},         # cambia la lógica, no los números
    "S4_dedup": {"dedup": "last"},             # cambia la lógica, no los números
}

# =============================================================== generación


def _texto_politica(t: dict) -> str:
    return t["entradas"]["politica"] if t["dominio"] == "negocio" else t["enunciado"]


def _hash_texto(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def generar_variantes(n_cosm: int, n_sust: int, salida: Path) -> tuple[list, list]:
    tareas, objetivos = [], []
    dir_datos = salida / "datos"
    sust_neg, sust_dat = list(SUSTANTIVAS_NEG), list(SUSTANTIVAS_DAT)
    # ---- negocio
    semilla = 100
    for k in range(n_cosm + n_sust):
        tipo = "cosmetica" if k < n_cosm else "sustantiva"
        nombre = ("c1" if k % 2 == 0 else "c2") if tipo == "cosmetica" else sust_neg[(k - n_cosm) % len(sust_neg)]
        while True:
            t, _ = X.generar_negocio_extremo(semilla)
            semilla += 1
            p = dict(BASE_NEG) if tipo == "cosmetica" else dict(BASE_NEG) | SUSTANTIVAS_NEG[nombre]
            estilo = nombre if tipo == "cosmetica" else "base"
            entradas = copy.deepcopy(t["entradas"])
            entradas["politica"] = politica_neg(p, estilo)
            obj = resolver_neg(entradas, p)
            if tipo == "sustantiva" and obj == resolver_neg(entradas, BASE_NEG):
                continue   # la variante no cambia nada en esta instancia: no sirve de prueba
            break
        tid = f"negocio-var-{k:03d}"
        tareas.append(t | {"id": tid, "nivel": "variante", "entradas": entradas,
                           "variante": {"tipo": tipo, "nombre": nombre}, "_params": p,
                           "hash_objetivo": _hash(obj)})
        objetivos.append({"id": tid, "objetivo": obj})
    # ---- datos
    semilla = 100
    for k in range(n_cosm + n_sust):
        tipo = "cosmetica" if k < n_cosm else "sustantiva"
        nombre = ("c1" if k % 2 == 0 else "c2") if tipo == "cosmetica" else sust_dat[(k - n_cosm) % len(sust_dat)]
        while True:
            t, _ = X.generar_datos_extremo(semilla, dir_datos)
            semilla += 1
            df = pd.read_csv(dir_datos / t["entradas"]["csv"])
            p = dict(BASE_DAT) if tipo == "cosmetica" else dict(BASE_DAT) | SUSTANTIVAS_DAT[nombre]
            obj = verdad_dat(df, p)
            if tipo == "sustantiva" and obj == verdad_dat(df, BASE_DAT):
                (dir_datos / t["entradas"]["csv"]).unlink()
                continue
            break
        tid = f"datos-var-{k:03d}"
        (dir_datos / t["entradas"]["csv"]).rename(dir_datos / f"{tid}.csv")
        estilo = nombre if tipo == "cosmetica" else "base"
        tareas.append(t | {"id": tid, "nivel": "variante", "enunciado": enunciado_dat(p, estilo),
                           "entradas": {"csv": f"{tid}.csv"},
                           "variante": {"tipo": tipo, "nombre": nombre}, "_params": p,
                           "hash_objetivo": _hash(obj)})
        objetivos.append({"id": tid, "objetivo": obj})
    with open(salida / "variantes.jsonl", "w", encoding="utf-8") as f:
        for t in tareas:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(salida / "objetivos_variantes.jsonl", "w", encoding="utf-8") as f:
        for o in objetivos:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return tareas, objetivos


def autotest(salida: Path) -> dict:
    """Paridad con extremo.py, plantillas base idénticas, resolubilidad y 'la sustantiva cambia algo'."""
    from benchmark.verificar import verificar
    assert politica_neg(BASE_NEG) == X.POLITICA_EXTREMO, "la plantilla base de negocio no es idéntica"
    assert enunciado_dat(BASE_DAT) == X.ENUNCIADO_DATOS, "la plantilla base de datos no es idéntica"
    base = [json.loads(l) for l in open(salida / "tareas.jsonl", encoding="utf-8")]
    n_par = 0
    for t in base:
        if t["nivel"] != "extremo":
            continue
        if t["dominio"] == "negocio":
            assert resolver_neg(t["entradas"], BASE_NEG) == X.resolver_negocio_extremo(t["entradas"]), t["id"]
        else:
            df = pd.read_csv(salida / "datos" / t["entradas"]["csv"])
            par, anom, _ = X.verdad_datos_extremo(df)
            assert verdad_dat(df, BASE_DAT) == {"par": par, "anomalias": anom}, t["id"]
        n_par += 1
    var = [json.loads(l) for l in open(salida / "variantes.jsonl", encoding="utf-8")]
    obj = {o["id"]: o["objetivo"] for o in map(json.loads, open(salida / "objetivos_variantes.jsonl", encoding="utf-8"))}
    base_txt = {"negocio": X.POLITICA_EXTREMO, "datos": X.ENUNCIADO_DATOS}
    for t in var:
        ok, det = verificar(t, obj[t["id"]], obj[t["id"]])
        assert ok, (t["id"], det)
        texto = _texto_politica(t)
        if t["variante"]["tipo"] == "cosmetica":
            assert texto != base_txt[t["dominio"]], f"{t['id']}: la cosmética es idéntica a la base"
        else:
            if t["dominio"] == "negocio":
                assert resolver_neg(t["entradas"], BASE_NEG) != obj[t["id"]], t["id"]
            else:
                df = pd.read_csv(salida / "datos" / t["entradas"]["csv"])
                assert verdad_dat(df, BASE_DAT) != obj[t["id"]], t["id"]
    return {"paridad_extremo": n_par, "variantes": len(var),
            "cosmeticas": sum(t["variante"]["tipo"] == "cosmetica" for t in var),
            "sustantivas": sum(t["variante"]["tipo"] == "sustantiva" for t in var)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-cosm", type=int, default=10)
    ap.add_argument("--n-sust", type=int, default=10)
    ap.add_argument("--salida", type=Path, default=Path("benchmark/out"))
    a = ap.parse_args()
    generar_variantes(a.n_cosm, a.n_sust, a.salida)
    print(autotest(a.salida))
