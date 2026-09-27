# Golden corpus v1 (ADR-001-BE, step 6)

130 tasks solved with the ADR-001-FN cascade and **everything** needed for the Rust core to
check every decision without calling any model.

| File | Contents |
|---|---|
| `v1/bloques.jsonl` | 2 `Bloque` (data, business) with a 30-example domain of validity |
| `v1/tareas.jsonl` | 130 `TareaSolicitud` (+ `_tarea_benchmark`) |
| `v1/eventos.jsonl` | 130 `Evento` with steps, hashed artifacts, and costs |
| `v1/resoluciones.jsonl` | 130 `Resolucion` |
| `v1/escalados.jsonl` | 6 `Escalado` |
| `v1/llamadas.jsonl` | 160 model calls, ordered by task: `(tarea, n, clave del prompt, respuesta por hash)` |
| `v1/esperado.jsonl` | the corpus ground truth, one line per task (see below) |
| `v1/artefactos/` | content-addressed store (sha256): plans, programs, outputs, prompts, responses, CSV |
| `v1/config.json` | RN3 threshold (0.8337), V, per-task budget, models, effort, sandbox |

Each line of `esperado.jsonl` carries:

- expected decision;
- output hash;
- step sequence `(tipo, resultado, artefacto)`;
- whether the output is correct against the benchmark ground truth.

**Verified guarantees:**

- **398/398 valid instances** against `contracts/v1/bloques.schema.json`.
- **Replay 130/130 identical** (`python corpus/grabar.py reproducir`): same decision, same
  output, and same steps, at $0 spend in 43 s. The recorded adapter checks that every prompt
  matches the recorded one; if not, it fails with "divergencia".
- Deterministic execution: `PYTHONHASHSEED=0` in the sandbox.

**How the Rust core uses it** (CI blocking criterion):

- `GeneradorGrabado` and `JuezGrabado` serve `llamadas.jsonl` by `(tarea, n)` and verify the key.
- For every task, the core has to produce the same decision, the same `hash_salida`, and the
  same step sequence as `esperado.jsonl`.

**Notes:**

- Costs are in list-price USD (the CLI) and are logged in `_eur` fields (1:1). [ASSUMPTION]
- Subprocess sandbox: this session has no Docker daemon.
- `grabar.py` is **disposable**. What's kept is this corpus, not a second implementation.
- Recording cost: $13.19.
