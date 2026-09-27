"""H5 · caso negativo (README §12.1): ante reglas que el esquema no expresa, el extractor debe decir «no».

    python negativos_parametros.py        # 12 llamadas a Haiku, presupuesto 0,5 $

Cada variante es la política base de exp/reutilizacion con reemplazos exactos (si un texto no aparece
una sola vez, aborta). Mismo extractor (SISTEMA_EXTRAER) y mismos bloques parametrizados de
exp/parametros. Cada «solo_parametros: true» cuenta como fallo silencioso potencial.
Salida en exp/parametros_negativos/ (carpeta nueva; no se sobrescribe).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import experimento_kappa as E
import reutilizacion_parametros as R

SALIDA = R.RAIZ / "exp" / "parametros_negativos"
PRESUPUESTO = 0.5
NIVELES = 'Acumulado ≤ 500 € → "auto"; ≤ 5000 € → "jefe_departamento"; > 5000 € → "direccion".'
VARIANTES = [
    ("negocio", "N1_cuarto_nivel", "parece_parametro",
     [(NIVELES, 'Acumulado ≤ 500 € → "auto"; ≤ 5000 € → "jefe_departamento"; ≤ 20000 € → "direccion"; '
                '> 20000 € → "comite".')]),
    ("negocio", "N2_umbral_por_categoria", "parece_parametro",
     [(NIVELES, NIVELES + ' Para la categoría "viajes", el límite de "auto" es 200 € en vez de 500 €.')]),
    ("negocio", "N3_importe_invalido", "regla_nueva",
     [("Solicitudes (en este orden):\n",
       'Solicitudes (en este orden):\n0. Importe con IVA menor que 1 € → estado "rechazada", motivo "importe_invalido".\n')]),
    ("negocio", "N4_plazo_cancelacion", "regla_nueva",
     [('Cancelaciones: anulan la solicitud "referencia".',
       'Cancelaciones: anulan la solicitud "referencia" solo si la cancelación tiene fecha dentro de los 30 días '
       'siguientes a la de la solicitud; si no, se ignora.')]),
    ("negocio", "N5_mixto_gbp", "mixto",
     [("Acumulado ≤ 500 €", "Acumulado ≤ 750 €"),
      ("4. revision_it = true",
       '4. Si el importe original está en GBP, el aprobador es siempre "finanzas", sea cual sea el acumulado.\n'
       '5. revision_it = true')]),
    ("negocio", "N6_orden_cancelaciones", "regla_nueva",
     [("procesa las entradas por fecha y, a igual fecha, por id (orden alfabético).",
       "procesa las entradas por fecha y, a igual fecha, primero las cancelaciones y después las solicitudes, "
       "cada grupo por id (orden alfabético).")]),
    ("datos", "D1_region_vacia", "regla_nueva",
     [("0. Si un 'id' aparece repetido,", "0. Descarta las filas cuya 'region' esté vacía. Si un 'id' aparece repetido,")]),
    ("datos", "D2_umbral_por_columna", "parece_parametro",
     [("se aleja más de 4.5 desviaciones robustas", "se aleja más de 4.5 desviaciones robustas (3 si la columna es 'precio_medio')")]),
    ("datos", "D3_salida_nueva", "regla_nueva",
     [('{"par": [col_a, col_b], "anomalias": [ids]}', '{"par": [col_a, col_b], "anomalias": [ids], "columnas_excluidas": [cols]}'),
      ("esa columna no marca anomalías en esa region.",
       'esa columna no marca anomalías en esa region.\n3. "columnas_excluidas": las columnas numéricas con más de un '
       "20 % de valores ausentes.")]),
    ("datos", "D4_dos_valores", "parece_parametro",
     [("una fila es anómala si alguno de sus valores no ausentes, en CUALQUIER columna numérica, se aleja",
       "una fila es anómala si al menos DOS de sus valores no ausentes, en columnas numéricas distintas, se alejan")]),
    ("datos", "D5_mixto_humedad", "mixto",
     [("4.5 desviaciones", "3.5 desviaciones"),
      ("Algunos valores numéricos usan coma decimal", "Ignora la columna 'humedad' en todo el análisis. "
                                                      "Algunos valores numéricos usan coma decimal")]),
    ("datos", "D6_logaritmo", "regla_nueva",
     [("1. Relación principal: entre", "1. Relación principal: antes de calcular, aplica logaritmo natural a las "
                                       "columnas cuyos valores no ausentes son todos positivos. Entre")]),
]


def main() -> None:
    if SALIDA.exists() and any(SALIDA.iterdir()):
        sys.exit(f"{SALIDA} ya tiene resultados: no se sobrescribe")
    _, _, _, bloques = R.cargar()
    param = {d: json.loads((R.SALIDA / f"bloque_param_{d}.json").read_text(encoding="utf-8")) for d in R.DOMINIOS}
    casos = []
    for dom, nombre, tipo, cambios in VARIANTES:
        texto = bloques[dom]["contrato_texto"]
        for viejo, nuevo in cambios:
            if texto.count(viejo) != 1:
                sys.exit(f"{nombre}: el texto a reemplazar no aparece exactamente una vez: {viejo[:50]!r}")
            texto = texto.replace(viejo, nuevo)
        casos.append({"dominio": dom, "nombre": nombre, "tipo": tipo, "texto": texto})

    def una(c: dict) -> dict:
        if E._gasto["usd"] >= PRESUPUESTO:
            return c | {"solo_parametros": None, "motivo": "presupuesto agotado", "coste_usd": 0.0}
        esquema = json.dumps(param[c["dominio"]]["esquema"], ensure_ascii=False, indent=1)
        r = E.llamar(E.MODELOS["barato"], R.SISTEMA_EXTRAER,
                     f"POLÍTICA A:\n{bloques[c['dominio']]['contrato_texto']}\n\nESQUEMA:\n{esquema}\n\n"
                     f"POLÍTICA B:\n{c['texto']}")
        try:
            d = E._json_de(r["texto"])
            sp, params, motivo = bool(d.get("solo_parametros")), d.get("params") or {}, str(d.get("motivo", ""))[:240]
        except Exception:
            sp, params, motivo = None, {}, "respuesta no parseable"
        return c | {"solo_parametros": sp, "params": params, "motivo": motivo, "coste_usd": r["coste_usd"]}

    filas = R.en_paralelo(una, casos)
    dicen_si = [f["nombre"] for f in filas if f["solo_parametros"] is True]
    sin_respuesta = [f["nombre"] for f in filas if f["solo_parametros"] is None]
    resumen = {"casos": len(filas), "dicen_solo_parametros": dicen_si, "sin_respuesta": sin_respuesta,
               "coste_usd": sum(f["coste_usd"] for f in filas),
               "veredicto": "H5 NO está lista: el extractor aceptó reglas nuevas" if dicen_si or sin_respuesta
               else "H5 sigue: el extractor rechazó las 12",
               "sha256": {"script": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          "reutilizacion_parametros.py": hashlib.sha256((R.RAIZ / "reutilizacion_parametros.py").read_bytes()).hexdigest()}}
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / "resultados.jsonl").write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in filas), encoding="utf-8")
    (SALIDA / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding="utf-8")
    for f in filas:
        marca = "✓ no" if f["solo_parametros"] is False else ("✗ SÍ" if f["solo_parametros"] else "? --")
        print(f"{marca} {f['dominio']:7} {f['nombre']:24} {f['tipo']:16} {f['motivo'][:90]}")
    print(json.dumps({k: v for k, v in resumen.items() if k != "sha256"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
