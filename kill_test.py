"""Prueba de muerte, versión simulada.

Pregunta: ¿optimizar el workflow COMPLETO (MDP) le gana a un router que decide paso a
paso (estilo UCCI: el tier más barato cuya probabilidad local de acierto supera un
umbral)? Si no le gana, la tesis central del framework cae.

La simulación NO prueba la tesis con datos reales. Lo que hace es mostrar de qué
depende: del ACOPLAMIENTO κ, cuánto empeora un paso barato de hoy la probabilidad de
éxito de los pasos siguientes (la tesis de gated-downshift). El benchmark real debe
medir κ; esta simulación dice a partir de qué κ merece la pena el MDP.

Modelo  [todos los números son SUPUESTOS y se pueden cambiar en Params]
  - S etapas en serie; en cada una se elige tier ∈ {barato, medio, caro}.
  - Cada etapa tiene un verificador determinista: sale ok o fallo.
  - P(ok | etapa, tier, deuda f, dificultad d) = d · q_tier · (1 − κ·f).
  - Deuda f: 1 − (1−φ_barato)^a (1−φ_medio)^b, con a, b = etapas ya superadas con
    tier barato / medio. Un paso barato "pasa", pero deja deuda que cobra después.
  - Fallo → reintento (hasta R); agotados → la tarea falla y va a la alternativa.
  - Objetivo de todas las políticas: max V·P(éxito) − E[coste]  (V = coste de la alternativa).
  - La dificultad d de cada tarea es desconocida para las políticas (planifican con E[d]).

Políticas comparadas
  mdp            value iteration exacta sobre (etapa, a, b, reintento)
  mdp_sin_acopl  MDP que planifica con κ̂ = 0 (sabe de reintentos, ignora la deuda)
  router         greedy por paso; su umbral τ se TUNEA a su mejor valor (baseline fuerte)
  cascada        estilo FrugalGPT: empieza en un tier y sube uno por cada reintento
                 (tier inicial tuneado). Es por paso, pero sí sabe escalar al fallar.
  barato         siempre barato (downshift ingenuo)
  agente_libre   siempre caro + overhead de decidir el siguiente paso con el LLM

Evaluación exacta por tarea (programación dinámica, vectorizada sobre d) + Monte Carlo
como prueba de paridad: ambos cálculos tienen que coincidir.

Uso:  python kill_test.py --salida resultados
"""
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

TIERS = ("barato", "medio", "caro")


@dataclass(frozen=True)
class Params:
    S: int = 4                                   # etapas
    R: int = 2                                   # reintentos por etapa
    coste_barato: float = 0.01                   # € por llamada   [SUPUESTO]
    ratio: float = 25.0                          # caro/barato; medio = media geométrica
    q: tuple[float, float, float] = (0.70, 0.85, 0.95)   # acierto local por tier [SUPUESTO]
    phi: tuple[float, float, float] = (0.50, 0.20, 0.0)  # deuda que deja cada tier [SUPUESTO]
    kappa: float = 0.5                           # acoplamiento: lo que hay que medir
    V: float = 2.0                               # € que vale resolver (coste de la alternativa)
    d_min: float = 0.80                          # dificultad por tarea ~ U(d_min, 1)
    overhead_agente: float = 0.30                # tokens extra del agente libre por decidir
    n_tareas: int = 2000
    semilla: int = 7

    @property
    def costes(self) -> np.ndarray:
        return self.coste_barato * np.array([1.0, np.sqrt(self.ratio), self.ratio])

    @property
    def d_media(self) -> float:
        return (self.d_min + 1.0) / 2


def estados(P: Params):
    """(s, a, b, r) con a+b ≤ s etapas ya superadas con barato/medio."""
    for s in range(P.S):
        for a in range(s + 1):
            for b in range(s + 1 - a):
                for r in range(P.R + 1):
                    yield (s, a, b, r)


def deuda(P: Params, a: int, b: int) -> float:
    return 1 - (1 - P.phi[0]) ** a * (1 - P.phi[1]) ** b


def p_ok(P: Params, t: int, a: int, b: int, d, kappa: float | None = None):
    k = P.kappa if kappa is None else kappa
    return d * P.q[t] * (1 - k * deuda(P, a, b))


def _siguiente(a: int, b: int, t: int) -> tuple[int, int]:
    return (a + (t == 0), b + (t == 1))


# --------------------------------------------------------------- políticas

def politica_mdp(P: Params, kappa_plan: float | None = None) -> dict:
    """Backward induction con d = E[d]. Devuelve estado → tier (determinista)."""
    d = P.d_media
    c = P.costes
    Vf: dict = {}
    pol: dict = {}
    for s in reversed(range(P.S)):
        for a in range(s + 1):
            for b in range(s + 1 - a):
                for r in reversed(range(P.R + 1)):
                    mejor, arg = -np.inf, 0
                    for t in range(3):
                        p = p_ok(P, t, a, b, d, kappa_plan)
                        a2, b2 = _siguiente(a, b, t)
                        v_ok = P.V if s == P.S - 1 else Vf[(s + 1, a2, b2, 0)]
                        v_fallo = Vf[(s, a, b, r + 1)] if r < P.R else 0.0
                        v = -c[t] + p * v_ok + (1 - p) * v_fallo
                        if v > mejor + 1e-12:
                            mejor, arg = v, t
                    Vf[(s, a, b, r)] = mejor
                    pol[(s, a, b, r)] = arg
    return pol


def politica_router(P: Params, tau: float) -> dict:
    """Greedy por paso: el tier más barato con p̂ local ≥ τ; si ninguno, el de mayor p̂.
    Ve la deuda actual (está en su entrada), pero no el efecto de su elección en el futuro."""
    pol = {}
    for (s, a, b, r) in estados(P):
        ps = [p_ok(P, t, a, b, P.d_media) for t in range(3)]
        ok = [t for t in range(3) if ps[t] >= tau]
        pol[(s, a, b, r)] = ok[0] if ok else int(np.argmax(ps))
    return pol


def politica_fija(P: Params, t: int) -> dict:
    return {e: t for e in estados(P)}


def politica_cascada(P: Params, inicio: int) -> dict:
    """FrugalGPT: intento 0 en `inicio`, y sube un tier por cada reintento."""
    return {(s, a, b, r): min(inicio + r, 2) for (s, a, b, r) in estados(P)}


# --------------------------------------------------------------- evaluación exacta

def evaluar(P: Params, pol: dict, d: np.ndarray, factor_coste: float = 1.0):
    """P(éxito) y E[coste] exactos por tarea (vectorizado sobre la dificultad d)."""
    c = P.costes * factor_coste
    Pe: dict = {}
    Ce: dict = {}
    for s in reversed(range(P.S)):
        for a in range(s + 1):
            for b in range(s + 1 - a):
                for r in reversed(range(P.R + 1)):
                    t = pol[(s, a, b, r)]
                    p = p_ok(P, t, a, b, d)
                    a2, b2 = _siguiente(a, b, t)
                    p_sig = 1.0 if s == P.S - 1 else Pe[(s + 1, a2, b2, 0)]
                    c_sig = 0.0 if s == P.S - 1 else Ce[(s + 1, a2, b2, 0)]
                    p_f = Pe[(s, a, b, r + 1)] if r < P.R else 0.0
                    c_f = Ce[(s, a, b, r + 1)] if r < P.R else 0.0
                    Pe[(s, a, b, r)] = p * p_sig + (1 - p) * p_f
                    Ce[(s, a, b, r)] = c[t] + p * c_sig + (1 - p) * c_f
    return Pe[(0, 0, 0, 0)], Ce[(0, 0, 0, 0)]


def metricas(P: Params, p_i: np.ndarray, c_i: np.ndarray, k: int = 5) -> dict:
    p, c = float(p_i.mean()), float(c_i.mean())
    return {
        "p_exito": p,
        "coste_medio": c,
        # coste real de cada tarea: lo gastado + la alternativa cuando el framework falla
        "coste_total_por_tarea": c + (1 - p) * P.V,
        "eur_por_resuelta": c / p if p > 0 else np.inf,
        "valor_neto": P.V * p - c,
        f"pass^{k}": float((p_i ** k).mean()),
    }


def tareas(P: Params) -> np.ndarray:
    return np.random.default_rng(P.semilla).uniform(P.d_min, 1.0, P.n_tareas)


TAUS = np.round(np.arange(0.30, 0.96, 0.025), 3)


def comparar(P: Params) -> dict[str, dict]:
    d = tareas(P)
    out = {}
    for nombre, pol, f in (
        ("mdp", politica_mdp(P), 1.0),
        ("mdp_sin_acopl", politica_mdp(P, kappa_plan=0.0), 1.0),
        ("barato", politica_fija(P, 0), 1.0),
        ("agente_libre", politica_fija(P, 2), 1 + P.overhead_agente),
    ):
        out[nombre] = metricas(P, *evaluar(P, pol, d, f))
    # router: el mejor umbral posible (tuneado sobre las mismas tareas → baseline optimista)
    mejor = max(
        ((tau, metricas(P, *evaluar(P, politica_router(P, tau), d))) for tau in TAUS),
        key=lambda x: x[1]["valor_neto"],
    )
    out["router"] = mejor[1] | {"tau": float(mejor[0])}
    mejor_c = max(
        ((ini, metricas(P, *evaluar(P, politica_cascada(P, ini), d))) for ini in (0, 1)),
        key=lambda x: x[1]["valor_neto"],
    )
    out["cascada"] = mejor_c[1] | {"inicio": TIERS[mejor_c[0]]}
    return out


# --------------------------------------------------------------- Monte Carlo (paridad)

def simular(P: Params, pol: dict, d: np.ndarray, rep: int, rng, factor_coste=1.0):
    c = P.costes * factor_coste
    exitos = np.zeros((len(d), rep), dtype=bool)
    costes = np.zeros((len(d), rep))
    for i, di in enumerate(d):
        for j in range(rep):
            s = a = b = r = 0
            gasto = 0.0
            while True:
                t = pol[(s, a, b, r)]
                gasto += c[t]
                if rng.random() < p_ok(P, t, a, b, di):
                    a, b = _siguiente(a, b, t)
                    s, r = s + 1, 0
                    if s == P.S:
                        exitos[i, j] = True
                        break
                elif r < P.R:
                    r += 1
                else:
                    break
            costes[i, j] = gasto
    return exitos, costes


def prueba_paridad(P: Params, n: int = 300, rep: int = 100) -> pd.DataFrame:
    """Exacto vs Monte Carlo sobre las mismas tareas: deben coincidir (|z| < 4)."""
    rng = np.random.default_rng(123)
    d = tareas(P)[:n]
    filas = []
    for nombre, pol in (("mdp", politica_mdp(P)), ("router_tau_0.7", politica_router(P, 0.7)),
                        ("cascada_barato", politica_cascada(P, 0)), ("barato", politica_fija(P, 0))):
        p_ex, c_ex = evaluar(P, pol, d)
        ex, co = simular(P, pol, d, rep, rng)
        for metrica, exacto, runs in (("p_exito", p_ex.mean(), ex.astype(float)),
                                      ("coste", c_ex.mean(), co)):
            mc = runs.mean()
            # única fuente de ruido: el Monte Carlo dentro de cada tarea
            se = np.sqrt(runs.var(axis=1, ddof=1).sum() / rep) / n
            filas.append({"politica": nombre, "metrica": metrica, "exacto": float(exacto),
                          "monte_carlo": float(mc), "z": float((mc - exacto) / se) if se > 0 else 0.0})
    return pd.DataFrame(filas)


# --------------------------------------------------------------- barridos

def barrido_kappa(P: Params) -> pd.DataFrame:
    filas = []
    for k in np.round(np.arange(0, 1.01, 0.1), 2):
        for nombre, m in comparar(replace(P, kappa=float(k))).items():
            filas.append({"kappa": k, "politica": nombre} | m)
    return pd.DataFrame(filas)


def fase(P: Params, kappas, ratios) -> pd.DataFrame:
    filas = []
    for k, rho in itertools.product(kappas, ratios):
        res = comparar(replace(P, kappa=float(k), ratio=float(rho)))
        m, s0 = res["mdp"], res["mdp_sin_acopl"]
        nombre_g = max(("router", "cascada"), key=lambda n: res[n]["valor_neto"])
        g = res[nombre_g]   # el MEJOR baseline por paso en esa celda
        filas.append({
            "kappa": k, "ratio": rho, "mejor_por_paso": nombre_g,
            "ahorro_eur_por_tarea": g["coste_total_por_tarea"] - m["coste_total_por_tarea"],
            "ahorro_pct_coste_total": 100 * (1 - m["coste_total_por_tarea"] / g["coste_total_por_tarea"]),
            # descomposición: cuánto viene de saber escalar y cuánto de modelar la deuda
            "aporte_reintentos_eur": g["coste_total_por_tarea"] - s0["coste_total_por_tarea"],
            "aporte_acoplamiento_eur": s0["coste_total_por_tarea"] - m["coste_total_por_tarea"],
            "p_mdp": m["p_exito"], "p_por_paso": g["p_exito"],
        })
    return pd.DataFrame(filas)


def comparar_con_pmin(P: Params, p_min: float = 0.9) -> dict[str, dict]:
    """Versión 'contrato': de cada familia, la configuración MÁS BARATA con P(éxito) ≥ p_min.
    Para el MDP se barre V (= el vértice del frente de Pareto que cumple p_min)."""
    d = tareas(P)
    familias = {
        "mdp": [politica_mdp(replace(P, V=float(v))) for v in np.geomspace(0.2, 200, 60)],
        "mdp_sin_acopl": [politica_mdp(replace(P, V=float(v)), 0.0) for v in np.geomspace(0.2, 200, 60)],
        "router": [politica_router(P, t) for t in TAUS],
        "cascada": [politica_cascada(P, i) for i in (0, 1, 2)],
        "agente_libre": [politica_fija(P, 2)],
    }
    out = {}
    for nombre, pols in familias.items():
        f = 1 + P.overhead_agente if nombre == "agente_libre" else 1.0
        factibles = []
        for pol in pols:
            m = metricas(P, *evaluar(P, pol, d, f))
            if m["p_exito"] >= p_min:
                factibles.append(m)
        out[nombre] = min(factibles, key=lambda m: m["coste_medio"]) if factibles else None
    return out


def barrido_pmin(P: Params, p_min: float = 0.9) -> pd.DataFrame:
    filas = []
    for k in np.round(np.arange(0, 1.01, 0.1), 2):
        for nombre, m in comparar_con_pmin(replace(P, kappa=float(k)), p_min).items():
            filas.append({"kappa": k, "politica": nombre, "factible": m is not None} | (m or {}))
    return pd.DataFrame(filas)


def prueba_optimalidad(P: Params) -> float:
    """Con d fijo = E[d] (el modelo con el que planifica), el MDP no puede perder contra
    ninguna otra política. Devuelve el margen mínimo (≥ 0 si es correcto)."""
    d = np.array([P.d_media])
    v_mdp = P.V * evaluar(P, politica_mdp(P), d)[0][0] - evaluar(P, politica_mdp(P), d)[1][0]
    rivales = [politica_router(P, t) for t in TAUS] + [politica_cascada(P, i) for i in (0, 1)] \
        + [politica_fija(P, t) for t in range(3)] + [politica_mdp(P, 0.0)]
    margen = min(v_mdp - (P.V * evaluar(P, pol, d)[0][0] - evaluar(P, pol, d)[1][0]) for pol in rivales)
    return float(margen)


def sensibilidad(P: Params) -> pd.DataFrame:
    """¿Cuánto de la ventaja sobrevive si cambian los supuestos o si κ se mide mal?"""
    filas = []

    def fila(etiqueta, Q, pol_mdp=None):
        res = comparar(Q)
        g = min(res["router"]["coste_total_por_tarea"], res["cascada"]["coste_total_por_tarea"])
        m = res["mdp"]["coste_total_por_tarea"]
        if pol_mdp is not None:   # MDP planificado con un κ̂ equivocado, evaluado con el κ real
            p_i, c_i = evaluar(Q, pol_mdp, tareas(Q))
            m = metricas(Q, p_i, c_i)["coste_total_por_tarea"]
        filas.append({"escenario": etiqueta, "kappa": Q.kappa, "ratio": Q.ratio,
                      "coste_mdp": m, "coste_mejor_por_paso": g, "ahorro_pct": 100 * (1 - m / g)})

    for k in (0.2, 0.5, 0.8):
        Q = replace(P, kappa=k)
        fila("base", Q)
        fila("κ̂ = κ/2 (subestimado)", Q, politica_mdp(Q, k / 2))
        fila("κ̂ = κ+0.2 (sobreestimado)", Q, politica_mdp(Q, min(k + 0.2, 1.0)))
    for S in (2, 8):
        fila(f"S = {S} etapas", replace(P, S=S))
    for V in (0.5, 5.0):
        fila(f"V = {V} €", replace(P, V=V))
    fila("deuda menor φ=(0.25, 0.1, 0)", replace(P, phi=(0.25, 0.1, 0.0)))
    fila("tiers más parecidos q=(0.85, 0.9, 0.95)", replace(P, q=(0.85, 0.90, 0.95)))
    return pd.DataFrame(filas)


def main(salida: Path) -> dict:
    salida.mkdir(parents=True, exist_ok=True)
    P = Params()
    paridad = prueba_paridad(P)
    paridad.to_csv(salida / "paridad.csv", index=False)
    assert (paridad["z"].abs() < 4).all(), f"exacto y Monte Carlo no coinciden:\n{paridad}"
    margen = min(prueba_optimalidad(replace(P, kappa=k, ratio=r))
                 for k in (0.0, 0.5, 1.0) for r in (2, 25, 100))
    assert margen >= -1e-9, f"el MDP pierde contra otra política en su propio modelo: {margen}"

    bk = barrido_kappa(P)
    bk.to_csv(salida / "barrido_kappa.csv", index=False)

    fs = fase(P, kappas=np.round(np.arange(0, 1.01, 0.1), 2), ratios=[2, 5, 10, 25, 50, 100])
    fs.to_csv(salida / "fase.csv", index=False)

    sens = sensibilidad(P)
    sens.to_csv(salida / "sensibilidad.csv", index=False)

    bp = barrido_pmin(P, p_min=0.9)
    bp.to_csv(salida / "barrido_pmin.csv", index=False)

    resumen = {"params": {k: v for k, v in P.__dict__.items()},
               "paridad_max_abs_z": float(paridad["z"].abs().max()),
               "optimalidad_margen_min": margen}
    (salida / "resumen.json").write_text(json.dumps(resumen, indent=2, default=list))
    return resumen


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--salida", type=Path, default=Path("resultados"))
    print(main(ap.parse_args().salida))
