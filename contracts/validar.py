"""Contract tests del esquema v1 (ADR-001-API, AVAP · TDD).

1. El esquema es válido contra el metaesquema 2020-12.
2. Ejemplos POSITIVOS construidos con datos reales de los experimentos → válidos.
3. Ejemplos NEGATIVOS, uno por regla de negocio → inválidos.

Uso: python contracts/validar.py   (sale con código ≠ 0 si algo falla; va en CI)
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

RAIZ = Path(__file__).resolve().parent
ESQUEMA = json.loads((RAIZ / "v1" / "bloques.schema.json").read_text())
Draft202012Validator.check_schema(ESQUEMA)
REG = ESQUEMA["$defs"]


def validador(nombre: str) -> Draft202012Validator:
    sub = {"$schema": ESQUEMA["$schema"], "$defs": REG, "$ref": f"#/$defs/{nombre}"}
    return Draft202012Validator(sub, format_checker=FormatChecker())


def h(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode()).hexdigest()


ULID = "01JAZ0000000000000000000{:02d}"
V = "1.0.0"
proyecto = RAIZ.parent
bloque_neg = json.loads((proyecto / "exp" / "reutilizacion" / "bloque_negocio.json").read_text())

contrato = {
    "schema_version": V, "proceso": "negocio-compras",
    "estado_objetivo": {"type": "object", "required": ["decisiones", "presupuesto_final"]},
    "verificadores": [
        {"id": "invariantes", "tipo": "determinista", "coste_eur": 0.0, "tpr": 1, "fpr": 0, "congelado": True},
        {"id": "juez_equivalencia", "tipo": "estadistico", "coste_eur": 0.014, "tpr": 0.97, "fpr": 0.05,
         "umbral": 0.83, "congelado": True},
        {"id": "revisor", "tipo": "humano", "coste_eur": 2.0, "congelado": True},
    ],
    "p_min": 0.9, "V_eur": 2.0, "k_pass": 5, "presupuesto_tarea_eur": 1.0,
}
bloque = {
    "schema_version": V, "id": "blq_" + ULID.format(1), "proceso": "negocio-compras", "version": "1.0.0",
    "hash_codigo": h(bloque_neg["codigo"]), "politica_hash": "sha256:" + bloque_neg["hash"],
    "politica_texto": bloque_neg["contrato_texto"],
    "dominio_validez": {"n_ejemplos": 30,
                        "conjuntos": {"monedas": ["EUR", "USD", "GBP", "€"], "meses": ["2026-09"],
                                      "formas_id": ["SC-999-99", "CX-999-9"]},
                        "rangos": {"n": [18, 18]}, "tolerancia_rango": 1.5},
    "validacion": {"aprobados": 5, "total": 5}, "coste_compilacion_eur": round(bloque_neg["coste_compilacion"], 4),
    "estado": "aprobado", "aprobado_por": "operador",
}
tarea = {"schema_version": V, "proceso": "negocio-compras", "clave_idempotencia": "negocio-extremo-005",
         "politica_texto": bloque_neg["contrato_texto"], "entradas": {"json": {"entradas": []}}}
res_reuso = {"schema_version": V, "tarea_id": "tar_" + ULID.format(2), "evento_id": "evt_" + ULID.format(3),
             "decision": "reutilizar", "salida": {"decisiones": {}, "presupuesto_final": {}},
             "bloque": {"id": bloque["id"], "version": "1.0.0"},
             "evidencia": {"hash_igual": True, "perfil_motivos": [], "invariantes_violados": []},
             "coste": {"generacion_eur": 0.0, "juez_eur": 0.0, "total_eur": 0.004}}
res_cruzada = res_reuso | {"decision": "verificacion_cruzada",
                           "evidencia": {"hash_igual": True, "perfil_motivos": ["meses: nuevo ['2026-10']"], "acuerdo": True},
                           "coste": {"generacion_eur": 0.21, "juez_eur": 0.0, "total_eur": 0.214}}
res_escalar = res_reuso | {"decision": "escalar", "salida": None, "escalado_id": "esc_" + ULID.format(4),
                           "evidencia": {"hash_igual": True, "perfil_motivos": ["formas_celda: nuevo ['9.9,9']"], "acuerdo": False},
                           "coste": {"generacion_eur": 0.13, "juez_eur": 0.0, "total_eur": 2.134}}
evento = {"schema_version": V, "evento_id": "evt_" + ULID.format(3), "tarea_id": "tar_" + ULID.format(2),
          "ts": "2026-09-27T10:00:00Z", "proceso": "negocio-compras", "clave_idempotencia": "negocio-extremo-005",
          "hash_entradas": h("entradas"), "hash_politica": "sha256:" + bloque_neg["hash"],
          "bloque": {"id": bloque["id"], "version": "1.0.0", "hash_codigo": bloque["hash_codigo"]},
          "pasos": [{"tipo": "hash", "resultado": "igual", "coste_eur": 0.0},
                    {"tipo": "perfil", "resultado": "dentro", "coste_eur": 0.0},
                    {"tipo": "ejecutar_bloque", "resultado": "ok", "coste_eur": 0.0, "artefacto": h("salida")},
                    {"tipo": "invariantes", "resultado": "ok", "coste_eur": 0.0}],
          "decision": "reutilizar", "hash_salida": h("salida"), "coste_total_eur": 0.004}
escalado = {"schema_version": V, "escalado_id": "esc_" + ULID.format(4), "tarea_id": "tar_" + ULID.format(5),
            "motivo": "desacuerdo_verificacion_cruzada",
            "salidas": [{"origen": "bloque", "hash_salida": h("a")}, {"origen": "nueva", "hash_salida": h("b")}],
            "perfil_motivos": ["formas_celda: nuevo ['9.9,9']"], "plazo": "2026-09-28T10:00:00Z", "estado": "abierto"}
error = {"codigo": "SIN_VERIFICADOR", "mensaje": "El contrato no tiene verificadores", "reintentable": False}

POSITIVOS = {"ContratoExito": [contrato], "Bloque": [bloque], "TareaSolicitud": [tarea],
             "Resolucion": [res_reuso, res_cruzada, res_escalar], "Evento": [evento], "Escalado": [escalado],
             "Error": [error]}


def mut(obj, **cambios):
    o = copy.deepcopy(obj)
    for k, v in cambios.items():
        if v is ...:
            o.pop(k, None)
        else:
            o[k] = v
    return o


NEGATIVOS = {  # (esquema, instancia, regla que protege)
    "sin verificadores → SIN_VERIFICADOR": ("ContratoExito", mut(contrato, verificadores=[])),
    "verificador no congelado (RN1)": ("ContratoExito", mut(contrato, verificadores=[
        mut(contrato["verificadores"][0], congelado=False)])),
    "estadístico sin umbral": ("ContratoExito", mut(contrato, verificadores=[
        mut(contrato["verificadores"][1], umbral=...)])),
    "determinista con fpr > 0": ("ContratoExito", mut(contrato, verificadores=[
        mut(contrato["verificadores"][0], fpr=0.1)])),
    "V = 0 (RN2)": ("ContratoExito", mut(contrato, V_eur=0)),
    "perfil con 5 ejemplos (RN6)": ("Bloque", mut(bloque, dominio_validez=mut(bloque["dominio_validez"], n_ejemplos=5))),
    "validación con 3 ejemplos (RN6)": ("Bloque", mut(bloque, validacion={"aprobados": 3, "total": 3})),
    "aprobado sin aprobador (RN6)": ("Bloque", mut(bloque, aprobado_por=...)),
    "escalar entregando salida (RN8)": ("Resolucion", mut(res_escalar, salida={"x": 1})),
    "escalar sin escalado_id": ("Resolucion", mut(res_escalar, escalado_id=...)),
    "reutilizar sin bloque": ("Resolucion", mut(res_reuso, bloque=...)),
    "evento sin pasos (RN9)": ("Evento", mut(evento, pasos=[])),
    "evento sin hash de entradas (RN9)": ("Evento", mut(evento, hash_entradas=...)),
    "escalado resuelto sin veredicto": ("Escalado", mut(escalado, estado="resuelto")),
    "tarea sin clave de idempotencia": ("TareaSolicitud", mut(tarea, clave_idempotencia=...)),
    "versión de esquema 2.x en v1": ("TareaSolicitud", mut(tarea, schema_version="2.0.0")),
}


def main() -> int:
    fallos = 0
    ejemplos = RAIZ / "v1" / "ejemplos"
    ejemplos.mkdir(exist_ok=True)
    for nombre, lista in POSITIVOS.items():
        for i, inst in enumerate(lista):
            errs = list(validador(nombre).iter_errors(inst))
            (ejemplos / f"{nombre}_{i}.json").write_text(json.dumps(inst, ensure_ascii=False, indent=1))
            if errs:
                fallos += 1
                print(f"✗ positivo {nombre}[{i}]: {errs[0].message}")
    for regla, (nombre, inst) in NEGATIVOS.items():
        if validador(nombre).is_valid(inst):
            fallos += 1
            print(f"✗ negativo aceptado: {regla}")
    n_pos = sum(len(v) for v in POSITIVOS.values())
    print(f"metaesquema ok · positivos {n_pos - min(fallos, n_pos)}/{n_pos} · "
          f"negativos rechazados {len(NEGATIVOS) - fallos if fallos <= len(NEGATIVOS) else 0}/{len(NEGATIVOS)} · fallos {fallos}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
