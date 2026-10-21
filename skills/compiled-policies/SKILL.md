---
name: compiled-policies
description: >-
  Executable success contracts, parametric policy blocks, and A2-DLC Z3/SMT contract exporter.
  Activate this skill when defining deterministic business policy blocks, validating contracts
  (`contracts/validar.py`), running the 120-task benchmark, or exporting `S*` slice contracts
  to A2-DLC via `bridge_a2dlc.py`.
---

# Compiled Policies Agent Skill (`compiled-policies`)

**compiled-policies** compiles business rules into frozen deterministic blocks (`congelado=True`, $\text{TPR}=1, \text{FPR}=0$) and exports formal `S*` slice contracts (with Z3/SMT invariants and boundary probes) into **A2-DLC**.

---

## 1. Core Agent Workflows

### A. Exporting a Policy Block to an A2-DLC `S*` Contract (`bridge_a2dlc.py`)
To generate a formal `contract.json` (`S*`) and `intent.json` for `a2dlc run`:

```python
from bridge_a2dlc import ParamsPolitica, exportar_politica_negocio_a2dlc, extraer_invariantes_de_bloque
import json

params = ParamsPolitica(
    umbral_auto=500,
    umbral_director=5000,
    iva_porcentaje=21,
    excepcion_seguridad=True,
    excepcion_urgente=True,
)
contract = exportar_politica_negocio_a2dlc(params, target_file="src/compras/politica.py")
print(json.dumps(contract, indent=2))
```

Or run the bridge test suite directly:
```bash
pytest test_bridge_a2dlc.py -q
```

### B. Validating JSON Schema Contracts & Golden Corpus
Before committing changes to contracts or policy blocks:
```bash
python contracts/validar.py
a2dlc check
```

### C. Running the Seeded Benchmark & Perturbation Autotest
```bash
python -m benchmark.generar --por-nivel 10 --extremo 30 --salida benchmark/out
python -m benchmark.verificar benchmark/out
```
*(Guarantees 120/120 valid solutions pass and 360/360 perturbed outputs fail).*
