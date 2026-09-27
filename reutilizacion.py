"""H2: ¿reutilizar un bloque compilado o generar un programa nuevo?

    python reutilizacion.py compilar  --presupuesto-usd 4
    python reutilizacion.py evaluar   --presupuesto-usd 13
    python reutilizacion.py analizar

compilar: por dominio, Opus planifica + Haiku escribe; cada candidato se valida en las 5 tareas de
          entrenamiento (extremo 0–4). Se queda el que más acierta (empate: el más barato).
evaluar:  para cada tarea del flujo de prueba se registra TODO lo necesario para simular cualquier
          política: resultado de reutilizar el bloque (gratis), decisión de cada router y resultado
          de generar (de exp/completo para las idénticas; nuevo para las variantes).
analizar: aplica cada política y los criterios de muerte K1/K2 (ver README §7).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import experimento_kappa as E
from benchmark import extremo as X

BENCH = E.BENCH
SALIDA = Path(__file__).parent / "exp" / "reutilizacion"
DOMINIOS = ("datos", "negocio")
CONTRATO = {"negocio": X.POLITICA_EXTREMO, "datos": X.ENUNCIADO_DATOS}
V = 2.0
_lock = threading.Lock()


def texto_politica(t: dict) -> str:
    return t["entradas"]["politica"] if t["dominio"] == "negocio" else t["enunciado"]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def firma(texto: str) -> dict:
    """Firma determinista: números (sin enumeradores de lista) + términos entre comillas dobles."""
    t = re.sub(r"(?m)^\s*(\d+\.|[a-z]\))\s", " ", texto)
    return {"numeros": sorted(re.findall(r"\d+(?:[.,]\d+)*", t)),
            "comillas": sorted(set(re.findall(r'"([^"]+)"', t)))}


def cargar():
    tareas = {t["id"]: t for f in ("tareas.jsonl", "variantes.jsonl")
              for t in map(json.loads, open(BENCH / f, encoding="utf-8"))}
    obj = {o["id"]: o["objetivo"] for f in ("objetivos.jsonl", "objetivos_variantes.jsonl")
           for o in map(json.loads, open(BENCH / f, encoding="utf-8"))}
    return tareas, obj


# =============================================================== compilar

def compilar_dominio(dominio: str, tareas: dict, obj: dict, n_val: int = 5, max_cand: int = 4) -> dict:
    train = [tareas[f"{dominio}-extremo-{i:03d}"] for i in range(n_val)]
    candidatos, coste_total = [], 0.0
    for k in range(max_cand):
        t = train[k % n_val]
        pl = E.planificar(t, "caro")
        coste = pl["coste_plan"]
        if not pl["gate_ok"]:
            coste_total += coste
            continue
        codigo, r = E.escribir_programa(t, pl["plan"])
        coste += r["coste_usd"]
        coste_total += coste
        res = [E.correr_programa(x, codigo, obj[x["id"]]) for x in train]
        aciertos = sum(x["ok"] for x in res)
        candidatos.append({"candidato": k, "entrenado_en": t["id"], "aciertos": aciertos, "coste": coste,
                           "codigo": codigo, "fallos": [x["detalle"][:120] for x in res if not x["ok"]]})
        print(f"[{dominio}] candidato {k}: {aciertos}/{n_val} (${coste:.3f})", flush=True)
        if aciertos == n_val:
            break
    mejor = max(candidatos, key=lambda c: (c["aciertos"], -c["coste"]))
    return {"dominio": dominio, "contrato_texto": CONTRATO[dominio], "hash": sha(CONTRATO[dominio]),
            "firma": firma(CONTRATO[dominio]), "codigo": mejor["codigo"],
            "validacion": f"{mejor['aciertos']}/{n_val}", "coste_compilacion": coste_total,
            "candidatos": [{k: v for k, v in c.items() if k != "codigo"} for c in candidatos]}


def compilar(presupuesto: float) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    tareas, obj = cargar()
    with ThreadPoolExecutor(max_workers=2) as ex:
        for b in ex.map(lambda d: compilar_dominio(d, tareas, obj), DOMINIOS):
            (SALIDA / f"bloque_{b['dominio']}.json").write_text(json.dumps(b, ensure_ascii=False, indent=1))
            print(f"bloque {b['dominio']}: validación {b['validacion']}, compilación ${b['coste_compilacion']:.2f}")
    print(f"gasto: ${E._gasto['usd']:.2f}")


# =============================================================== evaluar

SISTEMA_JUEZ = (
    "Eres un clasificador. Decides si un programa escrito para la POLÍTICA A sirve SIN CAMBIOS para la "
    "POLÍTICA B, es decir, si ambas producen exactamente el mismo resultado para cualquier entrada. "
    "Cambios de redacción, orden de exposición o formato NO importan. Cualquier cambio de umbral, "
    "porcentaje, ventana, categoría, criterio o regla SÍ importa. Responde SOLO con JSON: "
    '{"equivalentes": true|false, "p": probabilidad de que sean equivalentes (0-1), "diferencia": "..."}'
)


def juez(texto_bloque: str, texto_tarea: str) -> dict:
    r = E.llamar(E.MODELOS["barato"], SISTEMA_JUEZ,
                 f"POLÍTICA A (la del programa existente):\n{texto_bloque}\n\nPOLÍTICA B (la de la tarea nueva):\n{texto_tarea}")
    try:
        d = E._json_de(r["texto"])
        eq, p = bool(d.get("equivalentes")), float(d.get("p", 0.0))
    except Exception:
        eq, p, d = False, 0.0, {"diferencia": "respuesta no parseable"}
    return {"juez_equivalentes": eq, "juez_p": p, "juez_diferencia": str(d.get("diferencia", ""))[:200],
            "coste_juez": r["coste_usd"]}


def generar_desde_cero(t: dict, o: dict) -> dict:
    pl = E.planificar(t, "caro")
    coste = pl["coste_plan"]
    if not pl["gate_ok"]:
        return {"gen_ok": False, "gen_coste": coste, "gen_detalle": "plan no pasó el gate"}
    codigo, r = E.escribir_programa(t, pl["plan"])
    res = E.correr_programa(t, codigo, o)
    return {"gen_ok": res["ok"], "gen_coste": coste + r["coste_usd"], "gen_detalle": res["detalle"][:160]}


def generar_previo() -> dict:
    """'Generar siempre' en las tareas idénticas: brazo plan Opus de exp/completo (misma canalización)."""
    out = {}
    for l in open(Path(__file__).parent / "exp" / "completo" / "ejecuciones.jsonl", encoding="utf-8"):
        f = json.loads(l)
        if f["brazo"] == "plan_caro":
            out[f["tarea"]] = {"gen_ok": f["ok"], "gen_coste": f["coste_plan"] + f.get("coste_ejec", 0.0),
                               "gen_detalle": f["detalle"][:160], "gen_origen": "exp/completo"}
    return out


def evaluar_tarea(t: dict, o: dict, bloque: dict, previo: dict) -> dict:
    texto = texto_politica(t)
    cat = "identica" if t["nivel"] == "extremo" else t["variante"]["tipo"]
    fila = {"tarea": t["id"], "dominio": t["dominio"], "categoria": cat,
            "variante": t.get("variante", {}).get("nombre", "base"),
            "equivalente": cat != "sustantiva"}
    reuso = E.correr_programa(t, bloque["codigo"], o)
    fila |= {"reuso_ok": reuso["ok"], "reuso_detalle": reuso["detalle"][:160]}
    fila |= {"hash_igual": sha(texto) == bloque["hash"], "firma_igual": firma(texto) == bloque["firma"]}
    if fila["hash_igual"]:   # cascada: el juez solo se consulta si el texto cambió
        fila |= {"juez_equivalentes": True, "juez_p": 1.0, "juez_diferencia": "texto idéntico", "coste_juez": 0.0}
    else:
        fila |= juez(bloque["contrato_texto"], texto)
    fila |= previo[t["id"]] if t["id"] in previo else (generar_desde_cero(t, o) | {"gen_origen": "nuevo"})
    return fila


def evaluar(presupuesto: float, hilos: int) -> None:
    tareas, obj = cargar()
    previo = generar_previo()
    bloques = {d: json.loads((SALIDA / f"bloque_{d}.json").read_text()) for d in DOMINIOS}
    flujo = [t for t in tareas.values()
             if (t["nivel"] == "extremo" and int(t["id"][-3:]) >= 5) or t["nivel"] == "variante"]
    out = SALIDA / "evaluacion.jsonl"
    hechos = {json.loads(l)["tarea"] for l in open(out, encoding="utf-8")} if out.exists() else set()
    pendientes = [t for t in flujo if t["id"] not in hechos]
    print(f"{len(pendientes)} tareas pendientes de {len(flujo)}", flush=True)
    with ThreadPoolExecutor(max_workers=hilos) as ex, open(out, "a", encoding="utf-8") as fh:
        futuros = {ex.submit(evaluar_tarea, t, obj[t["id"]], bloques[t["dominio"]], previo): t["id"]
                   for t in pendientes}
        for i, fut in enumerate(as_completed(futuros), 1):
            try:
                fila = fut.result()
            except Exception as e:
                print(f"ERROR {futuros[fut]}: {e}", flush=True)
                continue
            with _lock:
                fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
                fh.flush()
            print(f"[{i}/{len(futuros)}] {futuros[fut]} gasto ${E._gasto['usd']:.2f}", flush=True)
            if E._gasto["usd"] > presupuesto:
                print("PRESUPUESTO AGOTADO: se esperan las tareas en curso y no se lanzan más", flush=True)
                for f in futuros:
                    f.cancel()


# =============================================================== analizar

POLITICAS = {
    "generar_siempre": lambda f: False,
    "reutilizar_siempre": lambda f: True,
    "router_hash": lambda f: f["hash_igual"],
    "router_firma": lambda f: f["firma_igual"],
    "router_semantico": lambda f: f["juez_equivalentes"] and f["juez_p"] >= 0.8,
}


def aplicar(df: pd.DataFrame, pol: str, amortizar: dict[str, float]) -> pd.DataFrame:
    reusa = df.apply(POLITICAS[pol], axis=1).astype(bool)
    coste_router = df["coste_juez"] * (pol == "router_semantico")
    ok = df["reuso_ok"].where(reusa, df["gen_ok"]).astype(bool)
    coste = df["gen_coste"].where(~reusa, 0.0) + coste_router
    if pol != "generar_siempre":   # el bloque se compila una vez: se reparte entre las tareas del dominio
        coste = coste + df["dominio"].map(amortizar)
    return pd.DataFrame({"categoria": df["categoria"], "dominio": df["dominio"], "reusa": reusa, "ok": ok,
                         "coste": coste, "total": coste + (~ok) * V,
                         "fallo_silencioso": reusa & ~df["reuso_ok"].astype(bool)})


def analizar() -> dict:
    df = pd.DataFrame([json.loads(l) for l in open(SALIDA / "evaluacion.jsonl", encoding="utf-8")])
    bloques = {d: json.loads((SALIDA / f"bloque_{d}.json").read_text()) for d in DOMINIOS}
    n_dom = df.groupby("dominio").size()
    amort = {d: bloques[d]["coste_compilacion"] / n_dom[d] for d in DOMINIOS}
    filas, por_cat = [], {}
    for pol in POLITICAS:
        r = aplicar(df, pol, amort)
        filas.append({"politica": pol, "reutiliza_%": 100 * r.reusa.mean(), "acierto_%": 100 * r.ok.mean(),
                      "coste_medio_$": r.coste.mean(), "coste_total_$": r.total.mean(),
                      "fallos_silenciosos": int(r.fallo_silencioso.sum())})
        por_cat[pol] = r.groupby("categoria")[["ok", "coste", "total"]].mean()
    tabla = pd.DataFrame(filas).set_index("politica")
    base = tabla.loc["generar_siempre"]
    tabla["ahorro_vs_generar_%"] = 100 * (1 - tabla["coste_total_$"] / base["coste_total_$"])

    routers = ["router_hash", "router_firma", "router_semantico"]
    mejor = tabla.loc[routers, "coste_total_$"].idxmin()
    k1 = bool(tabla.loc[mejor, "ahorro_vs_generar_%"] >= 30 and
              tabla.loc[mejor, "acierto_%"] >= base["acierto_%"] - 2)
    det = tabla.loc[["router_hash", "router_firma"], "coste_total_$"].idxmin()
    k2 = bool(tabla.loc["router_semantico", "coste_total_$"] <= 0.95 * tabla.loc[det, "coste_total_$"] and
              tabla.loc["router_semantico", "fallos_silenciosos"] <= tabla.loc[det, "fallos_silenciosos"])

    # precisión de cada router frente a la etiqueta 'equivalente' (idéntica/cosmética = sí; sustantiva = no)
    precision = {}
    for pol in routers:
        pred = df.apply(POLITICAS[pol], axis=1).astype(bool)
        precision[pol] = {
            "acierto_%": float(100 * (pred == df["equivalente"]).mean()),
            "reutiliza_sustantivas": int((pred & ~df["equivalente"]).sum()),
            "regenera_equivalentes": int((~pred & df["equivalente"]).sum()),
        }

    # sensibilidad a la mezcla del flujo (idénticas, cosméticas, sustantivas)
    mezclas = {"90/5/5": (0.9, 0.05, 0.05), "56/22/22 (esta)": None, "20/40/40": (0.2, 0.4, 0.4),
               "0/50/50": (0.0, 0.5, 0.5)}
    sens = {}
    for nombre, w in mezclas.items():
        fila = {}
        for pol in POLITICAS:
            c = por_cat[pol]["total"]
            fila[pol] = float(aplicar(df, pol, amort).total.mean()) if w is None else \
                float(w[0] * c.get("identica", 0) + w[1] * c.get("cosmetica", 0) + w[2] * c.get("sustantiva", 0))
        sens[nombre] = fila

    res = {"tabla": tabla.round(4).reset_index().to_dict(orient="records"),
           "por_categoria": {p: v.round(4).to_dict() for p, v in por_cat.items()},
           "mejor_router": mejor, "mejor_determinista": det, "K1_supera": k1, "K2_supera": k2,
           "precision_routers": precision, "sensibilidad_mezcla_coste_total": sens,
           "bloques": {d: {"validacion": b["validacion"], "coste_compilacion": b["coste_compilacion"]}
                       for d, b in bloques.items()},
           "gasto_generacion_nueva_$": float(df.loc[df.gen_origen == "nuevo", "gen_coste"].sum()),
           "gasto_juez_$": float(df["coste_juez"].sum())}
    (SALIDA / "resumen.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    pd.set_option("display.width", 200)
    print(tabla.round(3))
    print(pd.DataFrame(precision).T)
    print(pd.DataFrame(sens).T.round(3))
    print({k: res[k] for k in ("mejor_router", "mejor_determinista", "K1_supera", "K2_supera", "bloques",
                               "gasto_generacion_nueva_$", "gasto_juez_$")})
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["compilar", "evaluar", "analizar"])
    ap.add_argument("--presupuesto-usd", type=float, default=5.0)
    ap.add_argument("--hilos", type=int, default=8)
    a = ap.parse_args()
    if a.accion == "compilar":
        compilar(a.presupuesto_usd)
    elif a.accion == "evaluar":
        evaluar(a.presupuesto_usd, a.hilos)
    else:
        analizar()
