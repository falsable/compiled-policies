"""H5: reutilizar con parámetros (README §12).

    python reutilizacion_parametros.py parametrizar --dominio negocio --presupuesto-usd 1.5
    python reutilizacion_parametros.py parametrizar --dominio datos --presupuesto-usd 1.5
    python reutilizacion_parametros.py evaluar --presupuesto-usd 1
    python reutilizacion_parametros.py analizar

parametrizar: Opus recibe SOLO la política base y el bloque validado de exp/reutilizacion (ninguna
              variante) y lo reescribe con un diccionario PARAMS. Paridad: con los valores por defecto
              da lo mismo que el bloque original en las 35 tareas idénticas y cosméticas del dominio,
              corridas en esta máquina. Hasta 2 intentos.
evaluar:      para las 20 sustantivas, Haiku decide si B sale de A cambiando solo parámetros y extrae
              los valores; si sí, se corre el bloque parametrizado con ellos (coste LLM 0).
analizar:     cascada nueva frente al router semántico de §7 y criterio de muerte de §12. No llama a modelos.
Gastan dinero (claude -p, precio de lista): parametrizar y evaluar. Salida en exp/parametros/ (nueva).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import experimento_kappa as E

RAIZ = Path(__file__).parent
PREVIA = RAIZ / "exp" / "reutilizacion"
SALIDA = RAIZ / "exp" / "parametros"
DOMINIOS = ("datos", "negocio")
V = 2.0
_lock = threading.Lock()

SISTEMA_PARAM = (
    "Eres un COMPILADOR DE BLOQUES. Recibes una POLÍTICA en lenguaje natural y el PROGRAMA Python validado "
    "que la implementa. Reescribe el programa para que cada valor o elección de la política que podría "
    "cambiar en una versión futura de esa misma política (umbrales, importes, porcentajes, ventanas de "
    "tiempo, categorías o términos, y reglas opcionales que podrían activarse, desactivarse o tomar otra "
    "variante) se lea de un diccionario PARAMS definido al inicio del programa. Los valores por defecto de "
    "PARAMS deben reproducir EXACTAMENTE el comportamiento actual. Justo después de definir PARAMS, si existe "
    "el archivo params.json en el directorio actual, actualiza PARAMS con su contenido. No cambies nada más. "
    "Responde con dos bloques: primero ```json``` con la lista de parámetros "
    '[{"nombre": "...", "tipo": "...", "defecto": ..., "valores_o_rango": "...", "descripcion": "..."}], '
    "y después ```python``` con el programa completo."
)
SISTEMA_EXTRAER = (
    "Eres un clasificador y extractor. Un programa parametrizado implementa la POLÍTICA A; su ESQUEMA de "
    "parámetros tiene como valores por defecto los de A. Te dan una POLÍTICA B. Decide si B se obtiene de A "
    "cambiando SOLO valores de esos parámetros, sin ninguna regla, criterio o paso que el esquema no pueda "
    "expresar. Si es así, da los valores nuevos de los parámetros que cambian. Ante cualquier diferencia que "
    'los parámetros no cubran, responde false. Responde SOLO JSON: {"solo_parametros": true|false, '
    '"params": {"nombre": valor}, "motivo": "..."}'
)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def texto_politica(t: dict) -> str:   # igual que en reutilizacion.py
    return t["entradas"]["politica"] if t["dominio"] == "negocio" else t["enunciado"]


def cargar():
    tareas = {t["id"]: t for f in ("tareas.jsonl", "variantes.jsonl")
              for t in map(json.loads, open(E.BENCH / f, encoding="utf-8"))}
    obj = {o["id"]: o["objetivo"] for f in ("objetivos.jsonl", "objetivos_variantes.jsonl")
           for o in map(json.loads, open(E.BENCH / f, encoding="utf-8"))}
    ev = {f["tarea"]: f for f in map(json.loads, open(PREVIA / "evaluacion.jsonl", encoding="utf-8"))}
    bloques = {d: json.loads((PREVIA / f"bloque_{d}.json").read_text(encoding="utf-8")) for d in DOMINIOS}
    return tareas, obj, ev, bloques


def correr(t: dict, codigo: str, objetivo: dict, params: dict | None = None) -> dict:
    """correr_programa de experimento_kappa, más params.json en el directorio. Coste LLM: 0."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        E.preparar_directorio(t, d)
        (d / "sol.py").write_text(codigo, encoding="utf-8")
        if params:
            (d / "params.json").write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, "sol.py"], cwd=d, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {"ok": False, "detalle": "timeout 120 s"}
    if p.returncode != 0:
        return {"ok": False, "detalle": "error de ejecución: " + p.stderr.strip()[-300:]}
    try:
        estado = E._json_de(p.stdout)
    except Exception:
        return {"ok": False, "detalle": "stdout no es JSON: " + p.stdout[:200]}
    ok, detalle = E.verificar(t, estado, objetivo)
    return {"ok": ok, "detalle": detalle}


def en_paralelo(f, xs):
    with ThreadPoolExecutor(4) as pool:
        return list(pool.map(f, xs))


def parametrizar(dominio: str, presupuesto: float) -> None:
    tareas, obj, ev, bloques = cargar()
    b, out = bloques[dominio], SALIDA / f"bloque_param_{dominio}.json"
    if out.exists():
        sys.exit(f"{out} ya existe: no se sobrescribe")
    control = sorted(k for k, f in ev.items() if f["dominio"] == dominio and f["categoria"] != "sustantiva")
    base = dict(zip(control, (r["ok"] for r in en_paralelo(lambda k: correr(tareas[k], b["codigo"], obj[k]), control))))
    deriva = sum(base[k] != ev[k]["reuso_ok"] for k in control)
    print(f"{dominio}: línea base del bloque original en esta máquina {sum(base.values())}/{len(control)} ok; "
          f"difiere de lo grabado en {deriva}")
    prompt = f"POLÍTICA:\n{b['contrato_texto']}\n\nPROGRAMA VALIDADO:\n```python\n{b['codigo']}\n```"
    intentos, final = [], None
    for i in range(2):
        if E._gasto["usd"] >= presupuesto:
            print("presupuesto agotado")
            break
        r = E.llamar(E.MODELOS["caro"], SISTEMA_PARAM, prompt)
        mc = re.search(r"```python\s*(.*?)```", r["texto"], re.S)
        mj = re.search(r"```json\s*(.*?)```", r["texto"], re.S)
        try:
            esquema = json.loads(mj.group(1)) if mj else None
        except ValueError:
            esquema = None
        codigo = mc.group(1) if mc else ""
        gate = bool(codigo and "PARAMS" in codigo and "params.json" in codigo and isinstance(esquema, list) and esquema)
        paridad = dict(zip(control, (x["ok"] for x in en_paralelo(lambda k: correr(tareas[k], codigo, obj[k]), control)))) if gate else {}
        iguales = sum(paridad.get(k) == base[k] for k in control)
        intentos.append({"intento": i + 1, "coste_usd": r["coste_usd"], "gate": gate, "paridad": f"{iguales}/{len(control)}",
                         "parametros": [p.get("nombre") for p in esquema] if gate else []})
        print(f"  intento {i + 1}: gate {gate}, paridad {iguales}/{len(control)}, "
              f"{len(esquema) if gate else 0} parámetros, {r['coste_usd']:.3f} $")
        if gate and iguales == len(control):
            final = {"esquema": esquema, "codigo": codigo}
            break
        motivo = "no respeta el formato pedido" if not gate else \
            f"con los valores por defecto difiere del original en {len(control) - iguales} de {len(control)} tareas"
        prompt += f"\n\nTu versión anterior no sirve: {motivo}. Corrígela."
    SALIDA.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dominio": dominio, "ok": final is not None, **(final or {}),
                               "coste_parametrizacion": sum(x["coste_usd"] for x in intentos), "intentos": intentos,
                               "linea_base": {"control": len(control), "ok": sum(base.values()), "deriva_vs_grabado": deriva},
                               "sha256": {"script": sha(Path(__file__).read_bytes()), "bloque": b["hash"]}},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{dominio}: {'PARAMETRIZADO' if final else 'SIN PARIDAD'}; gasto acumulado {E._gasto['usd']:.3f} $")


def evaluar(presupuesto: float) -> None:
    tareas, obj, ev, bloques = cargar()
    out = SALIDA / "evaluacion.jsonl"
    if out.exists():
        sys.exit(f"{out} ya existe: no se sobrescribe")
    param = {d: json.loads(p.read_text(encoding="utf-8")) for d in DOMINIOS
             if (p := SALIDA / f"bloque_param_{d}.json").exists()}
    sust = sorted(k for k, f in ev.items() if f["categoria"] == "sustantiva")

    def una(k: str) -> dict:
        f = ev[k]
        fila = {"tarea": k, "dominio": f["dominio"], "variante": f["variante"], "cubre": False, "coste_extraccion": 0.0}
        bp = param.get(f["dominio"])
        if not bp or not bp["ok"]:
            return fila | {"motivo": "dominio sin parametrizar"}
        with _lock:
            if E._gasto["usd"] >= presupuesto:
                return fila | {"motivo": "presupuesto agotado"}
        esquema = json.dumps(bp["esquema"], ensure_ascii=False, indent=1)
        r = E.llamar(E.MODELOS["barato"], SISTEMA_EXTRAER,
                     f"POLÍTICA A:\n{bloques[f['dominio']]['contrato_texto']}\n\nESQUEMA:\n{esquema}\n\n"
                     f"POLÍTICA B:\n{texto_politica(tareas[k])}")
        try:
            d = E._json_de(r["texto"])
            cubre, params = bool(d.get("solo_parametros")), d.get("params") or {}
            motivo = str(d.get("motivo", ""))[:200]
        except Exception:
            cubre, params, motivo = False, {}, "respuesta no parseable"
        fila |= {"cubre": cubre, "params": params, "motivo": motivo, "coste_extraccion": r["coste_usd"]}
        if cubre:
            res = correr(tareas[k], bp["codigo"], obj[k], params)
            fila |= {"param_ok": res["ok"], "param_detalle": res["detalle"][:160]}
        return fila

    filas = en_paralelo(una, sust)
    SALIDA.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in filas), encoding="utf-8")
    for x in filas:
        print(f"  {x['tarea']:16} {x['variante']:16} cubre={str(x['cubre']):5} ok={x.get('param_ok', '-')!s:5} {x.get('motivo', '')[:70]}")
    print(f"gasto acumulado {E._gasto['usd']:.3f} $")


def analizar() -> None:
    tareas, obj, ev, bloques = cargar()
    param = {d: json.loads((SALIDA / f"bloque_param_{d}.json").read_text(encoding="utf-8")) for d in DOMINIOS
             if (SALIDA / f"bloque_param_{d}.json").exists()}
    h5 = {x["tarea"]: x for x in map(json.loads, open(SALIDA / "evaluacion.jsonl", encoding="utf-8"))}
    n_dom = {d: sum(f["dominio"] == d for f in ev.values()) for d in DOMINIOS}
    am2 = {d: bloques[d]["coste_compilacion"] / n_dom[d] for d in DOMINIOS}
    am5 = {d: am2[d] + param.get(d, {}).get("coste_parametrizacion", 0.0) / n_dom[d] for d in DOMINIOS}
    tot2 = tot5 = ok2 = ok5 = 0.0
    for k, f in ev.items():
        reusa = bool(f["juez_equivalentes"] and f["juez_p"] >= 0.8)   # router semántico de §7
        o2 = f["reuso_ok"] if reusa else f["gen_ok"]
        c2 = (0.0 if reusa else f["gen_coste"]) + f["coste_juez"] + am2[f["dominio"]]
        o5, c5 = o2, c2 - am2[f["dominio"]] + am5[f["dominio"]]
        if not reusa and k in h5:
            x = h5[k]
            c5 += x["coste_extraccion"]
            if x["cubre"]:
                o5, c5 = x.get("param_ok", False), c5 - f["gen_coste"]
        tot2, tot5 = tot2 + c2 + (not o2) * V, tot5 + c5 + (not o5) * V
        ok2, ok5 = ok2 + o2, ok5 + o5
    n = len(ev)
    cubiertas = sum(x["cubre"] for x in h5.values())
    silenciosos = sum(x["cubre"] and not x.get("param_ok") for x in h5.values())
    ahorro = 1 - tot5 / tot2
    muere = silenciosos > 0 or cubiertas < 5 or ahorro < 0.10
    res = {"tareas": n, "sustantivas_cubiertas": cubiertas, "fallos_silenciosos": silenciosos,
           "acierto_router_%": 100 * ok2 / n, "acierto_param_%": 100 * ok5 / n,
           "coste_total_medio_router_$": tot2 / n, "coste_total_medio_param_$": tot5 / n, "ahorro_%": 100 * ahorro,
           "parametrizados": {d: param[d]["ok"] for d in param},
           "veredicto": "H5 MUERE" if muere else "H5 SOBREVIVE",
           "sha256": {"script": sha(Path(__file__).read_bytes()),
                      "exp/reutilizacion/evaluacion.jsonl": sha((PREVIA / "evaluacion.jsonl").read_bytes())}}
    (SALIDA / "resumen.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("etapa", choices=["parametrizar", "evaluar", "analizar"])
    ap.add_argument("--dominio", choices=DOMINIOS)
    ap.add_argument("--presupuesto-usd", type=float, default=0.0)
    a = ap.parse_args()
    if a.etapa == "parametrizar":
        parametrizar(a.dominio, a.presupuesto_usd)
    elif a.etapa == "evaluar":
        evaluar(a.presupuesto_usd)
    else:
        analizar()
