"""Contrato de éxito de un workflow — versión de referencia (Python).

Un workflow tiene éxito cuando su ESTADO FINAL cumple la postcondición, y eso lo
comprueba un verificador fijado ANTES de ejecutar que el ejecutor no puede tocar.
El éxito es un estado, no un texto (mismo criterio que τ-bench).

Objetivo que optimiza la política:   R = V · 1[éxito] − coste_eur
Métrica principal del framework:      coste total por tarea = E[coste] + (1 − P(éxito)) · V
                                      (lo gastado + lo que cuesta la alternativa cuando falla;
                                       es exactamente V − valor neto)
Fiabilidad:                           pass^k = E_tarea[ P(éxito | tarea)^k ]

Nota: € por tarea resuelta (E[coste]/P) se propuso primero como métrica principal y la
simulación la descartó: premia políticas baratas que fallan mucho, porque no cobra la
alternativa. Se mantiene solo como métrica secundaria.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Sequence

from pydantic import BaseModel, Field, model_validator


class TipoVerificador(str, Enum):
    DETERMINISTA = "determinista"   # schema, reglas, invariantes, cuadres, tests
    ESTADISTICO = "estadistico"     # clasificador calibrado: P(correcto) >= umbral
    HUMANO = "humano"               # aprobación; caro, calibra a los otros dos


class Verificador(BaseModel):
    """Comprueba si un estado final cumple la postcondición."""
    id: str
    tipo: TipoVerificador
    coste_eur: float = Field(ge=0)
    tpr: float = Field(1.0, ge=0, le=1, description="P(aprueba | estado correcto)")
    fpr: float = Field(0.0, ge=0, le=1, description="P(aprueba | estado incorrecto)")
    umbral: float | None = Field(None, ge=0, le=1, description="Solo estadístico")
    congelado: bool = Field(True, description="Fijado antes de ejecutar; el ejecutor no lo modifica")

    @model_validator(mode="after")
    def _coherencia(self) -> "Verificador":
        if self.tipo is TipoVerificador.ESTADISTICO and self.umbral is None:
            raise ValueError(f"{self.id}: un verificador estadístico necesita umbral")
        if self.tipo is TipoVerificador.DETERMINISTA and (self.tpr, self.fpr) != (1.0, 0.0):
            raise ValueError(f"{self.id}: un verificador determinista tiene tpr=1, fpr=0")
        if not self.congelado:
            raise ValueError(f"{self.id}: un verificador no congelado se puede manipular")
        return self


class ContratoBloque(BaseModel):
    """Qué recibe y qué entrega un bloque. Solo se conectan bloques cuyos tipos encajan."""
    id: str
    entrada: dict[str, Any] = Field(description="JSON Schema de la entrada")
    salida: dict[str, Any] = Field(description="JSON Schema de la salida")
    pre: list[str] = Field(default_factory=list, description="Precondiciones (predicados con nombre)")
    post: list[str] = Field(default_factory=list, description="Postcondiciones (predicados con nombre)")
    tier: str = Field(description="determinista | barato | medio | caro | humano")
    coste_estimado_eur: float = Field(ge=0)

    def puede_seguir_a(self, anterior: "ContratoBloque") -> bool:
        """Compatibilidad mínima: lo que exige mi entrada lo produce la salida anterior."""
        requeridos = set(self.entrada.get("required", []))
        producidos = set(anterior.salida.get("properties", {}).keys())
        return requeridos <= producidos


class ContratoExito(BaseModel):
    """Contrato de éxito de un workflow. Se fija antes de la primera ejecución."""
    workflow: str
    estado_objetivo: dict[str, Any] = Field(description="JSON Schema del estado final esperado")
    verificadores: list[Verificador] = Field(min_length=1, description="Del más barato al más caro")
    p_min: float = Field(ge=0, le=1, description="P(éxito) mínima aceptable")
    V_eur: float = Field(gt=0, description="Valor de resolver = coste de la alternativa (humano / modelo caro)")
    k_pass: int = Field(5, ge=1, description="k para pass^k")

    @model_validator(mode="after")
    def _escalera(self) -> "ContratoExito":
        costes = [v.coste_eur for v in self.verificadores]
        if costes != sorted(costes):
            raise ValueError("los verificadores van del más barato al más caro")
        return self

    @property
    def lambda_(self) -> float:
        """λ = 1/V: cuánto P(éxito) vale un euro. Deja de ser un número inventado."""
        return 1.0 / self.V_eur


class Evento(BaseModel):
    """Cada decisión es un evento: con el log se puede re-ejecutar igual (replay)."""
    paso: int
    bloque: str
    tier: str
    prob_decision: float | None = None   # probabilidad que dio el selector, si la hubo
    resultado: str                       # ok | fallo | escalado
    coste_eur: float = Field(ge=0)


class RegistroEjecucion(BaseModel):
    tarea_id: str
    workflow: str
    intento: int = 0
    exito: bool
    coste_eur: float = Field(ge=0)
    eventos: list[Evento] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coste_cuadra(self) -> "RegistroEjecucion":
        if self.eventos:
            suma = sum(e.coste_eur for e in self.eventos)
            if abs(suma - self.coste_eur) > 1e-9:
                raise ValueError(f"coste_eur={self.coste_eur} no cuadra con eventos={suma}")
        return self


# ---------------------------------------------------------------- métricas

def coste_total_por_tarea(registros: Sequence[RegistroEjecucion], V_eur: float) -> float:
    """Métrica principal: lo gastado + V por cada tarea que acaba en la alternativa."""
    n = len(registros)
    return sum(r.coste_eur + (0.0 if r.exito else V_eur) for r in registros) / n


def eur_por_resuelta(registros: Sequence[RegistroEjecucion]) -> float:
    """Secundaria. E[coste]/P(éxito): no cobra la alternativa, así que favorece fallar barato."""
    exitos = sum(r.exito for r in registros)
    if exitos == 0:
        return float("inf")
    return sum(r.coste_eur for r in registros) / exitos


def valor_neto(registros: Sequence[RegistroEjecucion], V_eur: float) -> float:
    """E[V·1[éxito] − coste] por ejecución: lo que optimiza la política."""
    n = len(registros)
    return sum(V_eur * r.exito - r.coste_eur for r in registros) / n


def pass_hat_k(registros: Sequence[RegistroEjecucion], k: int) -> float:
    """Fracción de tareas en las que las k primeras ejecuciones tienen éxito todas."""
    por_tarea: dict[str, list[bool]] = {}
    for r in sorted(registros, key=lambda r: (r.tarea_id, r.intento)):
        por_tarea.setdefault(r.tarea_id, []).append(r.exito)
    evaluables = [v[:k] for v in por_tarea.values() if len(v) >= k]
    if not evaluables:
        raise ValueError(f"ninguna tarea tiene {k} ejecuciones")
    return sum(all(v) for v in evaluables) / len(evaluables)


def cumple_contrato(registros: Sequence[RegistroEjecucion], contrato: ContratoExito) -> dict[str, Any]:
    p = sum(r.exito for r in registros) / len(registros)
    return {
        "p_exito": p,
        "cumple_p_min": p >= contrato.p_min,
        "coste_total_por_tarea": coste_total_por_tarea(registros, contrato.V_eur),
        "eur_por_resuelta": eur_por_resuelta(registros),
        "valor_neto": valor_neto(registros, contrato.V_eur),
        f"pass^{contrato.k_pass}": pass_hat_k(registros, contrato.k_pass),
    }
