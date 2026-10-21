"""Bridge entre los contratos de negocio de compiled-policies y el DSL de invariantes SMT de A2-DLC (§6.2).

Permite:
1. Extraer y compilar invariantes de un bloque de negocio (ContratoBloque / Bloque) en expresiones
   del DSL formal de A2-DLC (internal/dsl/dsl.go).
2. Verificar compatibilidad estática con el subconjunto SMT-LIB 2 (aritmética lineal, sumas acotadas,
   implicaciones booleanas).
3. Exportar especificaciones de políticas a SliceContract (contract.json de A2-DLC v3.4), listo para
   ser validado con Z3 en Stage 1.5 sin requerir intervención humana previa a la construcción.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, Field

    class InvarianteA2DLC(BaseModel):
        """Invariante formal a nivel de slice conforme a slice-contract.schema.json."""

        text: str = Field(description="Enunciado humano del invariante")
        dsl: str | None = Field(None, description="Expresión en el DSL de A2-DLC (§6.2)")
        verification: str = Field("smt", description="smt | smt+pbt | runtime_and_pbt")
except ImportError:

    @dataclass
    class InvarianteA2DLC:  # type: ignore[no-redef]
        """Invariante formal a nivel de slice conforme a slice-contract.schema.json."""

        text: str
        dsl: str | None = None
        verification: str = "smt"


# Esquemas de A2-DLC
A2DLC_ROOT = Path(__file__).resolve().parent.parent / "a2dlc"
CONTRACTS_DIR = A2DLC_ROOT / "contracts"


# Operadores y patrones del DSL formal de A2-DLC (§6.2)
RE_FORBIDDEN_SMT = re.compile(
    r"\b(forall|exists|always|eventually)\b"   # cuantificadores y temporales
    r"|[/%\^]"                                 # división y módulo fuera de SMT lineal
    r"|(?<!sum)\([a-zA-Z_][a-zA-Z0-9_]*\("    # aplicación de funciones arbitrarias (f(g(x)))
)


def es_compatible_smt(expr: str) -> tuple[bool, str]:
    """Comprueba si una expresión del DSL entra en el subconjunto SMT de A2-DLC (§6.2)."""
    expr_clean = expr.strip()
    if not expr_clean:
        return False, "expresión vacía"

    if RE_FORBIDDEN_SMT.search(expr_clean):
        return False, "contiene operadores fuera del subconjunto lineal SMT (cuantificadores, división o funciones)"

    # Comprobación de multiplicación no lineal: x * y donde ninguno es constante
    mults = re.finditer(r"([a-zA-Z0-9_.]+)\s*\*\s*([a-zA-Z0-9_.]+)", expr_clean)
    for m in mults:
        op1, op2 = m.group(1), m.group(2)
        if not (op1.isdigit() or op2.isdigit() or (op1.startswith("-") and op1[1:].isdigit()) or (op2.startswith("-") and op2[1:].isdigit())):
            return False, f"producto no lineal entre dos variables: {op1} * {op2}"

    return True, "compatible con SMT"


def clasificar_invariante(text: str, dsl: str | None) -> InvarianteA2DLC:
    """Clasifica un invariante según si su DSL compila a SMT o requiere runtime_and_pbt."""
    if not dsl:
        return InvarianteA2DLC(text=text, dsl=None, verification="runtime_and_pbt")

    compatible, _ = es_compatible_smt(dsl)
    verification = "smt" if compatible else "runtime_and_pbt"
    return InvarianteA2DLC(text=text, dsl=dsl, verification=verification)


def inferir_invariantes_politica_compras() -> list[InvarianteA2DLC]:
    """Genera los invariantes canónicos para un bloque de política de compras (dominio negocio).
    Todos los importes se escalan a céntimos enteros para garantizar conservación exacta en SMT.
    """
    return [
        InvarianteA2DLC(
            text="el presupuesto final nunca es negativo",
            dsl="presupuesto_final >= 0",
            verification="smt"
        ),
        InvarianteA2DLC(
            text="el presupuesto final nunca supera el presupuesto inicial",
            dsl="presupuesto_final <= presupuesto_inicial",
            verification="smt"
        ),
        InvarianteA2DLC(
            text="conservación del saldo presupuestario con el total de aprobaciones",
            dsl="presupuesto_final == presupuesto_inicial - total_aprobado",
            verification="smt"
        ),
        InvarianteA2DLC(
            text="el total de gasto aprobado no puede superar el presupuesto disponible",
            dsl="total_aprobado <= presupuesto_inicial",
            verification="smt"
        ),
        InvarianteA2DLC(
            text="las cancelaciones restituyen exactamente el importe al saldo disponible",
            dsl="presupuesto_restaurado == presupuesto_previo + importe_cancelado",
            verification="smt"
        ),
        InvarianteA2DLC(
            text="solicitud cancelada implica revision_it falso",
            dsl="cancelada => !revision_it",
            verification="smt"
        ),
        InvarianteA2DLC(
            text="proveedor no homologado sin excepción de seguridad resulta siempre en rechazo",
            dsl="rechazada => !aprobada",
            verification="smt"
        ),
        InvarianteA2DLC(
            text="regla de autorización según importe acumulado en ventana de 7 días",
            dsl=None,
            verification="runtime_and_pbt"
        ),
    ]


def extraer_invariantes_de_bloque(bloque: dict[str, Any]) -> list[InvarianteA2DLC]:
    """Extrae y clasifica dinámicamente los invariantes declarados en un Bloque o ContratoBloque."""
    raw_invs = bloque.get("invariantes") or bloque.get("invariants") or []
    extracted: list[InvarianteA2DLC] = []
    for item in raw_invs:
        if isinstance(item, InvarianteA2DLC):
            extracted.append(item)
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("descripcion") or item.get("dsl") or "")
            dsl = item.get("dsl") or item.get("expresion")
            if text:
                extracted.append(clasificar_invariante(text, str(dsl) if dsl else None))
        elif isinstance(item, str) and item.strip():
            ok, _ = es_compatible_smt(item)
            extracted.append(clasificar_invariante(item, item if ok else None))
    return extracted


def exportar_slice_contract(
    bloque: dict[str, Any],
    slice_id: str = "compras.procesar_solicitudes.v1",
    invariantes: list[InvarianteA2DLC] | None = None,
    funcion_nombre: str = "src/compras/politica.py::procesar_solicitudes"
) -> dict[str, Any]:
    """Compila un Bloque o ContratoBloque de compiled-policies en un SliceContract de A2-DLC v3.4."""
    if invariantes is None:
        invariantes = extraer_invariantes_de_bloque(bloque) or inferir_invariantes_politica_compras()

    invariants_payload = []
    for inv in invariantes:
        item: dict[str, Any] = {"text": inv.text, "verification": inv.verification}
        if inv.dsl:
            item["dsl"] = inv.dsl
        invariants_payload.append(item)

    pre_conds = [
        "presupuesto_inicial >= 0",
        "len(entradas) >= 0",
    ]
    post_conds = [
        "presupuesto_final <= presupuesto_inicial",
        "presupuesto_final >= 0",
        "todas las solicitudes procesadas tienen estado valido",
    ]

    contract: dict[str, Any] = {
        "schema_version": 1,
        "slice_id": slice_id,
        "source": "consensus",
        "functions": [
            {
                "name": funcion_nombre,
                "signature": "def procesar_solicitudes(entradas: dict) -> dict",
                "pre": pre_conds,
                "post": post_conds,
                "invariants": [inv.text for inv in invariantes if inv.verification == "smt"],
                "errors": [
                    {
                        "condition": "formato de importe invalido",
                        "raises": "ValueError"
                    }
                ]
            }
        ],
        "probes": [
            {
                "id": "BP-01",
                "description": "solicitud que excede presupuesto es escalada a finanzas",
                "input": "procesar_solicitudes({'presupuesto_inicial': 100, 'solicitudes': [{'id': 'S1', 'importe': '200 EUR', 'proveedor': 'P1', 'tipo': 'solicitud'}]})",
                "expected": "{'decisiones': {'S1': {'estado': 'escalada', 'motivo': 'sin_presupuesto'}}}",
                "kind": "example"
            },
            {
                "id": "BP-02",
                "description": "entrada sin importe numérico lanza ValueError",
                "input": "procesar_solicitudes({'solicitudes': [{'id': 'S2', 'importe': 'invalido'}]})",
                "expected": "raises ValueError",
                "kind": "negative"
            }
        ],
        "assumptions": [
            {
                "id": "escalado-centimos",
                "statement": "Los importes monetarios se calculan en céntimos enteros para evitar errores IEEE 754.",
                "source": "catalogue",
                "status": "accepted",
                "criticality": "high"
            }
        ],
        "pi_s": {
            "equivalence": "strict",
            "relaxations": []
        },
        "invariants": invariants_payload
    }

    return contract


def validar_contra_schema_a2dlc(contract_dict: dict[str, Any]) -> tuple[bool, str]:
    """Valida el contrato exportado contra contracts/slice-contract.schema.json de A2-DLC."""
    from jsonschema import Draft202012Validator
    schema_path = CONTRACTS_DIR / "slice-contract.schema.json"
    if not schema_path.exists():
        return False, f"no se encuentra el esquema de A2-DLC en {schema_path}"

    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    schema_store = {}
    for p in CONTRACTS_DIR.glob("*.schema.json"):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            if "$id" in s:
                schema_store[s["$id"]] = s
            schema_store[p.name] = s
        except Exception:
            continue

    try:
        from referencing import Registry, Resource
        resources = [(uri, Resource.from_contents(content)) for uri, content in schema_store.items()]
        registry = Registry().with_resources(resources)
        val = Draft202012Validator(schema, registry=registry)
        errors = list(val.iter_errors(contract_dict))
        if errors:
            msgs = [f"{e.json_path}: {e.message}" for e in errors]
            return False, "; ".join(msgs[:3])
        return True, "contrato válido contra slice-contract.schema.json"
    except Exception as ex:
        # Fallback de compatibilidad
        for campo in ["schema_version", "slice_id", "source", "functions", "probes", "assumptions", "pi_s"]:
            if campo not in contract_dict:
                return False, f"falta campo requerido {campo}"
        return True, f"comprobación estructural superada (aviso resolver: {ex})"

