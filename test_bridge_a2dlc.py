"""Tests para bridge_a2dlc.py (Contratos de compiled-policies <-> DSL de invariantes A2-DLC)."""
from __future__ import annotations

import json
from pathlib import Path

from bridge_a2dlc import (
    es_compatible_smt,
    inferir_invariantes_politica_compras,
    exportar_slice_contract,
    validar_contra_schema_a2dlc,
)

RAIZ = Path(__file__).resolve().parent


def test_es_compatible_smt():
    # Expresiones válidas (§6.2 subset)
    validas = [
        "presupuesto_final >= 0",
        "presupuesto_final <= presupuesto_inicial",
        "presupuesto_final == presupuesto_inicial - total_aprobado",
        "sum(i in 0..10 : items[i].cost) <= limit",
        "cancelada => !revision_it",
        "2 * x - y <= 10",
        "balance.after == balance.before - amount",
        "!frozen || balance >= 0",
    ]
    for expr in validas:
        ok, msg = es_compatible_smt(expr)
        assert ok, f"esperada válida pero falló: {expr} ({msg})"

    # Expresiones fuera del subconjunto (deben ser rechazadas de SMT)
    invalidas = [
        ("x * y > 0", "producto no lineal"),
        ("x / 2 == 1", "división"),
        ("forall i in xs : i > 0", "cuantificador"),
        ("always balance >= 0", "operador temporal"),
        ("", "vacía"),
    ]
    for expr, motivo in invalidas:
        ok, msg = es_compatible_smt(expr)
        assert not ok, f"esperada inválida para SMT pero fue aceptada: {expr} ({motivo})"


def test_inferir_invariantes_politica_compras():
    invs = inferir_invariantes_politica_compras()
    assert len(invs) >= 6
    smt_invs = [inv for inv in invs if inv.verification == "smt"]
    runtime_invs = [inv for inv in invs if inv.verification == "runtime_and_pbt"]
    assert len(smt_invs) >= 5, "debe haber al menos 5 invariantes SMT lineales"
    assert len(runtime_invs) >= 1, "debe haber al menos 1 invariante runtime_and_pbt"


def test_exportar_y_validar_slice_contract():
    bloque_path = RAIZ / "exp" / "reutilizacion" / "bloque_negocio.json"
    assert bloque_path.exists(), "bloque_negocio.json debe existir"
    bloque = json.loads(bloque_path.read_text(encoding="utf-8"))

    contract = exportar_slice_contract(
        bloque=bloque,
        slice_id="compras.procesar_solicitudes.v1",
        funcion_nombre="src/compras/politica.py::procesar_solicitudes"
    )

    # Comprobaciones de forma según protocolo §6.1
    assert contract["schema_version"] == 1
    assert contract["slice_id"] == "compras.procesar_solicitudes.v1"
    assert contract["source"] == "consensus"
    assert len(contract["functions"]) == 1
    fn = contract["functions"][0]
    assert fn["name"] == "src/compras/politica.py::procesar_solicitudes"
    assert len(fn["pre"]) >= 1
    assert len(fn["post"]) >= 1
    assert len(contract["invariants"]) >= 6

    # Validación contra el esquema de A2-DLC
    ok, msg = validar_contra_schema_a2dlc(contract)
    assert ok, f"el contrato exportado no valida contra slice-contract.schema.json: {msg}"


def test_extraer_invariantes_dinamicos_de_bloque():
    bloque_custom = {
        "id": "tesoreria.transferencia.v1",
        "invariantes": [
            {"text": "saldo origen no negativo", "dsl": "saldo_origen_final >= 0"},
            {"text": "conservacion transferencia", "dsl": "saldo_origen_final + monto == saldo_origen_inicial"},
            {"text": "auditoria externa manual para montos altos", "dsl": "forall tx in lote : tx.ok"},
        ],
    }
    contract = exportar_slice_contract(
        bloque=bloque_custom,
        slice_id="tesoreria.transferencia.v1",
        funcion_nombre="src/tesoreria/pagos.py::ejecutar_transferencia",
    )
    assert len(contract["invariants"]) == 3
    assert contract["invariants"][0]["verification"] == "smt"
    assert contract["invariants"][1]["verification"] == "smt"
    assert contract["invariants"][2]["verification"] == "runtime_and_pbt"
    ok, msg = validar_contra_schema_a2dlc(contract)
    assert ok, f"contrato dinámico inválido: {msg}"


def main():
    test_es_compatible_smt()
    print("ok   test_es_compatible_smt")
    test_inferir_invariantes_politica_compras()
    print("ok   test_inferir_invariantes_politica_compras")
    test_exportar_y_validar_slice_contract()
    print("ok   test_exportar_y_validar_slice_contract")
    test_extraer_invariantes_dinamicos_de_bloque()
    print("ok   test_extraer_invariantes_dinamicos_de_bloque")
    print("4/4 tests de bridge_a2dlc pasados con éxito")


if __name__ == "__main__":
    main()

