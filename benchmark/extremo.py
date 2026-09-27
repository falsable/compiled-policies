"""Nivel EXTREMO del benchmark: tareas donde el enunciado importa y las trampas están activas.

Cada tarea se regenera hasta que:
  (1) la verdad calculada SOLO con lo visible coincide con lo sembrado (resoluble), y
  (2) cada trampa está ACTIVA: si te la saltas, la respuesta cambia.

datos    — ids duplicados (quédate con la primera), celdas vacías, columnas con >20 % de vacíos
           excluidas del par, anomalías por 'region' (no globales).
negocio  — lote desordenado, importes en USD/GBP con formato anglosajón, cancelaciones que
           devuelven presupuesto, fraccionamiento (suma de 7 días para elegir aprobador).
"""
from __future__ import annotations

import json
from datetime import date
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

UMBRAL_Z = 4.5
MAX_VACIOS = 0.20
IVA = 0.21
REGIONES = ["norte", "sur", "este", "oeste"]


# =============================================================== DATOS

ENUNCIADO_DATOS = (
    "En el CSV adjunto encuentra el estado final {{\"par\": [col_a, col_b], \"anomalias\": [ids]}} "
    "(listas ordenadas) aplicando EXACTAMENTE estas reglas:\n"
    "0. Si un 'id' aparece repetido, quédate solo con su PRIMERA aparición. Las celdas vacías son "
    "valores ausentes. Algunos valores numéricos usan coma decimal (p. ej. \"12,5\"): son números.\n"
    "1. Relación principal: entre las columnas numéricas con como mucho un 20 % de valores ausentes, "
    "el par con mayor correlación de Pearson en valor absoluto, calculada con las filas donde ambas "
    "columnas tienen valor.\n"
    "2. Filas anómalas: una fila es anómala si alguno de sus valores no ausentes, en CUALQUIER columna "
    "numérica, se aleja más de {z} desviaciones robustas (MAD × 1.4826) de la mediana de esa columna "
    "calculada DENTRO DE SU 'region' (ignorando ausentes). Si en una region la MAD de una columna es 0, "
    "esa columna no marca anomalías en esa region."
).format(z=UMBRAL_Z)


def _zrob(x: pd.Series, mad0: bool = True) -> pd.Series:
    med = x.median()
    mad = (x - med).abs().median() * 1.4826
    if mad == 0:
        if mad0:   # regla del enunciado: esa columna no marca anomalías en esa región
            return pd.Series(0.0, index=x.index)
        return pd.Series(np.where(x != med, np.inf, 0.0), index=x.index)   # versión ingenua
    return (x - med).abs() / mad


def _a_numero(col: pd.Series, coma: bool = True) -> pd.Series:
    if pd.api.types.is_numeric_dtype(col):
        return col
    s = col.astype(str)
    if coma:
        s = s.str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def verdad_datos_extremo(df: pd.DataFrame, *, dedup=True, excluir=True, por_region=True,
                         coma=True, mad0=True):
    """Criterio del enunciado. Los flags permiten comprobar que cada trampa está activa."""
    df = df.copy()
    for c in df.columns:
        if c not in ("id", "region"):
            df[c] = _a_numero(df[c], coma)
    if dedup:
        df = df.drop_duplicates(subset="id", keep="first")
    num = [c for c in df.columns if c not in ("id", "region")]
    elegibles = [c for c in num if df[c].isna().mean() <= MAX_VACIOS] if excluir else num
    corr = df[elegibles].corr()   # pairwise complete por defecto
    pares = sorted(((abs(corr.loc[a, b]), a, b) for a, b in combinations(elegibles, 2)), reverse=True)
    par = sorted([pares[0][1], pares[0][2]])
    margen = pares[0][0] - pares[1][0]
    if por_region:
        z = pd.concat([df.groupby("region")[c].transform(lambda s: _zrob(s, mad0)) for c in num], axis=1)
    else:
        z = pd.concat([_zrob(df[c], mad0) for c in num], axis=1)
    z = z.where(df[num].notna().values, 0.0)
    anom = sorted(df.loc[(z > UMBRAL_Z).any(axis=1), "id"].unique().tolist())
    return par, anom, margen


COLUMNAS = ["ventas", "visitas", "temperatura", "devoluciones", "tickets_soporte", "tiempo_entrega",
            "precio_medio", "descuento", "stock", "resenas", "clics", "conversiones", "coste_envio",
            "humedad", "incidencias", "horas_extra"]


def generar_datos_extremo(semilla: int, dir_datos: Path) -> tuple[dict, dict]:
    n, m, r, r_decoy = 1500, 12, 0.70, 0.92
    for intento in range(300):
        rng = np.random.default_rng(900_000 + semilla * 1000 + intento)
        cols = list(rng.choice(COLUMNAS, size=m, replace=False))
        orden = rng.permutation(m)
        a, b, dc, de = orden[:4]                  # par sembrado; señuelo (dc con muchos vacíos) ~ de
        Z = rng.standard_normal((n, m))
        Z[:, b] = r * Z[:, a] + np.sqrt(1 - r**2) * rng.standard_normal(n)
        Z[:, dc] = r_decoy * Z[:, de] + np.sqrt(1 - r_decoy**2) * rng.standard_normal(n)
        for c, d in [(orden[4], orden[5]), (orden[6], orden[7])]:
            Z[:, d] = 0.4 * Z[:, c] + np.sqrt(1 - 0.16) * rng.standard_normal(n)
        region = rng.choice(REGIONES, size=n, p=[0.3, 0.3, 0.2, 0.2])
        g = np.array([REGIONES.index(x) for x in region])
        offset = rng.normal(0, 1.0, (4, m))
        escala = rng.uniform(0.6, 1.6, (4, m))
        X = offset[g] + escala[g] * Z
        # anomalías dentro de su región, en columnas que no son del par ni del señuelo
        libres = [int(c) for c in orden[4:]]
        filas = rng.choice(n, size=6, replace=False)
        for f in filas:
            c = int(rng.choice(libres))
            X[f, c] = offset[g[f], c] + rng.choice([-1, 1]) * 7.5 * escala[g[f], c]
        # la primera anomalía cae en una columna que luego llevará coma decimal
        cols_coma = [int(libres[0]), int(libres[1])]
        c0 = cols_coma[0]
        X[filas[0], c0] = offset[g[filas[0]], c0] + 7.5 * escala[g[filas[0]], c0]
        mu = rng.uniform(10, 1000, m)
        sd = mu * rng.uniform(0.05, 0.25, m)
        df = pd.DataFrame(np.round(X * sd + mu, 3), columns=cols)
        # vacíos: 5 % en columnas normales (sin tocar las celdas anómalas), 30 % en el señuelo
        for j in range(m):
            frac = 0.30 if j == dc else 0.05
            mask = rng.random(n) < frac
            mask[filas] = False
            df.iloc[mask, j] = np.nan
        # columna discreta casi siempre 0: MAD = 0 en todas las regiones
        df["averias"] = rng.poisson(0.3, n).astype(float)
        df.insert(0, "region", region)
        df.insert(0, "id", [f"r{i:05d}" for i in range(n)])
        # duplicados: copia de una fila normal con un valor extremo; va DESPUÉS de la original
        normales = [i for i in range(n) if i not in set(filas)]
        dups = []
        for i in rng.choice(normales, size=4, replace=False):
            fila = df.iloc[int(i)].copy()
            c = cols[int(rng.choice(libres))]
            fila[c] = mu[cols.index(c)] + 30 * sd[cols.index(c)]
            dups.append((int(i), fila))
        partes = []
        for i, fila in sorted(dups, key=lambda x: x[0]):
            corte = int(rng.integers(i + 1, n + 1))
            partes.append((corte, fila))
        df_final = df.copy()
        for corte, fila in sorted(partes, key=lambda x: -x[0]):
            df_final = pd.concat([df_final.iloc[:corte], fila.to_frame().T, df_final.iloc[corte:]], ignore_index=True)
        for c in cols + ["averias"]:
            df_final[c] = pd.to_numeric(df_final[c])
        # coma decimal en ~15 % de los valores de dos columnas (y siempre en la celda anómala)
        for j in cols_coma:
            c = cols[j]
            col = df_final[c].astype(object)
            marcar = (rng.random(len(df_final)) < 0.15) | (df_final["id"] == df.loc[filas[0], "id"]).values
            marcar &= df_final[c].notna().values
            col[marcar] = [f"{v:.3f}".replace(".", ",") for v in df_final.loc[marcar, c]]
            df_final[c] = col

        import io
        buf = io.StringIO()
        df_final.to_csv(buf, index=False)
        df_final = pd.read_csv(io.StringIO(buf.getvalue()))   # exactamente lo que verá el agente
        par, anom, margen = verdad_datos_extremo(df_final)
        esperadas = sorted(df.loc[filas, "id"].tolist())
        if par != sorted([cols[a], cols[b]]) or anom != esperadas or margen < 0.08:
            continue
        # trampas activas
        if verdad_datos_extremo(df_final, excluir=False)[0] == par:
            continue
        if verdad_datos_extremo(df_final, por_region=False)[1] == anom:
            continue
        if verdad_datos_extremo(df_final, dedup=False)[1] == anom:
            continue
        if verdad_datos_extremo(df_final, coma=False)[1] == anom:
            continue
        if verdad_datos_extremo(df_final, mad0=False)[1] == anom:
            continue
        break
    else:
        raise RuntimeError(f"datos extremo {semilla}: no se logró una tarea válida")

    tarea_id = f"datos-extremo-{semilla:03d}"
    dir_datos.mkdir(parents=True, exist_ok=True)
    df_final.to_csv(dir_datos / f"{tarea_id}.csv", index=False)
    return ({"id": tarea_id, "dominio": "datos", "nivel": "extremo", "semilla": semilla,
             "enunciado": ENUNCIADO_DATOS, "entradas": {"csv": f"{tarea_id}.csv"},
             "verificador": {"id": "datos_exacto", "tipo": "determinista"}},
            {"par": par, "anomalias": anom})


# =============================================================== NEGOCIO

HOMOLOGADOS = ["Acme Suministros", "Iberdata", "Norte Oficina", "TecnoLevante", "SegurPro"]
NO_HOMOLOGADOS = ["Global Bargains", "QuickParts Ltd", "Ofertas Mediterráneo"]
DEPARTAMENTOS = ["marketing", "operaciones", "it", "rrhh"]
CATEGORIAS = ["material_oficina", "software", "hardware", "seguridad", "formacion"]
TIPOS_CAMBIO = {"EUR": 1.0, "USD": 0.92, "GBP": 1.17}

POLITICA_EXTREMO = """POLÍTICA DE COMPRAS
Orden: procesa las entradas por fecha y, a igual fecha, por id (orden alfabético). El lote NO viene ordenado.
Hay dos tipos de entrada: "solicitud" y "cancelacion".

Importes: cada solicitud trae su importe en EUR, USD o GBP.
- EUR usa formato español (1.234,56) o plano (1234.56). USD y GBP usan formato anglosajón (1,234.56).
- Convierte a EUR con "tipos_cambio" (EUR por unidad): eur = round(importe × tipo, 2).
- Si el texto dice "+ IVA", el importe con IVA es round(eur × 1.21, 2); si dice "(IVA incluido)", ya lo es.
Todas las reglas usan el importe con IVA en EUR.

Solicitudes (en este orden):
1. Proveedor que no está en "proveedores_homologados" → estado "rechazada", motivo "proveedor_no_homologado".
   Excepción: categoría "seguridad" y urgente → estado "escalada", aprobador "direccion", motivo "excepcion_seguridad".
2. Importe mayor que el presupuesto restante del departamento → estado "escalada", aprobador "finanzas",
   motivo "sin_presupuesto".
3. En otro caso → estado "aprobada", motivo null, y descuenta el importe del presupuesto del departamento.
   Aprobador según el IMPORTE ACUMULADO: el de esta solicitud más el de las solicitudes ya procesadas que
   sigan aprobadas, del mismo proveedor y departamento, con fecha dentro de los 7 días anteriores
   (incluida la misma fecha). Acumulado ≤ 500 € → "auto"; ≤ 5000 € → "jefe_departamento"; > 5000 € → "direccion".
   (La regla 2 usa solo el importe de esta solicitud.)
4. revision_it = true si la categoría es "software" y el estado es "aprobada" o "escalada".

Cancelaciones: anulan la solicitud "referencia". Esa solicitud queda con estado "cancelada", aprobador null,
motivo "cancelada_por_solicitante", revision_it false. Si estaba aprobada, su importe vuelve al presupuesto
del departamento. Las cancelaciones no aparecen en "decisiones".

Redondeo a 2 decimales."""


def parsear_importe(texto: str, tipos: dict[str, float]) -> float:
    """Importe con IVA en EUR a partir del texto visible."""
    t = texto.replace("(IVA incluido)", "").replace("+ IVA", "").strip()
    moneda = "EUR"
    for mon in ("USD", "GBP", "EUR", "€"):
        if mon in t:
            moneda = "EUR" if mon == "€" else mon
            t = t.replace(mon, "").strip()
    if moneda == "EUR" and "," in t:
        t = t.replace(".", "").replace(",", ".")
    else:
        t = t.replace(",", "")
    eur = round(float(t) * tipos[moneda], 2)
    return round(eur * (1 + IVA), 2) if "+ IVA" in texto else eur


def resolver_negocio_extremo(entradas: dict, *, ordenar=True, fraccionar=True, cancelar=True) -> dict:
    """Motor de referencia sobre las entradas VISIBLES. Los flags sirven para comprobar trampas."""
    tipos = entradas["tipos_cambio"]
    restante = dict(entradas["presupuesto_inicial"])
    homologados = set(entradas["proveedores_homologados"])
    eventos = list(entradas["entradas"])
    if ordenar:
        eventos = sorted(eventos, key=lambda e: (e["fecha"], e["id"]))
    info, decisiones = {}, {}
    for e in eventos:
        if e["tipo"] == "cancelacion":
            if not cancelar:
                continue
            ref = e["referencia"]
            if ref in decisiones:
                if decisiones[ref]["estado"] == "aprobada":
                    s = info[ref]
                    restante[s["departamento"]] = round(restante[s["departamento"]] + s["importe"], 2)
                decisiones[ref] = {"estado": "cancelada", "aprobador": None,
                                   "motivo": "cancelada_por_solicitante", "revision_it": False}
            continue
        importe = parsear_importe(e["importe"], tipos)
        info[e["id"]] = {"departamento": e["departamento"], "proveedor": e["proveedor"],
                         "fecha": e["fecha"], "importe": importe}
        d = {"estado": None, "aprobador": None, "motivo": None, "revision_it": False}
        if e["proveedor"] not in homologados:
            if e["categoria"] == "seguridad" and e["urgente"]:
                d.update(estado="escalada", aprobador="direccion", motivo="excepcion_seguridad")
            else:
                d.update(estado="rechazada", motivo="proveedor_no_homologado")
        elif importe > restante[e["departamento"]] + 1e-9:
            d.update(estado="escalada", aprobador="finanzas", motivo="sin_presupuesto")
        else:
            acumulado = importe
            if fraccionar:
                f0 = date.fromisoformat(e["fecha"])
                for sid, s in info.items():
                    if sid == e["id"] or decisiones.get(sid, {}).get("estado") != "aprobada":
                        continue
                    if (s["proveedor"], s["departamento"]) == (e["proveedor"], e["departamento"]) \
                            and 0 <= (f0 - date.fromisoformat(s["fecha"])).days <= 7:
                        acumulado += s["importe"]
            acumulado = round(acumulado, 2)
            aprobador = "auto" if acumulado <= 500 else "jefe_departamento" if acumulado <= 5000 else "direccion"
            d.update(estado="aprobada", aprobador=aprobador)
            restante[e["departamento"]] = round(restante[e["departamento"]] - importe, 2)
        d["revision_it"] = e["categoria"] == "software" and d["estado"] in ("aprobada", "escalada")
        decisiones[e["id"]] = d
    return {"decisiones": decisiones, "presupuesto_final": {k: round(v, 2) for k, v in restante.items()}}


def _texto_importe(base: float, moneda: str, sin_iva: bool, rng) -> str:
    if moneda == "EUR":
        if rng.random() < 0.4:
            num = f"{base:.2f}"
        else:
            num = f"{base:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        txt = f"{num} {'€' if rng.random() < 0.6 else 'EUR'}"
    else:
        txt = f"{base:,.2f} {moneda}"
    return txt + (" + IVA" if sin_iva else " (IVA incluido)")


def generar_negocio_extremo(semilla: int) -> tuple[dict, dict]:
    for intento in range(300):
        rng = np.random.default_rng(700_000 + semilla * 1000 + intento)
        presupuesto = {d: float(round(rng.uniform(2500, 7000), -2)) for d in DEPARTAMENTOS}
        sols = []
        for i in range(14):
            homologado = rng.random() < 0.8
            moneda = str(rng.choice(["EUR", "USD", "GBP"], p=[0.6, 0.25, 0.15]))
            base = float(round(np.exp(rng.uniform(np.log(60), np.log(4000))), 2))
            sols.append({
                "id": f"SC-{semilla:03d}-{i:02d}", "tipo": "solicitud",
                "fecha": f"2026-09-{int(rng.integers(1, 21)):02d}",
                "departamento": str(rng.choice(DEPARTAMENTOS)),
                "proveedor": str(rng.choice(HOMOLOGADOS if homologado else NO_HOMOLOGADOS)),
                "categoria": str(rng.choice(CATEGORIAS)),
                "urgente": bool(rng.random() < 0.3),
                "importe": _texto_importe(base, moneda, bool(rng.random() < 0.4), rng),
            })
        # fraccionamiento forzado: dos compras pequeñas del mismo proveedor y departamento en 3 días
        prov, dep = str(rng.choice(HOMOLOGADOS)), str(rng.choice(DEPARTAMENTOS))
        dia = int(rng.integers(2, 15))
        for k, (j, dd) in enumerate([(14, 0), (15, int(rng.integers(1, 4)))]):
            sols.append({"id": f"SC-{semilla:03d}-{j:02d}", "tipo": "solicitud",
                         "fecha": f"2026-09-{dia + dd:02d}", "departamento": dep, "proveedor": prov,
                         "categoria": str(rng.choice(["material_oficina", "software", "formacion"])),
                         "urgente": False,
                         "importe": _texto_importe(float(round(rng.uniform(260, 400), 2)), "EUR", False, rng)})
        # excepción de seguridad forzada
        sols[int(rng.integers(0, 14))].update(proveedor=str(rng.choice(NO_HOMOLOGADOS)), categoria="seguridad",
                                              urgente=True)
        base_entradas = {"politica": POLITICA_EXTREMO, "proveedores_homologados": HOMOLOGADOS,
                         "tipos_cambio": {k: v for k, v in TIPOS_CAMBIO.items() if k != "EUR"} | {"EUR": 1.0},
                         "presupuesto_inicial": presupuesto, "entradas": sols}
        # cancelaciones: una de una solicitud aprobada, otra de una cualquiera; fecha posterior
        previo = resolver_negocio_extremo(base_entradas)["decisiones"]
        aprobadas = [s for s in sols if previo[s["id"]]["estado"] == "aprobada" and s["fecha"] < "2026-09-25"]
        if not aprobadas:
            continue
        ref1 = aprobadas[int(rng.integers(0, len(aprobadas)))]
        ref2 = sols[int(rng.integers(0, len(sols)))]
        cancel = []
        for k, ref in enumerate([ref1, ref2]):
            d0 = int(ref["fecha"][-2:])
            cancel.append({"id": f"CX-{semilla:03d}-{k}", "tipo": "cancelacion",
                           "fecha": f"2026-09-{int(rng.integers(d0 + 1, 27)):02d}", "referencia": ref["id"]})
        eventos = sols + cancel
        rng.shuffle(eventos)
        entradas = base_entradas | {"entradas": eventos}
        objetivo = resolver_negocio_extremo(entradas)
        estados = [d["estado"] for d in objetivo["decisiones"].values()]
        # todas las ramas presentes y todas las trampas activas
        if not {"aprobada", "rechazada", "escalada", "cancelada"} <= set(estados):
            continue
        if not any(d["motivo"] == "sin_presupuesto" for d in objetivo["decisiones"].values()):
            continue
        if any(resolver_negocio_extremo(entradas, **{flag: False}) == objetivo
               for flag in ("ordenar", "fraccionar", "cancelar")):
            continue
        # divisas: tratar todo como EUR tiene que cambiar el resultado
        falso = json.loads(json.dumps(entradas))
        falso["tipos_cambio"] = {k: 1.0 for k in falso["tipos_cambio"]}
        if resolver_negocio_extremo(falso) == objetivo:
            continue
        break
    else:
        raise RuntimeError(f"negocio extremo {semilla}: no se logró una tarea válida")

    return ({"id": f"negocio-extremo-{semilla:03d}", "dominio": "negocio", "nivel": "extremo",
             "semilla": semilla,
             "enunciado": ("Aplica la política de compras a las entradas y devuelve el estado final: "
                           '{"decisiones": {id_solicitud: {"estado", "aprobador", "motivo", "revision_it"}}, '
                           '"presupuesto_final": {departamento: euros}}.'),
             "entradas": entradas,
             "verificador": {"id": "negocio_estado", "tipo": "determinista"}},
            objetivo)
