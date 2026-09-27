"""Grabador del corpus dorado — ADR-001-BE, paso 6 (adelantado).

DESECHABLE: es código de investigación que recorre la cascada del ADR-001-FN sobre 130 tareas y
guarda TODO lo necesario para que el núcleo Rust pruebe cada decisión sin llamar a ningún modelo:

  corpus/v1/bloques.jsonl       Bloque (contrato v1) por dominio, con su dominio de validez (30 ejemplos)
  corpus/v1/tareas.jsonl        TareaSolicitud (contrato v1) + id de benchmark
  corpus/v1/eventos.jsonl       Evento (contrato v1): pasos, artefactos por hash, costes
  corpus/v1/resoluciones.jsonl  Resolucion (contrato v1)
  corpus/v1/escalados.jsonl     Escalado (contrato v1)
  corpus/v1/llamadas.jsonl      cada llamada a un modelo, en orden por tarea (para adaptadores grabados)
  corpus/v1/esperado.jsonl      decisión + hash de salida esperados + acierto contra la verdad
  corpus/v1/artefactos/         almacén direccionado por contenido (sha256)

    python corpus/grabar.py grabar --presupuesto-usd 16
    python corpus/grabar.py reproducir          # sin llamadas: misma decisión y salida en el 100 %
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# ejecución determinista de los programas: el orden de sets/dicts no depende de la semilla de hash
os.environ["PYTHONHASHSEED"] = "0"

from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

import comprobaciones as C  # noqa: E402
import experimento_kappa as E  # noqa: E402
import reutilizacion as R  # noqa: E402
from benchmark.verificar import verificar  # noqa: E402

CORPUS = RAIZ / "corpus" / "v1"
ART = CORPUS / "artefactos"
SV = "1.0.0"
V_EUR = 2.0
PRESUPUESTO_TAREA = 1.0
_lock = threading.Lock()
_ctx = threading.local()

# ------------------------------------------------------------------ contrato

ESQUEMA = json.loads((RAIZ / "contracts" / "v1" / "bloques.schema.json").read_text())
_VALIDADORES = {}


def validar(nombre: str, inst: dict) -> None:
    if nombre not in _VALIDADORES:
        _VALIDADORES[nombre] = Draft202012Validator(
            {"$schema": ESQUEMA["$schema"], "$defs": ESQUEMA["$defs"], "$ref": f"#/$defs/{nombre}"},
            format_checker=FormatChecker())
    errores = list(_VALIDADORES[nombre].iter_errors(inst))
    if errores:
        raise ValueError(f"{nombre} no cumple el contrato v1: {errores[0].message} en {list(errores[0].path)}")


# ------------------------------------------------------------------ utilidades

CROCK = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(prefijo: str) -> str:
    n = (int(time.time() * 1000) << 80) | int.from_bytes(os.urandom(10), "big")
    return prefijo + "_" + "".join(CROCK[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def ahora() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def poner(datos: bytes | str) -> str:
    datos = datos.encode() if isinstance(datos, str) else datos
    h = hashlib.sha256(datos).hexdigest()
    p = ART / h[:2] / h[2:]
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(datos)
    return "sha256:" + h


def leer(ref: str) -> bytes:
    h = ref.split(":", 1)[1]
    return (ART / h[:2] / h[2:]).read_bytes()


def paso(tipo, resultado, coste=0.0, prob=None, artefacto=None, modelo=None) -> dict:
    p = {"tipo": tipo, "resultado": resultado, "coste_eur": round(float(coste), 6)}
    if prob is not None:
        p["prob"] = round(float(prob), 4)
    if artefacto:
        p["artefacto"] = artefacto
    if modelo:
        p["modelo"] = modelo
    return p


# ------------------------------------------------------------------ llamadas a modelos: grabar / reproducir

_llamar_real = E.llamar
_GRABADAS: dict[str, list[dict]] = {}


def _clave(modelo, sistema, prompt) -> str:
    return hashlib.sha256(f"{modelo}\0{sistema}\0{prompt}".encode()).hexdigest()


def llamar_grabando(modelo, sistema, prompt, timeout=600):
    r = _llamar_real(modelo, sistema, prompt, timeout)
    reg = {"tarea": _ctx.tarea, "n": _ctx.n, "modelo": modelo, "clave": _clave(modelo, sistema, prompt),
           "sistema": poner(sistema), "prompt": poner(prompt), "respuesta": poner(r["texto"]),
           "coste_usd": r["coste_usd"], "tokens_in": r["tokens_in"], "tokens_out": r["tokens_out"]}
    _ctx.n += 1
    _ctx.llamadas.append(reg)
    return r


def llamar_reproduciendo(modelo, sistema, prompt, timeout=600):
    lista = _GRABADAS.get(_ctx.tarea, [])
    if _ctx.n >= len(lista):
        raise RuntimeError(f"{_ctx.tarea}: llamada {_ctx.n} no grabada (divergencia)")
    reg = lista[_ctx.n]
    if reg["clave"] != _clave(modelo, sistema, prompt):
        raise RuntimeError(f"{_ctx.tarea}: el prompt de la llamada {_ctx.n} difiere del grabado (divergencia)")
    _ctx.n += 1
    return {"texto": leer(reg["respuesta"]).decode(), "coste_usd": 0.0, "segundos": 0.0,
            "tokens_in": 0, "tokens_out": 0}


# ------------------------------------------------------------------ bloques y umbral

def umbral_reutilizacion() -> float:
    """RN3 con los costes medidos en H2 (exp/reutilizacion): p* = 1 − (c_gen + (1 − q_gen)·V)/V."""
    r = json.loads((RAIZ / "exp" / "reutilizacion" / "resumen.json").read_text())
    g = next(x for x in r["tabla"] if x["politica"] == "generar_siempre")
    c_gen, q_gen = g["coste_medio_$"], g["acierto_%"] / 100
    return round(1 - (c_gen + (1 - q_gen) * V_EUR) / V_EUR, 4)


def perfil_contrato(p: dict) -> dict:
    conjuntos, rangos = {}, {}
    for k, v in p.items():
        if isinstance(v, set):
            conjuntos[k] = sorted(str(x) for x in v)
        elif isinstance(v, bool):
            conjuntos[k] = [str(v).lower()]
        else:
            rangos[k] = [float(v[0]), float(v[1])]
    return {"n_ejemplos": 30, "conjuntos": conjuntos, "rangos": rangos, "tolerancia_rango": 1.5}


def preparar_bloques(tareas: dict) -> dict:
    out = {}
    for d in R.DOMINIOS:
        a = json.loads((R.SALIDA / f"bloque_{d}.json").read_text())
        perf = C.perfil(C.entrenamiento(tareas, d) + C.calibracion(d))
        b = {"schema_version": SV, "id": ulid("blq"), "proceso": f"{d}-extremo", "version": "1.0.0",
             "hash_codigo": poner(a["codigo"]), "politica_hash": "sha256:" + a["hash"],
             "politica_texto": a["contrato_texto"], "dominio_validez": perfil_contrato(perf),
             "validacion": {"aprobados": 5, "total": 5}, "coste_compilacion_eur": round(a["coste_compilacion"], 6),
             "estado": "aprobado", "aprobado_por": "aldemar"}
        validar("Bloque", b)
        out[d] = {"contrato": b, "codigo": a["codigo"], "texto": a["contrato_texto"], "hash": a["hash"], "perfil": perf}
    return out


# ------------------------------------------------------------------ cascada (ADR-001-FN)

def _generar(t: dict, o: dict, pasos: list) -> dict:
    """Plan con el generador de mayor capacidad (RN5) + programa + ejecución + invariantes."""
    pl = E.planificar(t, "caro")
    pasos.append(paso("generar_plan", "ok" if pl["gate_ok"] else "gate_fallido", pl["coste_plan"],
                      artefacto=poner(canon(pl["plan"])) if pl["plan"] else None, modelo=E.MODELOS["caro"]))
    coste = pl["coste_plan"]
    if not pl["gate_ok"]:
        return {"salida": None, "coste": coste, "valida": False}
    codigo, r = E.escribir_programa(t, pl["plan"])
    coste += r["coste_usd"]
    pasos.append(paso("generar_programa", "ok", r["coste_usd"], artefacto=poner(codigo), modelo=E.MODELOS["barato"]))
    rr = C.correr_bloque(t, codigo)
    if rr["revienta"]:
        pasos.append(paso("ejecutar_programa", "revienta"))
        return {"salida": None, "coste": coste, "valida": False}
    pasos.append(paso("ejecutar_programa", "ok", artefacto=poner(canon(rr["salida"]))))
    inv = C.invariantes(t, rr["salida"])
    pasos.append(paso("invariantes", "ok" if not inv else "violados"))
    return {"salida": rr["salida"], "coste": coste, "valida": not inv}


def resolver(t: dict, o: dict, bloque: dict, umbral: float) -> dict:
    pasos, coste_gen, coste_juez = [], 0.0, 0.0
    texto = R.texto_politica(t)
    evid = {"umbral_reutilizacion": umbral}
    hash_igual = R.sha(texto) == bloque["hash"]
    evid["hash_igual"] = hash_igual
    pasos.append(paso("hash", "igual" if hash_igual else "distinto"))
    equivalente = hash_igual
    if not hash_igual:   # RN4: el juez solo si el texto cambió
        j = R.juez(bloque["texto"], texto)
        coste_juez += j["coste_juez"]
        equivalente = j["juez_equivalentes"] and j["juez_p"] > umbral
        evid["juez_p"] = round(j["juez_p"], 4)
        pasos.append(paso("juez", "equivalente" if equivalente else "no_equivalente", j["coste_juez"], prob=j["juez_p"],
                          modelo=E.MODELOS["barato"]))
    decision, salida, escalado, usa_bloque = None, None, None, False
    if not equivalente:
        g = _generar(t, o, pasos)
        coste_gen += g["coste"]
        decision, salida = ("generar", g["salida"]) if g["valida"] else ("no_resuelta", None)
    else:
        motivos = C.fuera_de_perfil(t, bloque["perfil"])
        evid["perfil_motivos"] = motivos
        pasos.append(paso("perfil", "dentro" if not motivos else "fuera"))
        rb = C.correr_bloque(t, bloque["codigo"])
        evid["bloque_reventó"] = rb["revienta"]
        pasos.append(paso("ejecutar_bloque", "revienta" if rb["revienta"] else "ok",
                          artefacto=None if rb["revienta"] else poner(canon(rb["salida"]))))
        inv = [] if rb["revienta"] else C.invariantes(t, rb["salida"])
        if not rb["revienta"]:
            evid["invariantes_violados"] = inv
            pasos.append(paso("invariantes", "ok" if not inv else "violados"))
        if not motivos and not rb["revienta"] and not inv:
            decision, salida, usa_bloque = "reutilizar", rb["salida"], True
        elif rb["revienta"] or inv:           # fallo visible del bloque → generar (escenario del FN)
            g = _generar(t, o, pasos)
            coste_gen += g["coste"]
            decision, salida = ("generar", g["salida"]) if g["valida"] else ("no_resuelta", None)
        else:                                  # fuera de dominio → verificación cruzada (RN7)
            g = _generar(t, o, pasos)
            coste_gen += g["coste"]
            acuerdo = g["valida"] and C.mismas_salidas(t, rb["salida"], g["salida"])
            evid["acuerdo"] = acuerdo
            pasos.append(paso("comparar", "acuerdo" if acuerdo else "desacuerdo"))
            if acuerdo:
                decision, salida, usa_bloque = "verificacion_cruzada", rb["salida"], True
            else:                              # RN8: ni el plan B es oráculo → humano
                decision = "escalar"
                pasos.append(paso("escalar", "desacuerdo_verificacion_cruzada"))
                salidas = [{"origen": "bloque", "hash_salida": poner(canon(rb["salida"]))}]
                if g["salida"] is not None:
                    salidas.append({"origen": "nueva", "hash_salida": poner(canon(g["salida"]))})
                escalado = {"motivo": "desacuerdo_verificacion_cruzada", "salidas": salidas, "perfil_motivos": motivos}
    if coste_gen + coste_juez > PRESUPUESTO_TAREA and decision not in ("escalar",):   # RN10
        pasos.append(paso("presupuesto", "agotado"))
        decision, salida, escalado = "escalar", None, {"motivo": "presupuesto_agotado", "salidas": [], "perfil_motivos": []}
    return {"pasos": pasos, "decision": decision, "salida": salida if decision not in ("escalar", "no_resuelta") else None,
            "evidencia": evid, "coste_gen": coste_gen, "coste_juez": coste_juez, "usa_bloque": usa_bloque,
            "escalado": escalado}


# ------------------------------------------------------------------ flujo

def flujo(tareas: dict) -> list[dict]:
    return [t for t in tareas.values() if (t["nivel"] == "extremo" and int(t["id"][-3:]) >= 5)
            or t["nivel"] in ("variante", "deriva")]


def cargar_tareas():
    tareas = {t["id"]: t for f in ("tareas.jsonl", "variantes.jsonl", "deriva.jsonl")
              for t in map(json.loads, open(E.BENCH / f, encoding="utf-8"))}
    obj = {o["id"]: o["objetivo"] for f in ("objetivos.jsonl", "objetivos_variantes.jsonl", "objetivos_deriva.jsonl")
           for o in map(json.loads, open(E.BENCH / f, encoding="utf-8"))}
    return tareas, obj


def entradas_contrato(t: dict) -> tuple[dict, str]:
    if t["dominio"] == "datos":
        datos = (E.BENCH / "datos" / t["entradas"]["csv"]).read_bytes()
        ref = poner(datos)
        return {"ficheros": {"datos.csv": ref}}, ref
    ref = poner(canon(t["entradas"]))
    return {"json": t["entradas"]}, ref


def unidad(t: dict, o: dict, bloques: dict, umbral: float, modo: str) -> dict:
    _ctx.tarea, _ctx.n, _ctx.llamadas = t["id"], 0, []
    b = bloques[t["dominio"]]
    r = resolver(t, o, b, umbral)
    ent, hash_ent = entradas_contrato(t)
    tarea_id, evento_id = ulid("tar"), ulid("evt")
    solicitud = {"schema_version": SV, "proceso": b["contrato"]["proceso"], "clave_idempotencia": t["id"],
                 "politica_texto": R.texto_politica(t), "entradas": ent}
    hash_salida = poner(canon(r["salida"])) if r["salida"] is not None else None
    total = r["coste_gen"] + r["coste_juez"]
    evento = {"schema_version": SV, "evento_id": evento_id, "tarea_id": tarea_id, "ts": ahora(),
              "proceso": b["contrato"]["proceso"], "clave_idempotencia": t["id"], "hash_entradas": hash_ent,
              "hash_politica": "sha256:" + R.sha(R.texto_politica(t)),
              "bloque": {"id": b["contrato"]["id"], "version": "1.0.0", "hash_codigo": b["contrato"]["hash_codigo"]},
              "pasos": r["pasos"], "decision": r["decision"], "coste_total_eur": round(total, 6)}
    if hash_salida:
        evento["hash_salida"] = hash_salida
    resolucion = {"schema_version": SV, "tarea_id": tarea_id, "evento_id": evento_id, "decision": r["decision"],
                  "salida": r["salida"], "evidencia": r["evidencia"],
                  "coste": {"generacion_eur": round(r["coste_gen"], 6), "juez_eur": round(r["coste_juez"], 6),
                            "total_eur": round(total, 6)}}
    if r["usa_bloque"]:
        resolucion["bloque"] = {"id": b["contrato"]["id"], "version": "1.0.0"}
    escalado = None
    if r["escalado"]:
        escalado = {"schema_version": SV, "escalado_id": ulid("esc"), "tarea_id": tarea_id,
                    "motivo": r["escalado"]["motivo"], "salidas": r["escalado"]["salidas"],
                    "perfil_motivos": r["escalado"]["perfil_motivos"],
                    "plazo": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "estado": "abierto"}
        resolucion["escalado_id"] = escalado["escalado_id"]
    for nombre, inst in (("TareaSolicitud", solicitud), ("Evento", evento), ("Resolucion", resolucion)):
        validar(nombre, inst)
    if escalado:
        validar("Escalado", escalado)
    ok_verdad = None if r["salida"] is None else verificar(t, r["salida"], o)[0]
    esperado = {"tarea": t["id"], "dominio": t["dominio"], "nivel": t["nivel"],
                "categoria": t.get("variante", {}).get("tipo") or t.get("deriva", {}).get("tipo") or "normal",
                "decision": r["decision"], "hash_salida": hash_salida, "ok_contra_verdad": ok_verdad,
                "firma_pasos": [(p["tipo"], p["resultado"], p.get("artefacto")) for p in r["pasos"]],
                "coste_total": round(total, 6)}
    return {"solicitud": solicitud | {"_tarea_benchmark": t["id"]}, "evento": evento, "resolucion": resolucion,
            "escalado": escalado, "esperado": esperado, "llamadas": _ctx.llamadas}


def grabar(presupuesto: float, hilos: int) -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    E.llamar = llamar_grabando
    tareas, obj = cargar_tareas()
    bloques = preparar_bloques(tareas)
    with open(CORPUS / "bloques.jsonl", "w", encoding="utf-8") as f:
        for b in bloques.values():
            f.write(json.dumps(b["contrato"], ensure_ascii=False) + "\n")
    umbral = umbral_reutilizacion()
    (CORPUS / "config.json").write_text(json.dumps({"umbral_reutilizacion": umbral, "V_eur": V_EUR,
                                                    "presupuesto_tarea_eur": PRESUPUESTO_TAREA,
                                                    "modelos": E.MODELOS, "esfuerzo": E.ESFUERZO,
                                                    "sandbox": "subproceso (sin Docker en esta sesión)"}, indent=1))
    hechos = set()
    if (CORPUS / "esperado.jsonl").exists():
        hechos = {json.loads(l)["tarea"] for l in open(CORPUS / "esperado.jsonl", encoding="utf-8")}
    pend = [t for t in flujo(tareas) if t["id"] not in hechos]
    print(f"umbral de reutilización (RN3) = {umbral} · {len(pend)} tareas pendientes", flush=True)
    archivos = {k: open(CORPUS / f"{k}.jsonl", "a", encoding="utf-8")
                for k in ("tareas", "eventos", "resoluciones", "escalados", "esperado", "llamadas")}
    try:
        with ThreadPoolExecutor(max_workers=hilos) as ex:
            fut = {ex.submit(unidad, t, obj[t["id"]], bloques, umbral, "grabar"): t["id"] for t in pend}
            for i, f in enumerate(as_completed(fut), 1):
                try:
                    u = f.result()
                except Exception as e:
                    print(f"ERROR {fut[f]}: {e}", flush=True)
                    continue
                with _lock:
                    archivos["tareas"].write(json.dumps(u["solicitud"], ensure_ascii=False) + "\n")
                    archivos["eventos"].write(json.dumps(u["evento"], ensure_ascii=False) + "\n")
                    archivos["resoluciones"].write(json.dumps(u["resolucion"], ensure_ascii=False) + "\n")
                    if u["escalado"]:
                        archivos["escalados"].write(json.dumps(u["escalado"], ensure_ascii=False) + "\n")
                    for ll in u["llamadas"]:
                        archivos["llamadas"].write(json.dumps(ll, ensure_ascii=False) + "\n")
                    archivos["esperado"].write(json.dumps(u["esperado"], ensure_ascii=False) + "\n")
                    for a in archivos.values():
                        a.flush()
                print(f"[{i}/{len(fut)}] {fut[f]} → {u['esperado']['decision']} · gasto ${E._gasto['usd']:.2f}", flush=True)
                if E._gasto["usd"] > presupuesto:
                    print("PRESUPUESTO AGOTADO", flush=True)
                    for g in fut:
                        g.cancel()
    finally:
        for a in archivos.values():
            a.close()


def reproducir(hilos: int) -> dict:
    """Test de replay: misma decisión, misma salida y mismos pasos, sin gastar."""
    for l in open(CORPUS / "llamadas.jsonl", encoding="utf-8"):
        reg = json.loads(l)
        _GRABADAS.setdefault(reg["tarea"], []).append(reg)
    for v in _GRABADAS.values():
        v.sort(key=lambda r: r["n"])
    E.llamar = llamar_reproduciendo
    tareas, obj = cargar_tareas()
    bloques = preparar_bloques(tareas)
    umbral = json.loads((CORPUS / "config.json").read_text())["umbral_reutilizacion"]
    esperado = {e["tarea"]: e for e in map(json.loads, open(CORPUS / "esperado.jsonl", encoding="utf-8"))}
    iguales, distintas = 0, []
    with ThreadPoolExecutor(max_workers=hilos) as ex:
        fut = {ex.submit(unidad, tareas[k], obj[k], bloques, umbral, "reproducir"): k for k in esperado}
        for f in as_completed(fut):
            k = fut[f]
            try:
                u = f.result()["esperado"]
            except Exception as e:
                distintas.append((k, str(e)[:120]))
                continue
            e = esperado[k]
            mismo = (u["decision"], u["hash_salida"], [tuple(x) for x in u["firma_pasos"]]) == \
                    (e["decision"], e["hash_salida"], [tuple(x) for x in e["firma_pasos"]])
            if mismo:
                iguales += 1
            else:
                distintas.append((k, f"{e['decision']}→{u['decision']}"))
    res = {"reproducidas_identicas": iguales, "total": len(esperado), "divergentes": distintas,
           "gasto_llm_usd": E._gasto["usd"]}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["grabar", "reproducir"])
    ap.add_argument("--presupuesto-usd", type=float, default=16.0)
    ap.add_argument("--hilos", type=int, default=8)
    a = ap.parse_args()
    grabar(a.presupuesto_usd, a.hilos) if a.accion == "grabar" else reproducir(a.hilos)
