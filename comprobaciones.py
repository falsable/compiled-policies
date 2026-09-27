"""H3: comprobaciones en runtime SIN respuesta correcta, para reutilizar bloques con deriva de datos.

    python comprobaciones.py compilar_b --presupuesto-usd 2     # segundo bloque independiente (N-versión)
    python comprobaciones.py evaluar    --presupuesto-usd 13
    python comprobaciones.py analizar

Tres comprobaciones (se integran ideas existentes, no se inventan):
  perfil      — estilo TFDV: esquema inferido de las entradas con las que se validó el bloque
                (monedas, formatos de número, meses, departamentos, proveedores, forma de los ids,
                tamaño del lote; separador del CSV, regiones, formatos de celda, columnas no numéricas).
                Lo que se sale del perfil es territorio no probado → no se reutiliza a ciegas.
  invariantes — propiedades estructurales de la salida que exige el contrato.
  n_version   — un segundo bloque generado por separado; si las salidas difieren, al menos una está mal.
Un bloque que revienta (excepción, salida no JSON) se detecta siempre.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import experimento_kappa as E
import reutilizacion as R
from benchmark import extremo as X
from benchmark.verificar import verificar

BENCH = E.BENCH
SALIDA = Path(__file__).parent / "exp" / "deriva"
V_EUR = 2.0
_lock = threading.Lock()


def cargar():
    tareas = {t["id"]: t for f in ("tareas.jsonl", "deriva.jsonl")
              for t in map(json.loads, open(BENCH / f, encoding="utf-8"))}
    obj = {o["id"]: o["objetivo"] for f in ("objetivos.jsonl", "objetivos_deriva.jsonl")
           for o in map(json.loads, open(BENCH / f, encoding="utf-8"))}
    return tareas, obj


def entrenamiento(tareas: dict, dominio: str) -> list[dict]:
    return [tareas[f"{dominio}-extremo-{i:03d}"] for i in range(5)]


def calibracion(dominio: str) -> list[dict]:
    """25 tareas NORMALES extra (semillas 30–54) generadas sin LLM, solo para inferir el perfil.
    Añadido después de ver en seco que el perfil de 5 tareas daba 34 % de falsas alarmas."""
    ruta = BENCH / "calibracion.jsonl"
    if not ruta.exists():
        filas = []
        for s in range(30, 55):
            tn, _ = X.generar_negocio_extremo(s)
            td, _ = X.generar_datos_extremo(s, BENCH / "datos")
            filas += [tn | {"id": f"negocio-calib-{s:03d}"}, td | {"id": f"datos-calib-{s:03d}"}]
        with open(ruta, "w", encoding="utf-8") as f:
            for t in filas:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
    return [t for t in map(json.loads, open(ruta, encoding="utf-8")) if t["dominio"] == dominio]


# =============================================================== perfil (TFDV)

def _forma(s: str) -> str:
    return re.sub(r"\d+", "9", s)


def _forma_id(s: str) -> str:
    return re.sub(r"\d", "9", s)


def _rasgos_negocio(t: dict) -> dict:
    e = t["entradas"]
    formatos, monedas = set(), set()
    for x in e["entradas"]:
        if x["tipo"] != "solicitud":
            continue
        txt = x["importe"]
        sufijo = "+IVA" if "+ IVA" in txt else "incl"
        num = txt.replace("(IVA incluido)", "").replace("+ IVA", "").strip()
        mon = next((m for m in ("USD", "GBP", "EUR", "€") if m in num), "?")
        monedas.add(mon)
        formatos.add((mon, _forma(num.replace(mon, "").strip()), sufijo))
    return {
        "monedas": monedas, "formatos": formatos,
        "meses": {x["fecha"][:7] for x in e["entradas"]},
        "departamentos": set(e["presupuesto_inicial"]) | {x.get("departamento") for x in e["entradas"] if x["tipo"] == "solicitud"},
        "proveedores": {x.get("proveedor") for x in e["entradas"] if x["tipo"] == "solicitud"},
        "homologados": set(e["proveedores_homologados"]),
        "formas_id": {_forma_id(x["id"]) for x in e["entradas"]},
        "claves": {tuple(sorted(x)) for x in e["entradas"]},
        "n": len(e["entradas"]),
    }


def _leer_crudo(ruta: Path) -> tuple[str, pd.DataFrame]:
    primera = ruta.read_text(encoding="utf-8").splitlines()[0]
    try:
        sep = csv.Sniffer().sniff(primera, delimiters=",;").delimiter
    except csv.Error:
        sep = ","
    return sep, pd.read_csv(ruta, sep=sep, dtype=str)


def _rasgos_datos(t: dict) -> dict:
    sep, df = _leer_crudo(BENCH / "datos" / t["entradas"]["csv"])
    formas, no_numericas = set(), 0
    for c in df.columns:
        if c in ("id", "region"):
            continue
        s = df[c].dropna().str.strip()
        plano = pd.to_numeric(s, errors="coerce").notna()
        formas |= {_forma(v) for v in s[~plano].head(500)}
        parseable = pd.to_numeric(s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
                                  errors="coerce").notna() | plano
        no_numericas += parseable.mean() < 0.5
    return {"separador": {sep}, "n_columnas": len(df.columns),
            "obligatorias": {"id", "region"} <= set(df.columns),
            "regiones": set(df["region"].dropna()) if "region" in df else set(),
            "formas_celda": formas, "columnas_no_numericas": no_numericas}


def perfil(tareas_ent: list[dict]) -> dict:
    dom = tareas_ent[0]["dominio"]
    rs = [(_rasgos_negocio if dom == "negocio" else _rasgos_datos)(t) for t in tareas_ent]
    p = {}
    for k in rs[0]:
        v = [r[k] for r in rs]
        if isinstance(v[0], set):
            p[k] = set().union(*v)
        elif isinstance(v[0], bool):
            p[k] = all(v)
        else:
            p[k] = (min(v), max(v))
    return p


def fuera_de_perfil(t: dict, p: dict) -> list[str]:
    r = (_rasgos_negocio if t["dominio"] == "negocio" else _rasgos_datos)(t)
    motivos = []
    for k, v in r.items():
        if isinstance(v, set):
            nuevos = v - p[k]
            if nuevos:
                motivos.append(f"{k}: nuevo {sorted(map(str, nuevos))[:3]}")
        elif isinstance(v, bool):
            if v != p[k]:
                motivos.append(f"{k}: {v}")
        else:
            lo, hi = p[k]
            if v > hi * 1.5 or v < lo / 1.5 or (k == "columnas_no_numericas" and v > hi):
                motivos.append(f"{k}: {v} fuera de [{lo}, {hi}]")
    return motivos


# =============================================================== invariantes de salida

ESTADOS_OK = {
    "aprobada": lambda d: d["aprobador"] in ("auto", "jefe_departamento", "direccion") and d["motivo"] is None,
    "rechazada": lambda d: d["aprobador"] is None and d["motivo"] == "proveedor_no_homologado",
    "escalada": lambda d: (d["aprobador"], d["motivo"]) in (("finanzas", "sin_presupuesto"),
                                                            ("direccion", "excepcion_seguridad")),
    "cancelada": lambda d: d["aprobador"] is None and d["motivo"] == "cancelada_por_solicitante"
                           and d["revision_it"] is False,
}


def invariantes(t: dict, salida: dict) -> list[str]:
    v = []
    try:
        if t["dominio"] == "negocio":
            e = t["entradas"]
            sols = {x["id"]: x for x in e["entradas"] if x["tipo"] == "solicitud"}
            refs = {x["referencia"] for x in e["entradas"] if x["tipo"] == "cancelacion"}
            dec = salida.get("decisiones", {})
            if set(dec) != set(sols):
                v.append("decisiones ≠ solicitudes")
            for sid, d in dec.items():
                if d.get("estado") not in ESTADOS_OK or not ESTADOS_OK[d["estado"]](d):
                    v.append(f"{sid}: combinación inválida"); break
                if not isinstance(d.get("revision_it"), bool):
                    v.append(f"{sid}: revision_it no booleano"); break
            if {k for k, d in dec.items() if d.get("estado") == "cancelada"} != (refs & set(sols)):
                v.append("canceladas ≠ referencias de cancelación")
            homol = set(e["proveedores_homologados"])
            for sid, x in sols.items():
                d = dec.get(sid, {})
                if d.get("estado") == "cancelada":
                    continue
                excepcion = x["categoria"] == "seguridad" and x["urgente"]
                if (x["proveedor"] not in homol and not excepcion) != (d.get("estado") == "rechazada"):
                    v.append(f"{sid}: rechazo incoherente con homologados"); break
            pf, pi = salida.get("presupuesto_final", {}), e["presupuesto_inicial"]
            if set(pf) != set(pi):
                v.append("departamentos del presupuesto ≠")
            elif any(float(pf[k]) > pi[k] + 0.05 or float(pf[k]) < -0.05 for k in pi):
                v.append("presupuesto fuera de [0, inicial]")
        else:
            sep, df = _leer_crudo(BENCH / "datos" / t["entradas"]["csv"])
            par, anom = salida.get("par", []), salida.get("anomalias", [])
            cols = set(df.columns) - {"id", "region"}
            if len(par) != 2 or len(set(par)) != 2 or not set(par) <= cols:
                v.append("par inválido")
            ids = set(df["id"]) if "id" in df else set()
            if not set(anom) <= ids:
                v.append("anomalías con ids inexistentes")
            if len(anom) != len(set(anom)) or anom != sorted(anom):
                v.append("anomalías no únicas u ordenadas")
            if len(anom) > 0.02 * len(df):
                v.append("demasiadas anomalías (>2 % de filas)")
    except Exception as ex:
        v.append(f"salida malformada: {ex}")
    return v


# =============================================================== ejecutar un bloque (salida cruda)

def correr_bloque(t: dict, codigo: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        E.preparar_directorio(t, d)
        (d / "sol.py").write_text(codigo, encoding="utf-8")
        try:
            p = subprocess.run([sys.executable, "sol.py"], cwd=d, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return {"revienta": True, "salida": None, "detalle": "timeout"}
    if p.returncode != 0:
        return {"revienta": True, "salida": None, "detalle": p.stderr.strip()[-200:]}
    try:
        return {"revienta": False, "salida": E._json_de(p.stdout), "detalle": ""}
    except Exception:
        return {"revienta": True, "salida": None, "detalle": "stdout no JSON"}


def mismas_salidas(t: dict, a: dict | None, b: dict | None) -> bool:
    if a is None or b is None:
        return False
    if t["dominio"] == "datos":
        return sorted(a.get("par", [])) == sorted(b.get("par", [])) and \
            sorted(map(str, a.get("anomalias", []))) == sorted(map(str, b.get("anomalias", [])))
    if a.get("decisiones") != b.get("decisiones"):
        return False
    pa, pb = a.get("presupuesto_final", {}), b.get("presupuesto_final", {})
    return set(pa) == set(pb) and all(abs(float(pa[k]) - float(pb[k])) <= 0.05 for k in pa)


# =============================================================== compilar el bloque B

def compilar_b(presupuesto: float) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    tareas, obj = cargar()

    def uno(dom):
        train = entrenamiento(tareas, dom)
        a = json.loads((R.SALIDA / f"bloque_{dom}.json").read_text())
        mejor, coste_total = None, 0.0
        for k in range(1, 5):   # arranca en otra tarea de entrenamiento que el bloque A
            t = train[k % 5]
            pl = E.planificar(t, "caro")
            coste = pl["coste_plan"]
            if pl["gate_ok"]:
                codigo, r = E.escribir_programa(t, pl["plan"])
                coste += r["coste_usd"]
                acc = sum(E.correr_programa(x, codigo, obj[x["id"]])["ok"] for x in train)
                print(f"[{dom}] B candidato {k}: {acc}/5", flush=True)
                if codigo.strip() != a["codigo"].strip() and (mejor is None or acc > mejor["aciertos"]):
                    mejor = {"codigo": codigo, "aciertos": acc}
            coste_total += coste
            if mejor and mejor["aciertos"] == 5:
                break
        b = {"dominio": dom, "codigo": mejor["codigo"], "validacion": f"{mejor['aciertos']}/5",
             "coste_compilacion": coste_total}
        (SALIDA / f"bloque_{dom}_B.json").write_text(json.dumps(b, ensure_ascii=False, indent=1))
        return dom, b["validacion"], coste_total

    with ThreadPoolExecutor(max_workers=2) as ex:
        for dom, val, c in ex.map(uno, R.DOMINIOS):
            print(f"bloque B {dom}: {val}, ${c:.2f}")


# =============================================================== evaluar

def evaluar_tarea(t, o, A, B, perf, previo, perf30=None) -> dict:
    cat = "normal" if t["nivel"] == "extremo" else "deriva"
    fila = {"tarea": t["id"], "dominio": t["dominio"], "categoria": cat,
            "tipo": t.get("deriva", {}).get("tipo", "normal")}
    ra, rb = correr_bloque(t, A["codigo"]), correr_bloque(t, B["codigo"])
    a_ok = (not ra["revienta"]) and verificar(t, ra["salida"], o)[0]
    fila |= {"a_revienta": ra["revienta"], "a_ok": a_ok, "a_detalle": ra["detalle"][:120],
             "b_revienta": rb["revienta"], "b_ok": (not rb["revienta"]) and verificar(t, rb["salida"], o)[0],
             "acuerdo": mismas_salidas(t, ra["salida"], rb["salida"]),
             "perfil": fuera_de_perfil(t, perf),
             "perfil_30": fuera_de_perfil(t, perf30) if perf30 else [],
             "invariantes": [] if ra["revienta"] else invariantes(t, ra["salida"])}
    fila |= previo[t["id"]] if t["id"] in previo else (R.generar_desde_cero(t, o) | {"gen_origen": "nuevo"})
    return fila


def evaluar(presupuesto: float, hilos: int) -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    tareas, obj = cargar()
    previo = R.generar_previo()
    A = {d: json.loads((R.SALIDA / f"bloque_{d}.json").read_text()) for d in R.DOMINIOS}
    B = {d: json.loads((SALIDA / f"bloque_{d}_B.json").read_text()) for d in R.DOMINIOS}
    perf = {d: perfil(entrenamiento(tareas, d)) for d in R.DOMINIOS}
    perf30 = {d: perfil(entrenamiento(tareas, d) + calibracion(d)) for d in R.DOMINIOS}
    flujo = [t for t in tareas.values()
             if (t["nivel"] == "extremo" and int(t["id"][-3:]) >= 5) or t["nivel"] == "deriva"]
    out = SALIDA / "evaluacion.jsonl"
    hechos = {json.loads(l)["tarea"] for l in open(out, encoding="utf-8")} if out.exists() else set()
    pend = [t for t in flujo if t["id"] not in hechos]
    print(f"{len(pend)} pendientes de {len(flujo)}", flush=True)
    with ThreadPoolExecutor(max_workers=hilos) as ex, open(out, "a", encoding="utf-8") as fh:
        fut = {ex.submit(evaluar_tarea, t, obj[t["id"]], A[t["dominio"]], B[t["dominio"]], perf[t["dominio"]],
                         previo, perf30[t["dominio"]]): t["id"] for t in pend}
        for i, f in enumerate(as_completed(fut), 1):
            try:
                fila = f.result()
            except Exception as e:
                print(f"ERROR {fut[f]}: {e}", flush=True)
                continue
            with _lock:
                fh.write(json.dumps(fila, ensure_ascii=False, default=list) + "\n")
                fh.flush()
            print(f"[{i}/{len(fut)}] {fut[f]} gasto ${E._gasto['usd']:.2f}", flush=True)
            if E._gasto["usd"] > presupuesto:
                print("PRESUPUESTO AGOTADO", flush=True)
                for g in fut:
                    g.cancel()


# =============================================================== analizar

def _alarma(f: dict, checks: tuple[str, ...]) -> bool:
    a = f["a_revienta"]   # reventar se detecta siempre
    if "perfil" in checks:
        a = a or bool(f["perfil"])
    if "perfil_30" in checks:
        a = a or bool(f.get("perfil_30"))
    if "invariantes" in checks:
        a = a or bool(f["invariantes"])
    if "n_version" in checks:
        a = a or not f["acuerdo"]
    return a


COMBOS = {
    "reutilizar (solo si revienta)": (),
    "+ invariantes": ("invariantes",),
    "+ N-versión": ("n_version",),
    "+ perfil (5 tareas)": ("perfil",),
    "+ perfil (30 tareas) [post-hoc]": ("perfil_30",),
    "+ invariantes + N-versión": ("invariantes", "n_version"),
    "+ perfil30 + N-versión [post-hoc]": ("perfil_30", "n_version"),
    "+ todo (perfil 5)": ("perfil", "invariantes", "n_version"),
}


def analizar() -> dict:
    df = pd.DataFrame([json.loads(l) for l in open(SALIDA / "evaluacion.jsonl", encoding="utf-8")])
    A = {d: json.loads((R.SALIDA / f"bloque_{d}.json").read_text()) for d in R.DOMINIOS}
    B = {d: json.loads((SALIDA / f"bloque_{d}_B.json").read_text()) for d in R.DOMINIOS}
    n_dom = df.groupby("dominio").size()
    amort = {d: (A[d]["coste_compilacion"] + B[d]["coste_compilacion"]) / n_dom[d] for d in R.DOMINIOS}
    filas, detalle = [], {}
    silenciosas = df[~df.a_ok & ~df.a_revienta]           # respuestas equivocadas del bloque que no revientan
    for nombre, checks in list(COMBOS.items()) + [("generar siempre", None)]:
        if checks is None:
            usa_a = pd.Series(False, index=df.index)
        else:
            usa_a = ~df.apply(lambda f: _alarma(f, checks), axis=1)
        ok = df["a_ok"].where(usa_a, df["gen_ok"]).astype(bool)
        coste = df["gen_coste"].where(~usa_a, 0.0) + (df["dominio"].map(amort) if checks is not None else 0.0)
        total = coste + (~ok) * V_EUR
        res = {"politica": nombre}
        for cat in ("normal", "deriva"):
            m = df.categoria == cat
            res[f"acierto_{cat}_%"] = 100 * ok[m].mean()
            res[f"coste_total_{cat}_$"] = total[m].mean()
        gen_silencioso = ~usa_a & ~df["gen_ok"].astype(bool) & ~df["gen_detalle"].astype(str).str.startswith(
            ("error de ejecución", "timeout", "stdout no"))
        res["fallos_silenciosos"] = int((usa_a & ~df.a_ok).sum() + gen_silencioso.sum())
        if checks is not None:
            al = df.apply(lambda f: _alarma(f, checks), axis=1)
            res["deteccion_silenciosas_deriva_%"] = (100 * al[silenciosas.index][silenciosas.categoria == "deriva"].mean()
                                                     if (silenciosas.categoria == "deriva").any() else float("nan"))
            res["falsas_alarmas_normal_%"] = 100 * (al & df.a_ok)[df.categoria == "normal"].sum() / (df.categoria == "normal").sum()
            res["falsas_alarmas_deriva_%"] = 100 * (al & df.a_ok)[df.categoria == "deriva"].sum() / (df.categoria == "deriva").sum()
        res["coste_total_50_50_$"] = 0.5 * res["coste_total_normal_$"] + 0.5 * res["coste_total_deriva_$"]
        filas.append(res)
    # [post-hoc] perfil30 como disparador + verificación cruzada bloque vs generación nueva:
    #   sin alarma → bloque; alarma → se genera de nuevo y se compara con el bloque:
    #   coinciden → se entrega; difieren → humano (cuesta V, pero no se entrega nada erróneo).
    #   Si uno revienta, se entrega el otro. Con ambos erróneos se usa como indicador de desacuerdo
    #   que su error difiera (en los datos de este experimento: 1 caso, comprobado a mano).
    from benchmark.verificar import verificar as _ver
    T, O = cargar()
    entregado_ok, coste, escalado = [], [], []
    for _, f in df.iterrows():
        am = amort[f["dominio"]]
        if not f.get("perfil_30"):
            if f["a_revienta"]:
                entregado_ok.append(bool(f["gen_ok"])); coste.append(f["gen_coste"] + am); escalado.append(False)
            else:
                entregado_ok.append(bool(f["a_ok"])); coste.append(am); escalado.append(False)
            continue
        gen_revienta = str(f["gen_detalle"]).startswith(("error de ejecución", "timeout", "stdout no"))
        if f["a_revienta"]:
            ok = bool(f["gen_ok"])
            entregado_ok.append(ok); coste.append(f["gen_coste"] + am); escalado.append(False)
        elif gen_revienta:
            entregado_ok.append(bool(f["a_ok"])); coste.append(f["gen_coste"] + am); escalado.append(False)
        else:
            if f["a_ok"] and f["gen_ok"]:
                entregado_ok.append(True); coste.append(f["gen_coste"] + am); escalado.append(False)
            else:
                a_det = _ver(T[f["tarea"]], correr_bloque(T[f["tarea"]], A[f["dominio"]]["codigo"])["salida"], O[f["tarea"]])[1]
                difieren = f["a_ok"] != f["gen_ok"] or a_det != f["gen_detalle"]
                if difieren:
                    entregado_ok.append(None); coste.append(f["gen_coste"] + am); escalado.append(True)
                else:
                    entregado_ok.append(False); coste.append(f["gen_coste"] + am); escalado.append(False)
    ent = pd.Series(entregado_ok, index=df.index, dtype=object)
    esc = pd.Series(escalado, index=df.index)
    coste_s = pd.Series(coste, index=df.index)
    total_s = coste_s + (esc | (ent == False)) * V_EUR   # noqa: E712
    fila = {"politica": "+ perfil30 → verificación cruzada + humano [post-hoc]"}
    for cat in ("normal", "deriva"):
        m = df.categoria == cat
        fila[f"acierto_{cat}_%"] = 100 * (ent[m] == True).mean()   # noqa: E712
        fila[f"coste_total_{cat}_$"] = total_s[m].mean()
    fila["fallos_silenciosos"] = int((ent == False).sum())   # noqa: E712
    fila["escalados_humano"] = int(esc.sum())
    fila["coste_total_50_50_$"] = 0.5 * fila["coste_total_normal_$"] + 0.5 * fila["coste_total_deriva_$"]
    filas.append(fila)
    # entregas erróneas de cada política (lo que llega al usuario mal y sin aviso)
    for r in filas:
        r.setdefault("escalados_humano", 0)
    tabla = pd.DataFrame(filas).set_index("politica")
    gen = tabla.loc["generar siempre", "coste_total_50_50_$"]
    tabla["ahorro_50_50_vs_generar_%"] = 100 * (1 - tabla["coste_total_50_50_$"] / gen)
    # K3a / K3b: alguna combinación que cumpla ambas
    candidatas = tabla.drop(index=["generar siempre", "+ perfil30 → verificación cruzada + humano [post-hoc]"])
    k3a = candidatas[(candidatas["deteccion_silenciosas_deriva_%"].fillna(100) >= 80) &
                     (candidatas["falsas_alarmas_normal_%"] <= 10)]
    k3b = k3a[k3a["ahorro_50_50_vs_generar_%"] >= 30]
    por_tipo = df.groupby("tipo").agg(n=("tarea", "size"), a_ok=("a_ok", "sum"), a_revienta=("a_revienta", "sum"),
                                      b_ok=("b_ok", "sum"), acuerdo=("acuerdo", "sum"),
                                      perfil=("perfil", lambda s: sum(bool(x) for x in s)),
                                      invariantes=("invariantes", lambda s: sum(bool(x) for x in s)),
                                      gen_ok=("gen_ok", "sum"))
    res = {"tabla": tabla.round(3).reset_index().to_dict(orient="records"),
           "por_tipo": por_tipo.reset_index().to_dict(orient="records"),
           "K3a_combinaciones": list(k3a.index), "K3b_combinaciones": list(k3b.index),
           "K3a_supera": len(k3a) > 0, "K3b_supera": len(k3b) > 0,
           "silenciosas_deriva": int((silenciosas.categoria == "deriva").sum()),
           "silenciosas_normal": int((silenciosas.categoria == "normal").sum()),
           "bloques_B": {d: B[d]["validacion"] for d in R.DOMINIOS},
           "gasto_generacion_nueva_$": float(df.loc[df.get("gen_origen") == "nuevo", "gen_coste"].sum())}
    (SALIDA / "resumen.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    print(tabla.round(2).T)
    print(por_tipo)
    print({k: res[k] for k in ("K3a_combinaciones", "K3b_combinaciones", "silenciosas_deriva",
                               "silenciosas_normal", "bloques_B", "gasto_generacion_nueva_$")})
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["compilar_b", "evaluar", "analizar"])
    ap.add_argument("--presupuesto-usd", type=float, default=5.0)
    ap.add_argument("--hilos", type=int, default=8)
    a = ap.parse_args()
    {"compilar_b": lambda: compilar_b(a.presupuesto_usd),
     "evaluar": lambda: evaluar(a.presupuesto_usd, a.hilos),
     "analizar": analizar}[a.accion]()
