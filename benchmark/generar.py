"""Benchmark sintético con verdad sembrada (planted ground truth).

Cada tarea se genera desde una semilla: misma semilla → misma tarea, byte a byte.
La respuesta correcta se conoce por construcción y se guarda APARTE (objetivos.jsonl),
con su hash en la tarea para poder comprobar que nadie la tocó.

Dominios:
  datos   — dataset con un par de variables relacionadas y filas anómalas sembradas.
  negocio — lote de solicitudes de compra + política; el objetivo es el estado final
            (decisiones y presupuestos), como en τ-bench.

Uso:  python -m benchmark.generar --por-nivel 10 --salida benchmark/out
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

NIVELES = ("facil", "media", "dificil")
UMBRAL_Z_ROBUSTO = 4.5
IVA = 0.21


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# =============================================================== DATOS

COLUMNAS = [
    "ventas", "visitas", "temperatura", "devoluciones", "tickets_soporte", "tiempo_entrega",
    "precio_medio", "descuento", "stock", "resenas", "clics", "conversiones", "coste_envio",
    "humedad", "incidencias", "horas_extra", "satisfaccion", "retrasos",
]


@dataclass(frozen=True)
class ParamsDatos:
    n: int
    m: int
    r: float            # correlación del par sembrado
    distractores: int   # pares extra con correlación moderada
    r_distractor: float
    k_anom: int
    z_anom: float       # desviaciones robustas de las anomalías


PARAMS_DATOS = {
    "facil": ParamsDatos(300, 6, 0.90, 0, 0.0, 3, 10.0),
    "media": ParamsDatos(800, 10, 0.75, 2, 0.40, 5, 7.0),
    "dificil": ParamsDatos(2000, 16, 0.60, 4, 0.45, 8, 6.0),
}


def _z_robusto(col: np.ndarray) -> np.ndarray:
    med = np.median(col)
    mad = np.median(np.abs(col - med)) * 1.4826
    return np.abs(col - med) / mad


def _verdad_datos(df: pd.DataFrame) -> tuple[list[str], list[str], float]:
    """Aplica el criterio del enunciado. Devuelve (par, anomalías, margen del par)."""
    num = df.drop(columns="id")
    corr = num.corr().abs()
    pares = sorted(
        ((corr.loc[a, b], a, b) for a, b in combinations(num.columns, 2)), reverse=True
    )
    margen = pares[0][0] - pares[1][0]
    par = sorted([pares[0][1], pares[0][2]])
    z = np.column_stack([_z_robusto(num[c].to_numpy()) for c in num.columns])
    anom = sorted(df.loc[(z > UMBRAL_Z_ROBUSTO).any(axis=1), "id"].tolist())
    return par, anom, margen


def generar_datos(semilla: int, nivel: str, dir_datos: Path) -> tuple[dict, dict]:
    p = PARAMS_DATOS[nivel]
    for intento in range(100):  # se regenera hasta que la verdad sea identificable
        rng = np.random.default_rng(semilla * 1000 + intento)
        cols = list(rng.choice(COLUMNAS, size=p.m, replace=False))
        X = rng.standard_normal((p.n, p.m))
        orden = rng.permutation(p.m)
        a, b = orden[0], orden[1]
        X[:, b] = p.r * X[:, a] + np.sqrt(1 - p.r**2) * rng.standard_normal(p.n)
        libres = list(orden[2:])
        for i in range(p.distractores):
            c, d = libres[2 * i], libres[2 * i + 1]
            X[:, d] = p.r_distractor * X[:, c] + np.sqrt(1 - p.r_distractor**2) * rng.standard_normal(p.n)
        # anomalías en columnas que no son del par sembrado
        filas = rng.choice(p.n, size=p.k_anom, replace=False)
        for f in filas:
            col = rng.choice(libres)
            X[f, col] = rng.choice([-1, 1]) * p.z_anom
        # escala plausible (afín: no cambia correlaciones ni z robustos)
        mu = rng.uniform(10, 1000, p.m)
        sd = mu * rng.uniform(0.05, 0.3, p.m)
        df = pd.DataFrame(np.round(X * sd + mu, 3), columns=cols)
        df.insert(0, "id", [f"r{i:05d}" for i in range(p.n)])

        par_esperado = sorted([cols[a], cols[b]])
        anom_esperadas = sorted(df.loc[filas, "id"].tolist())
        par, anom, margen = _verdad_datos(df)
        if par == par_esperado and anom == anom_esperadas and margen >= 0.10:
            break
    else:
        raise RuntimeError(f"datos {nivel}/{semilla}: no se logró una verdad identificable")

    tarea_id = f"datos-{nivel}-{semilla:03d}"
    ruta = dir_datos / f"{tarea_id}.csv"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta, index=False)
    objetivo = {"par": par, "anomalias": anom}
    tarea = {
        "id": tarea_id,
        "dominio": "datos",
        "nivel": nivel,
        "semilla": semilla,
        "enunciado": (
            f"En el CSV adjunto ({p.n} filas, {p.m} variables) encuentra: (1) la relación "
            "principal: el par de variables con mayor correlación de Pearson en valor absoluto; "
            "(2) las filas anómalas: aquellas con algún valor a más de "
            f"{UMBRAL_Z_ROBUSTO} desviaciones robustas (MAD × 1.4826) de la mediana de su columna. "
            'Responde con el estado final {"par": [col_a, col_b], "anomalias": [ids]} (listas ordenadas).'
        ),
        "entradas": {"csv": ruta.name},
        "verificador": {"id": "datos_exacto", "tipo": "determinista"},
        "hash_objetivo": _hash(objetivo),
    }
    return tarea, objetivo


# =============================================================== NEGOCIO

PROVEEDORES_HOMOLOGADOS = ["Acme Suministros", "Iberdata", "Norte Oficina", "TecnoLevante", "SegurPro"]
PROVEEDORES_NO_HOMOLOGADOS = ["Global Bargains", "QuickParts Ltd", "Ofertas Mediterráneo"]
DEPARTAMENTOS = ["marketing", "operaciones", "it", "rrhh"]
CATEGORIAS = ["material_oficina", "software", "hardware", "seguridad", "formacion"]

POLITICA = """POLÍTICA DE COMPRAS (se aplica en orden a cada solicitud, por fecha de entrada)
1. Proveedor que no está en "proveedores_homologados" → estado "rechazada", motivo "proveedor_no_homologado".
   Excepción: categoría "seguridad" y urgente → estado "escalada", aprobador "direccion",
   motivo "excepcion_seguridad".
2. Importe con IVA mayor que el presupuesto restante del departamento → estado "escalada",
   aprobador "finanzas", motivo "sin_presupuesto".
3. En otro caso → estado "aprobada", motivo null. Aprobador según importe con IVA:
   ≤ 500 € → "auto"; ≤ 5000 € → "jefe_departamento"; > 5000 € → "direccion".
4. revision_it = true si la categoría es "software" y la solicitud no está rechazada.
5. Solo las aprobadas descuentan su importe con IVA del presupuesto del departamento.
Los umbrales usan el importe CON IVA (21 %). Redondeo a 2 decimales."""


@dataclass(frozen=True)
class ParamsNegocio:
    lote: int
    formato: str          # "plano" | "es" | "mixto"
    prob_sin_iva: float   # solicitudes que dan la base imponible
    presupuesto: tuple[float, float]


PARAMS_NEGOCIO = {
    "facil": ParamsNegocio(3, "plano", 0.0, (20000, 40000)),
    "media": ParamsNegocio(8, "es", 0.25, (6000, 15000)),
    "dificil": ParamsNegocio(15, "mixto", 0.4, (3000, 8000)),
}


def _fmt_importe(x: float, formato: str, rng) -> str:
    if formato == "plano" or (formato == "mixto" and rng.random() < 0.3):
        return f"{x:.2f}"
    es = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{es} €" if (formato == "es" or rng.random() < 0.5) else f"{es} EUR"


def resolver_negocio(solicitudes: list[dict], presupuesto: dict[str, float]) -> dict:
    """Motor de reglas de referencia: define la verdad del dominio negocio."""
    restante = dict(presupuesto)
    decisiones = {}
    for s in sorted(solicitudes, key=lambda s: s["fecha"]):
        importe = round(s["importe_con_iva"], 2)
        d = {"estado": None, "aprobador": None, "motivo": None, "revision_it": False}
        if s["proveedor"] not in PROVEEDORES_HOMOLOGADOS:
            if s["categoria"] == "seguridad" and s["urgente"]:
                d.update(estado="escalada", aprobador="direccion", motivo="excepcion_seguridad")
            else:
                d.update(estado="rechazada", motivo="proveedor_no_homologado")
        elif importe > restante[s["departamento"]] + 1e-9:
            d.update(estado="escalada", aprobador="finanzas", motivo="sin_presupuesto")
        else:
            aprobador = "auto" if importe <= 500 else "jefe_departamento" if importe <= 5000 else "direccion"
            d.update(estado="aprobada", aprobador=aprobador)
            restante[s["departamento"]] = round(restante[s["departamento"]] - importe, 2)
        d["revision_it"] = s["categoria"] == "software" and d["estado"] != "rechazada"
        decisiones[s["id"]] = d
    return {"decisiones": decisiones, "presupuesto_final": {k: round(v, 2) for k, v in restante.items()}}


def generar_negocio(semilla: int, nivel: str) -> tuple[dict, dict]:
    p = PARAMS_NEGOCIO[nivel]
    rng = np.random.default_rng(10_000 + semilla)
    presupuesto = {d: float(round(rng.uniform(*p.presupuesto), -2)) for d in DEPARTAMENTOS}
    # casos forzados para que la excepción de seguridad (y su casi-excepción) aparezcan
    forzados: dict[int, str] = {}
    if nivel != "facil":
        idx = rng.choice(p.lote, size=2, replace=False)
        forzados[int(idx[0])] = "excepcion"
        if nivel == "dificil":
            forzados[int(idx[1])] = "casi_excepcion"   # seguridad + no homologado, pero no urgente
    solicitudes, visibles = [], []
    for i in range(p.lote):
        homologado = rng.random() < 0.8
        proveedor = str(rng.choice(PROVEEDORES_HOMOLOGADOS if homologado else PROVEEDORES_NO_HOMOLOGADOS))
        categoria = str(rng.choice(CATEGORIAS))
        urgente = bool(rng.random() < 0.3)
        if i in forzados:
            proveedor = str(rng.choice(PROVEEDORES_NO_HOMOLOGADOS))
            categoria = "seguridad"
            urgente = forzados[i] == "excepcion"
        # importes cerca de los umbrales con más frecuencia en niveles altos
        if nivel != "facil" and rng.random() < 0.35:
            importe = float(rng.choice([500.0, 500.01, 4999.99, 5000.0, 5000.01]))
        else:
            importe = float(round(np.exp(rng.uniform(np.log(80), np.log(9000))), 2))
        sin_iva = rng.random() < p.prob_sin_iva
        base = round(importe / (1 + IVA), 2) if sin_iva else importe
        con_iva = round(base * (1 + IVA), 2) if sin_iva else importe
        s = {
            "id": f"SC-{semilla:03d}-{i:02d}",
            "fecha": f"2026-09-{1 + i:02d}",
            "departamento": str(rng.choice(DEPARTAMENTOS)),
            "proveedor": proveedor,
            "categoria": categoria,
            "urgente": urgente,
            "importe_con_iva": con_iva,
        }
        solicitudes.append(s)
        texto_importe = _fmt_importe(base, p.formato, rng) + (" + IVA" if sin_iva else " (IVA incluido)")
        visibles.append({k: v for k, v in s.items() if k != "importe_con_iva"} | {"importe": texto_importe})

    objetivo = resolver_negocio(solicitudes, presupuesto)
    tarea_id = f"negocio-{nivel}-{semilla:03d}"
    tarea = {
        "id": tarea_id,
        "dominio": "negocio",
        "nivel": nivel,
        "semilla": semilla,
        "enunciado": (
            "Aplica la política de compras al lote de solicitudes y devuelve el estado final: "
            '{"decisiones": {id: {"estado", "aprobador", "motivo", "revision_it"}}, '
            '"presupuesto_final": {departamento: euros}}.'
        ),
        "entradas": {"politica": POLITICA, "proveedores_homologados": PROVEEDORES_HOMOLOGADOS,
                     "presupuesto_inicial": presupuesto, "solicitudes": visibles},
        "verificador": {"id": "negocio_estado", "tipo": "determinista"},
        "hash_objetivo": _hash(objetivo),
    }
    return tarea, objetivo


# =============================================================== main

def generar(por_nivel: int, salida: Path, n_extremo: int = 30) -> tuple[list[dict], list[dict]]:
    from benchmark.extremo import generar_datos_extremo, generar_negocio_extremo
    salida.mkdir(parents=True, exist_ok=True)
    tareas, objetivos = [], []
    for nivel in NIVELES:
        for s in range(por_nivel):
            for t, o in (generar_datos(s, nivel, salida / "datos"), generar_negocio(s, nivel)):
                tareas.append(t)
                objetivos.append({"id": t["id"], "objetivo": o})
    for s in range(n_extremo):
        for t, o in (generar_datos_extremo(s, salida / "datos"), generar_negocio_extremo(s)):
            t["hash_objetivo"] = _hash(o)
            tareas.append(t)
            objetivos.append({"id": t["id"], "objetivo": o})
    with open(salida / "tareas.jsonl", "w", encoding="utf-8") as f:
        for t in tareas:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    with open(salida / "objetivos.jsonl", "w", encoding="utf-8") as f:
        for o in objetivos:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    return tareas, objetivos


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--por-nivel", type=int, default=10)
    ap.add_argument("--salida", type=Path, default=Path("benchmark/out"))
    ap.add_argument("--extremo", type=int, default=30)
    args = ap.parse_args()
    t, _ = generar(args.por_nivel, args.salida, args.extremo)
    print(f"{len(t)} tareas → {args.salida}/tareas.jsonl (objetivos aparte en objetivos.jsonl)")
