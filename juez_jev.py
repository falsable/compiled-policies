"""H2 con Jev como juez de cobertura (README §11).

    python juez_jev.py        # necesita OPENROUTER_API_KEY; gasta < 0,01 $ (40 llamadas a Jev)

Mismas 40 variantes que juzgó Haiku en exp/reutilizacion (las de texto distinto al del bloque), la
pregunta de SISTEMA_JUEZ (reutilizacion.py) como decisión sí/no de Jev y el umbral del router
semántico (reutiliza si p >= 0,8). Solo biblioteca estándar. Escribe en exp/jev_juez/ (carpeta
nueva) y se niega a sobrescribirla; exp/reutilizacion solo se lee.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).parent
BENCH = RAIZ / "benchmark" / "out"
PREVIA = RAIZ / "exp" / "reutilizacion"
SALIDA = RAIZ / "exp" / "jev_juez"
URL = "https://openrouter.ai/api/alpha/decisions"
MODELO = "typesafe/jev-1.13"
UMBRAL = 0.8   # el de POLITICAS["router_semantico"] en reutilizacion.py
PREGUNTA = {"equivalentes": {
    "type": "noul",
    "instructions": "¿Un programa escrito para `politica_a` sirve SIN CAMBIOS para `politica_b`, es decir, "
                    "ambas producen exactamente el mismo resultado para cualquier entrada?",
    "criteria": {
        "true": "Sí: solo cambian la redacción, el orden de exposición o el formato.",
        "false": "No: cambia algún umbral, porcentaje, ventana, categoría, criterio o regla."}}}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def texto_politica(t: dict) -> str:   # igual que en reutilizacion.py
    return t["entradas"]["politica"] if t["dominio"] == "negocio" else t["enunciado"]


def jev(estado: dict, clave: str) -> tuple[float, float, float]:
    cuerpo = json.dumps({"model": MODELO, "state": estado, "questions": PREGUNTA}).encode("utf-8")
    req = urllib.request.Request(URL, data=cuerpo, headers={"Authorization": f"Bearer {clave}",
                                                            "Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8"))
    return float(d["answers"]["equivalentes"]["noul"]), float((d.get("usage") or {}).get("cost") or 0.0), \
        time.perf_counter() - t0


def main() -> None:
    clave = os.environ.get("OPENROUTER_API_KEY")
    if not clave:
        sys.exit("Falta OPENROUTER_API_KEY.")
    if SALIDA.exists() and any(SALIDA.iterdir()):
        sys.exit(f"{SALIDA} ya tiene resultados: una corrida nueva va a otra carpeta.")
    tareas = {t["id"]: t for f in ("tareas.jsonl", "variantes.jsonl")
              for t in map(json.loads, open(BENCH / f, encoding="utf-8"))}
    bloques = {d: json.loads((PREVIA / f"bloque_{d}.json").read_text(encoding="utf-8")) for d in ("datos", "negocio")}
    evaluacion = (PREVIA / "evaluacion.jsonl").read_bytes()
    casos = [f for f in map(json.loads, evaluacion.decode("utf-8").splitlines()) if not f["hash_igual"]]

    def juzgar(f: dict) -> dict:
        p, coste, seg = jev({"politica_a": bloques[f["dominio"]]["contrato_texto"],
                             "politica_b": texto_politica(tareas[f["tarea"]])}, clave)
        return {"tarea": f["tarea"], "dominio": f["dominio"], "categoria": f["categoria"],
                "variante": f["variante"], "equivalente": f["equivalente"], "jev_p": p,
                "jev_reutiliza": p >= UMBRAL, "coste_jev": coste, "segundos": round(seg, 3),
                "haiku_p": f["juez_p"], "haiku_reutiliza": bool(f["juez_equivalentes"] and f["juez_p"] >= UMBRAL),
                "coste_haiku": f["coste_juez"]}

    with ThreadPoolExecutor(4) as pool:
        rs = list(pool.map(juzgar, casos))
    sust = sum(r["jev_reutiliza"] and not r["equivalente"] for r in rs)
    regen = sum(not r["jev_reutiliza"] and r["equivalente"] for r in rs)
    vive = sust == 0 and regen <= 2
    segs = sorted(r["segundos"] for r in rs)
    resumen = {
        "fecha": time.strftime("%Y-%m-%d"), "modelo": MODELO, "umbral": UMBRAL, "casos": len(rs),
        "acierto_%": 100 * sum(r["jev_reutiliza"] == r["equivalente"] for r in rs) / len(rs),
        "reutiliza_sustantivas": sust, "regenera_equivalentes": regen,
        "coste_jev_$": sum(r["coste_jev"] for r in rs), "coste_haiku_$": sum(r["coste_haiku"] for r in rs),
        "latencia_mediana_s": segs[len(segs) // 2],
        "veredicto": "Jev SOBREVIVE como juez" if vive else "Jev MUERE como juez",
        "sha256": {"juez_jev.py": sha(Path(__file__).read_bytes()), "exp/reutilizacion/evaluacion.jsonl": sha(evaluacion)},
    }
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / "resultados.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs), encoding="utf-8")
    (SALIDA / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in sorted(rs, key=lambda r: (r["dominio"], r["categoria"], r["jev_p"])):
        marca = "✓" if r["jev_reutiliza"] == r["equivalente"] else "✗"
        print(f"{marca} {r['dominio']:7} {r['categoria']:10} jev p={r['jev_p']:.2f}  haiku p={r['haiku_p']:.2f}  {r['variante']}")
    print(json.dumps({k: v for k, v in resumen.items() if k != "sha256"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
