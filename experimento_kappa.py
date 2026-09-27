"""Experimento real para medir el acoplamiento δ = κ·φ con LLMs.

Réplica mínima de gated-downshift en 2 etapas:

    PLAN (tier sorteado: barato=Haiku | caro=Opus)
      → GATE determinista (el plan tiene que estar completo)
      → EJECUCIÓN (tier FIJO barato: escribe un programa que produce el estado final)
      → VERIFICADOR del benchmark (estado final == objetivo congelado)

Diseño pareado: para cada tarea y réplica se generan los dos planes y se ejecutan tres brazos
con el MISMO ejecutor: sin_plan, plan_barato, plan_caro.

    δ̂ = 1 − P(ok ejecución | plan barato) / P(ok ejecución | plan caro)
    (condicionado a que el plan pasó su gate, como en el modelo)

Cada llamada registra su coste real (total_cost_usd de la CLI, precio de lista).

Uso:
    python experimento_kappa.py --tareas 12 --reps 1 --salida exp/piloto
    python experimento_kappa.py --tareas 60 --reps 2 --salida exp/completo --presupuesto-usd 25
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from benchmark.verificar import verificar

RAIZ = Path(__file__).parent
BENCH = RAIZ / "benchmark" / "out"
MODELOS = {"barato": "haiku", "caro": "opus"}
EJECUTOR = "barato"
ESFUERZO = "medium"   # igual para planificador y ejecutor

SISTEMA_PLAN = (
    "Eres el PLANIFICADOR de un workflow. No resuelves la tarea: escribes el plan que seguirá "
    "otro modelo, más barato, que escribirá un programa Python para resolverla. El plan debe ser "
    "completo y verificable. Responde SOLO con un objeto JSON válido, sin texto alrededor."
)
FORMATO_PLAN = """Formato obligatorio del plan (JSON):
{
  "objetivo": "qué estado final hay que producir",
  "criterios": ["cada regla o criterio que el programa debe aplicar, con sus casos límite"],
  "pasos": [{"n": 1, "accion": "…", "detalle": "…"}],
  "verificacion": ["comprobaciones que el programa debe hacer antes de imprimir el resultado"],
  "riesgos": ["errores probables a evitar"]
}
Mínimo 3 pasos, al menos 1 criterio y 1 verificación."""

SISTEMA_EJEC = (
    "Eres el EJECUTOR de un workflow. Escribes un único programa Python 3 autocontenido "
    "(solo biblioteca estándar, pandas y numpy) que resuelve la tarea. Responde SOLO con el "
    "programa dentro de un bloque ```python```."
)

_lock = threading.Lock()
_gasto = {"usd": 0.0}


# ------------------------------------------------------------------ LLM

def llamar(modelo: str, sistema: str, prompt: str, timeout: int = 600) -> dict:
    # sin heredar el esfuerzo de esta sesión: esfuerzo fijo y explícito para todas las llamadas
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_EFFORT")}
    cmd = ["claude", "-p", "--model", modelo, "--effort", ESFUERZO, "--output-format", "json", "--tools", "",
           "--system-prompt", sistema, "--setting-sources", "", "--strict-mcp-config",
           "--no-session-persistence", "--max-budget-usd", "1.0"]
    with tempfile.TemporaryDirectory() as vacio:   # cwd vacío: ningún CLAUDE.md
        for intento in range(3):
            t0 = time.time()
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=vacio,
                               env=env, timeout=timeout)
            try:
                d = json.loads(p.stdout)
                if d.get("is_error"):
                    raise ValueError(d.get("result") or d.get("api_error_status"))
                coste = float(d.get("total_cost_usd", 0.0))
                with _lock:
                    _gasto["usd"] += coste
                u = d.get("usage", {})
                return {"texto": d.get("result", ""), "coste_usd": coste, "segundos": time.time() - t0,
                        "tokens_in": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                        + u.get("cache_creation_input_tokens", 0),
                        "tokens_out": u.get("output_tokens", 0)}
            except Exception as e:  # reintento técnico (red, rate limit), no de contenido
                err = f"{e} | stderr={p.stderr[-300:]}"
                time.sleep(5 * (intento + 1))
        raise RuntimeError(f"llamada fallida tras 3 intentos: {err}")


# ------------------------------------------------------------------ contexto de la tarea

def describir_entradas(tarea: dict) -> str:
    e = tarea["entradas"]
    if tarea["dominio"] == "datos":
        try:
            df = pd.read_csv(BENCH / "datos" / e["csv"])
            return (f"Archivo `datos.csv`: {len(df)} filas, columnas {list(df.columns)}.\n"
                    f"Primeras 5 filas:\n{df.head().to_csv(index=False)}")
        except Exception:   # CSV que no se lee con los valores por defecto: se muestra crudo
            lineas = (BENCH / "datos" / e["csv"]).read_text(encoding="utf-8").splitlines()
            return (f"Archivo `datos.csv`: {len(lineas) - 1} filas. Primeras 6 líneas tal cual:\n"
                    + "\n".join(lineas[:6]))
    return (f"Archivo `entradas.json` con las claves {list(e)}. Contenido:\n"
            + json.dumps(e, ensure_ascii=False, indent=1))


def preparar_directorio(tarea: dict, d: Path) -> None:
    e = dict(tarea["entradas"])
    if tarea["dominio"] == "datos":
        (d / "datos.csv").write_bytes((BENCH / "datos" / e["csv"]).read_bytes())
        e = {"csv": "datos.csv"}
    (d / "entradas.json").write_text(json.dumps(e, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ etapas

def _json_de(texto: str) -> dict:
    texto = texto.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", texto, re.S)
    if m:
        texto = m.group(1)
    i, j = texto.find("{"), texto.rfind("}")
    return json.loads(texto[i:j + 1])


def gate_plan(plan: dict) -> tuple[bool, str]:
    """Gate determinista: el plan está completo o no pasa."""
    for k in ("objetivo", "criterios", "pasos", "verificacion"):
        if k not in plan:
            return False, f"falta '{k}'"
    if not isinstance(plan["pasos"], list) or len(plan["pasos"]) < 3:
        return False, "menos de 3 pasos"
    if not plan["criterios"] or not plan["verificacion"]:
        return False, "criterios o verificación vacíos"
    if not all(isinstance(p, dict) and p.get("accion") for p in plan["pasos"]):
        return False, "pasos sin 'accion'"
    return True, "ok"


def planificar(tarea: dict, tier: str) -> dict:
    prompt = (f"TAREA:\n{tarea['enunciado']}\n\nENTRADAS:\n{describir_entradas(tarea)}\n\n{FORMATO_PLAN}")
    registro = {"tier_plan": tier, "coste_plan": 0.0, "llamadas_plan": 0}
    for intento in range(2):   # 1 reintento si el gate falla (como un ciclo del gate)
        r = llamar(MODELOS[tier], SISTEMA_PLAN, prompt)
        registro["coste_plan"] += r["coste_usd"]
        registro["llamadas_plan"] += 1
        try:
            plan = _json_de(r["texto"])
            ok, motivo = gate_plan(plan)
        except Exception as e:
            plan, ok, motivo = None, False, f"JSON inválido: {e}"
        if ok:
            return registro | {"plan": plan, "gate_ok": True, "gate_motivo": motivo}
        prompt += f"\n\nTu plan anterior no pasó el gate ({motivo}). Corrígelo."
    return registro | {"plan": None, "gate_ok": False, "gate_motivo": motivo}


def escribir_programa(tarea: dict, plan: dict | None) -> tuple[str, dict]:
    """El ejecutor (tier fijo) escribe el programa. Devuelve (código, llamada)."""
    partes = [f"TAREA:\n{tarea['enunciado']}", f"ENTRADAS (en el directorio actual):\n{describir_entradas(tarea)}"]
    if plan is not None:
        partes.append("PLAN A SEGUIR (escrito por el planificador):\n" + json.dumps(plan, ensure_ascii=False, indent=1))
    partes.append("Escribe el programa. Debe leer los archivos del directorio actual e imprimir por "
                  "stdout SOLO el JSON del estado final.")
    r = llamar(MODELOS[EJECUTOR], SISTEMA_EJEC, "\n\n".join(partes))
    m = re.search(r"```python\s*(.*?)```", r["texto"], re.S)
    return (m.group(1) if m else r["texto"]), r


def correr_programa(tarea: dict, codigo: str, objetivo: dict) -> dict:
    """Ejecuta un programa (generado o reutilizado) sobre la tarea y lo verifica. Coste LLM: 0."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        preparar_directorio(tarea, d)
        (d / "sol.py").write_text(codigo, encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, "sol.py"], cwd=d, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "detalle": "timeout 120 s"}
    if p.returncode != 0:
        return {"ok": False, "detalle": "error de ejecución: " + p.stderr.strip()[-300:]}
    try:
        estado = _json_de(p.stdout)
    except Exception:
        return {"ok": False, "detalle": "stdout no es JSON: " + p.stdout[:200]}
    ok, detalle = verificar(tarea, estado, objetivo)
    return {"ok": ok, "detalle": detalle}


def ejecutar(tarea: dict, objetivo: dict, plan: dict | None) -> dict:
    codigo, r = escribir_programa(tarea, plan)
    res = {"coste_ejec": r["coste_usd"], "tokens_in_ejec": r["tokens_in"], "tokens_out_ejec": r["tokens_out"]}
    return res | correr_programa(tarea, codigo, objetivo)


# ------------------------------------------------------------------ experimento

def unidad(tarea: dict, objetivo: dict, rep: int, solo_sin_plan: bool = False) -> list[dict]:
    """Una tarea × una réplica: 2 planes + 3 ejecuciones con el mismo ejecutor.
    Con solo_sin_plan (calibración) se ejecuta únicamente el brazo sin plan."""
    base = {"tarea": tarea["id"], "dominio": tarea["dominio"], "nivel": tarea["nivel"], "rep": rep}
    filas = []
    tiers = () if solo_sin_plan else ("barato", "caro")
    planes = {t: planificar(tarea, t) for t in tiers}
    brazos = [("sin_plan", None, {})] + [(f"plan_{t}", planes[t]["plan"], planes[t]) for t in tiers]
    for brazo, plan, info in brazos:
        fila = base | {"brazo": brazo, "gate_ok": info.get("gate_ok", True),
                       "gate_motivo": info.get("gate_motivo", ""), "coste_plan": info.get("coste_plan", 0.0),
                       "llamadas_plan": info.get("llamadas_plan", 0)}
        if brazo != "sin_plan" and not info["gate_ok"]:
            filas.append(fila | {"ok": False, "detalle": "plan no pasó el gate", "coste_ejec": 0.0})
            continue
        filas.append(fila | ejecutar(tarea, objetivo, plan))
    return filas


def seleccionar(n: int, semilla: int = 0, niveles: tuple[str, ...] | None = None,
                dominios: tuple[str, ...] | None = None) -> list[dict]:
    tareas = [json.loads(l) for l in open(BENCH / "tareas.jsonl", encoding="utf-8")]
    if niveles:
        tareas = [t for t in tareas if t["nivel"] in niveles]
    if dominios:
        tareas = [t for t in tareas if t["dominio"] in dominios]
    grupos: dict[tuple, list] = {}
    for t in tareas:
        grupos.setdefault((t["dominio"], t["nivel"]), []).append(t)
    por_grupo = max(1, n // len(grupos))
    rng = random.Random(semilla)
    elegidas = []
    for k in sorted(grupos):
        elegidas += rng.sample(grupos[k], min(por_grupo, len(grupos[k])))
    return elegidas


def main(n: int, reps: int, salida: Path, presupuesto: float, hilos: int,
         niveles: tuple[str, ...] | None = None, solo_sin_plan: bool = False,
         dominios: tuple[str, ...] | None = None) -> Path:
    salida.mkdir(parents=True, exist_ok=True)
    objetivos = {o["id"]: o["objetivo"] for o in map(json.loads, open(BENCH / "objetivos.jsonl", encoding="utf-8"))}
    trabajos = [(t, r) for r in range(reps) for t in seleccionar(n, niveles=niveles, dominios=dominios)]
    out = salida / "ejecuciones.jsonl"
    hechos = set()
    if out.exists():   # reanudable
        for l in open(out, encoding="utf-8"):
            f = json.loads(l)
            hechos.add((f["tarea"], f["rep"]))
    pendientes = [(t, r) for t, r in trabajos if (t["id"], r) not in hechos]
    print(f"{len(pendientes)} unidades pendientes de {len(trabajos)}", flush=True)
    with ThreadPoolExecutor(max_workers=hilos) as ex, open(out, "a", encoding="utf-8") as fh:
        futuros = {}
        for t, r in pendientes:
            futuros[ex.submit(unidad, t, objetivos[t["id"]], r, solo_sin_plan)] = (t["id"], r)
        for i, fut in enumerate(as_completed(futuros), 1):
            try:
                filas = fut.result()
            except Exception as e:
                print(f"ERROR {futuros[fut]}: {e}", flush=True)
                continue
            with _lock:
                for f in filas:
                    fh.write(json.dumps(f, ensure_ascii=False) + "\n")
                fh.flush()
            print(f"[{i}/{len(futuros)}] {futuros[fut]} gasto acumulado ${_gasto['usd']:.2f}", flush=True)
            if _gasto["usd"] > presupuesto:
                print(f"PRESUPUESTO AGOTADO (${presupuesto}); se cancela lo pendiente", flush=True)
                for f in futuros:
                    f.cancel()
                break
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tareas", type=int, default=12)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--salida", type=Path, default=Path("exp/piloto"))
    ap.add_argument("--presupuesto-usd", type=float, default=5.0)
    ap.add_argument("--hilos", type=int, default=6)
    ap.add_argument("--niveles", nargs="*", default=None, help="p. ej. --niveles extremo")
    ap.add_argument("--solo-sin-plan", action="store_true", help="calibración: solo el brazo sin plan")
    ap.add_argument("--dominios", nargs="*", default=None)
    a = ap.parse_args()
    print(main(a.tareas, a.reps, a.salida, a.presupuesto_usd, a.hilos,
               tuple(a.niveles) if a.niveles else None, a.solo_sin_plan,
               tuple(a.dominios) if a.dominios else None))
