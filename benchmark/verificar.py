"""Verificadores deterministas del benchmark + autotest.

verificar(tarea, estado_final, objetivo) -> (ok, detalle)
El verificador comprueba primero que el objetivo no fue alterado (hash congelado).

Autotest:  python -m benchmark.verificar benchmark/out
  - el objetivo de cada tarea debe pasar su verificador;
  - cada perturbación del objetivo debe fallar;
  - regenerar con las mismas semillas debe dar los mismos hashes (determinismo).
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

from benchmark.generar import _hash, generar

TOL_EUR = 0.05   # diferencias de redondeo acumuladas; +1 € sigue fallando


def _verificar_datos(estado: dict, objetivo: dict) -> tuple[bool, str]:
    par = sorted(estado.get("par", []))
    anom = sorted(estado.get("anomalias", []))
    if par != objetivo["par"]:
        return False, f"par {par} ≠ {objetivo['par']}"
    faltan = set(objetivo["anomalias"]) - set(anom)
    sobran = set(anom) - set(objetivo["anomalias"])
    if faltan or sobran:
        return False, f"anomalías: faltan {sorted(faltan)}, sobran {sorted(sobran)}"
    return True, "ok"


def _verificar_negocio(estado: dict, objetivo: dict) -> tuple[bool, str]:
    dec, dec_obj = estado.get("decisiones", {}), objetivo["decisiones"]
    if set(dec) != set(dec_obj):
        return False, f"ids distintos: {sorted(set(dec) ^ set(dec_obj))}"
    for sid, esperado in dec_obj.items():
        for campo in ("estado", "aprobador", "motivo", "revision_it"):
            if dec[sid].get(campo) != esperado[campo]:
                return False, f"{sid}.{campo} = {dec[sid].get(campo)!r} ≠ {esperado[campo]!r}"
    pres, pres_obj = estado.get("presupuesto_final", {}), objetivo["presupuesto_final"]
    for dep, v in pres_obj.items():
        if dep not in pres or abs(float(pres[dep]) - v) > TOL_EUR:
            return False, f"presupuesto {dep} = {pres.get(dep)} ≠ {v}"
    return True, "ok"


VERIFICADORES = {"datos_exacto": _verificar_datos, "negocio_estado": _verificar_negocio}


def verificar(tarea: dict, estado_final: dict, objetivo: dict) -> tuple[bool, str]:
    if _hash(objetivo) != tarea["hash_objetivo"]:
        raise ValueError(f"{tarea['id']}: el objetivo no coincide con el hash congelado")
    return VERIFICADORES[tarea["verificador"]["id"]](estado_final, objetivo)


def _perturbaciones(tarea: dict, objetivo: dict) -> list[tuple[str, dict]]:
    out = []
    if tarea["dominio"] == "datos":
        o = copy.deepcopy(objetivo); o["anomalias"] = o["anomalias"][1:]; out.append(("falta anomalía", o))
        o = copy.deepcopy(objetivo); o["anomalias"] = o["anomalias"] + ["r99999"]; out.append(("sobra anomalía", o))
        o = copy.deepcopy(objetivo); o["par"] = [o["par"][0], "otra_col"]; out.append(("par cambiado", o))
    else:
        sid = next(iter(objetivo["decisiones"]))
        o = copy.deepcopy(objetivo)
        o["decisiones"][sid]["estado"] = "aprobada" if o["decisiones"][sid]["estado"] != "aprobada" else "rechazada"
        out.append(("estado cambiado", o))
        o = copy.deepcopy(objetivo)
        o["decisiones"][sid]["revision_it"] = not o["decisiones"][sid]["revision_it"]
        out.append(("revision_it cambiada", o))
        o = copy.deepcopy(objetivo)
        dep = next(iter(o["presupuesto_final"]))
        o["presupuesto_final"][dep] += 1.0
        out.append(("presupuesto +1 €", o))
    return out


def _importe_visible(texto: str) -> float:
    """Parsea el importe tal como lo ve el agente ('1.234,56 € + IVA', '413.23 (IVA incluido)')."""
    base = texto.replace("€", "").replace("EUR", "").replace("+ IVA", "").replace("(IVA incluido)", "").strip()
    if "," in base:                      # formato español
        base = base.replace(".", "").replace(",", ".")
    x = float(base)
    return round(x * 1.21, 2) if "+ IVA" in texto else x


def resolver_desde_visible(tarea: dict) -> dict:
    """Resuelve la tarea usando SOLO lo que ve el agente. Si no coincide con el objetivo,
    la tarea no es resoluble (falta información) y el benchmark no es justo."""
    from benchmark import generar as G
    from benchmark import extremo as X
    e = tarea["entradas"]
    if tarea["nivel"] == "deriva":
        from benchmark import deriva as D
        from benchmark import variantes as V
        if tarea["dominio"] == "datos":
            return V.verdad_dat(D.leer_csv_visible(Path(tarea["_dir"]) / "datos" / e["csv"]), V.BASE_DAT)
        return V.resolver_neg(e, V.BASE_NEG)
    if tarea["nivel"] == "variante":
        from benchmark import variantes as V
        if tarea["dominio"] == "datos":
            import pandas as pd
            return V.verdad_dat(pd.read_csv(Path(tarea["_dir"]) / "datos" / e["csv"]), tarea["_params"])
        return V.resolver_neg(e, tarea["_params"])
    if tarea["nivel"] == "extremo":
        if tarea["dominio"] == "datos":
            import pandas as pd
            par, anom, _ = X.verdad_datos_extremo(pd.read_csv(Path(tarea["_dir"]) / "datos" / e["csv"]))
            return {"par": par, "anomalias": anom}
        return X.resolver_negocio_extremo(e)
    if tarea["dominio"] == "datos":
        import pandas as pd
        df = pd.read_csv(Path(tarea["_dir"]) / "datos" / e["csv"])
        par, anom, _ = G._verdad_datos(df)
        return {"par": par, "anomalias": anom}
    homologados = e["proveedores_homologados"]
    solicitudes = [s | {"importe_con_iva": _importe_visible(s["importe"])} for s in e["solicitudes"]]
    original = G.PROVEEDORES_HOMOLOGADOS
    try:   # el motor de referencia usa la lista VISIBLE, no la interna
        G.PROVEEDORES_HOMOLOGADOS = homologados
        return G.resolver_negocio(solicitudes, e["presupuesto_inicial"])
    finally:
        G.PROVEEDORES_HOMOLOGADOS = original


def autotest(dir_out: Path) -> dict:
    tareas = [json.loads(l) for l in open(dir_out / "tareas.jsonl", encoding="utf-8")]
    objetivos = {o["id"]: o["objetivo"] for o in map(json.loads, open(dir_out / "objetivos.jsonl", encoding="utf-8"))}
    n_ok = n_pert = 0
    for t in tareas:
        ok, det = verificar(t, objetivos[t["id"]], objetivos[t["id"]])
        assert ok, f"{t['id']}: el objetivo no pasa su propio verificador ({det})"
        # resoluble: con SOLO la información visible se llega al objetivo
        ok, det = verificar(t, resolver_desde_visible(t | {"_dir": str(dir_out)}), objetivos[t["id"]])
        assert ok, f"{t['id']}: no es resoluble con la información visible ({det})"
        n_ok += 1
        for nombre, estado in _perturbaciones(t, objetivos[t["id"]]):
            ok, _ = verificar(t, estado, objetivos[t["id"]])
            assert not ok, f"{t['id']}: la perturbación '{nombre}' pasó el verificador"
            n_pert += 1
    # determinismo: regenerar con las mismas semillas → mismos hashes
    por_nivel = sum(1 for t in tareas if t["nivel"] == "facil" and t["dominio"] == "datos")
    n_ext = sum(1 for t in tareas if t["nivel"] == "extremo" and t["dominio"] == "datos")
    with tempfile.TemporaryDirectory() as tmp:
        tareas2, _ = generar(por_nivel, Path(tmp), n_ext)
    iguales = [a["hash_objetivo"] == b["hash_objetivo"] for a, b in zip(tareas, tareas2)]
    assert all(iguales) and len(tareas) == len(tareas2), "regenerar no reproduce los mismos objetivos"
    return {"tareas": n_ok, "perturbaciones_rechazadas": n_pert, "reproducible": True}


if __name__ == "__main__":
    print(autotest(Path(sys.argv[1] if len(sys.argv) > 1 else "benchmark/out")))
