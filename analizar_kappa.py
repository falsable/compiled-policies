"""Análisis del experimento de κ: δ̂ con IC por bootstrap pareado, McNemar exacto, costes reales.

Uso: python analizar_kappa.py exp/piloto [--V 2.0]
"""
from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd


def cargar(d: Path) -> pd.DataFrame:
    return pd.DataFrame([json.loads(l) for l in open(d / "ejecuciones.jsonl", encoding="utf-8")])


def mcnemar_exacto(b: int, c: int) -> float:
    """p bilateral exacto sobre los pares discordantes."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)


def pares(df: pd.DataFrame) -> pd.DataFrame:
    """Una fila por (tarea, rep) con el resultado de cada brazo, solo si ambos planes pasaron el gate."""
    w = df.pivot_table(index=["tarea", "rep", "dominio", "nivel"], columns="brazo", values="ok", aggfunc="first")
    g = df.pivot_table(index=["tarea", "rep", "dominio", "nivel"], columns="brazo", values="gate_ok", aggfunc="first")
    w = w[(g["plan_barato"] == True) & (g["plan_caro"] == True)].astype(float)  # noqa: E712
    return w.reset_index()


def delta(w: pd.DataFrame) -> float:
    pc = w["plan_caro"].mean()
    return float("nan") if pc == 0 else 1 - w["plan_barato"].mean() / pc


def bootstrap_delta(w: pd.DataFrame, B: int = 5000, semilla: int = 0) -> tuple[float, float]:
    """Remuestrea TAREAS (con todas sus réplicas): respeta la dependencia dentro de la tarea."""
    rng = np.random.default_rng(semilla)
    grupos = [g for _, g in w.groupby("tarea")]
    vals = []
    for _ in range(B):
        muestra = pd.concat([grupos[i] for i in rng.integers(0, len(grupos), len(grupos))])
        vals.append(delta(muestra))
    vals = np.array(vals)
    vals = vals[~np.isnan(vals)]
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def resumen(df: pd.DataFrame, V: float) -> dict:
    out = {}
    for ambito, sub in [("global", df)] + [(d, g) for d, g in df.groupby("dominio")]:
        w = pares(sub)
        b = int(((w.plan_barato == 1) & (w.plan_caro == 0)).sum())
        c = int(((w.plan_barato == 0) & (w.plan_caro == 1)).sum())
        lo, hi = bootstrap_delta(w) if len(w) >= 5 else (float("nan"), float("nan"))
        brazos = {}
        for brazo, g in sub.groupby("brazo"):
            coste = g["coste_plan"] + g["coste_ejec"]
            brazos[brazo] = {
                "n": int(len(g)),
                "gate_ok": float(g["gate_ok"].mean()),
                "p_ok": float(g["ok"].mean()),
                "coste_medio_usd": float(coste.mean()),
                "coste_plan_usd": float(g["coste_plan"].mean()),
                "coste_ejec_usd": float(g["coste_ejec"].mean()),
                # métrica principal del contrato: lo gastado + V cuando falla
                "coste_total_por_tarea_usd": float((coste + (~g["ok"].astype(bool)) * V).mean()),
            }
        out[ambito] = {
            "pares_validos": int(len(w)),
            "p_ok_plan_barato": float(w["plan_barato"].mean()) if len(w) else None,
            "p_ok_plan_caro": float(w["plan_caro"].mean()) if len(w) else None,
            "p_ok_sin_plan": float(w["sin_plan"].mean()) if len(w) else None,
            "delta": delta(w) if len(w) else None,
            "delta_ic95": [lo, hi],
            "discordantes_barato_gana": b,
            "discordantes_caro_gana": c,
            "mcnemar_p": mcnemar_exacto(b, c),
            "brazos": brazos,
        }
    # ratio de coste efectivo por llamada de planificación (lo que usa el mapa de fase)
    pb = df[df.brazo == "plan_barato"]["coste_plan"] / df[df.brazo == "plan_barato"]["llamadas_plan"]
    pc = df[df.brazo == "plan_caro"]["coste_plan"] / df[df.brazo == "plan_caro"]["llamadas_plan"]
    out["ratio_coste_por_llamada_caro_barato"] = float(pc.mean() / pb.mean())
    out["gasto_total_usd"] = float(df.drop_duplicates(["tarea", "rep", "brazo"])[["coste_plan", "coste_ejec"]].sum().sum()
                                   - df[df.brazo == "sin_plan"]["coste_plan"].sum())
    return out


def router_por_dominio(df: pd.DataFrame, V: float) -> dict:
    """¿Cuánto vale decidir 'planificar o no' según el tipo de tarea, frente a una política fija?
    Oráculo por dominio (cota superior de lo que ganaría un clasificador perfecto por dominio)."""
    df = df.assign(coste=df["coste_plan"] + df["coste_ejec"],
                   total=lambda d: d["coste"] + (~d["ok"].astype(bool)) * V)
    tabla = df.groupby(["dominio", "brazo"])["total"].mean().unstack("brazo")
    fija = tabla.mean(axis=0)              # misma política en ambos dominios (dominios equilibrados)
    mejor_fija = fija.idxmin()
    por_dom = tabla.min(axis=1)
    return {
        "coste_total_por_dominio_y_brazo": tabla.round(4).to_dict(),
        "mejor_politica_fija": mejor_fija,
        "coste_mejor_fija": float(fija.min()),
        "brazo_elegido_por_dominio": tabla.idxmin(axis=1).to_dict(),
        "coste_router_por_dominio": float(por_dom.mean()),
        "ahorro_router_vs_fija_pct": float(100 * (1 - por_dom.mean() / fija.min())),
    }


def por_nivel(df: pd.DataFrame) -> pd.DataFrame:
    return (df.groupby(["dominio", "nivel", "brazo"])["ok"].mean().unstack("brazo")
            .reindex(columns=["sin_plan", "plan_barato", "plan_caro"]).round(2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--V", type=float, default=2.0)
    a = ap.parse_args()
    df = cargar(a.dir)
    r = resumen(df, a.V)
    r["router_por_dominio"] = router_por_dominio(df, a.V)
    (a.dir / "resumen.json").write_text(json.dumps(r, indent=2, ensure_ascii=False))
    por_nivel(df).to_csv(a.dir / "por_nivel.csv")
    print(json.dumps({k: v for k, v in r.items() if k != "global"} | {"global": {k: v for k, v in r["global"].items() if k != "brazos"}}, indent=1, ensure_ascii=False)[:3000])
    print(por_nivel(df))
