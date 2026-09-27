## ADR-001-API: Validated Block Engine with Cost-Based Routing — Contract

### Status
[x] Proposed | [ ] Under review | [ ] Approved | [ ] Deprecated

### Type
API/Contract slice — depends on ADR-001-FN

### Parent
ADR-001-FN — Validated Block Engine with Cost-Based Routing (Functional)

### Context
The consumers are **agents and our own tools**, not a user interface:

- Claude Code's hooks;
- our own terminal (TUI);
- process scripts;
- the operator from the command line;
- an auditor who reproduces decisions.

All of them need the **same objects** (success contract, block, task, resolution, event, and
escalation), with the same shape in the reference implementation and in the production one.

That is why the contract is a **shared data schema** before it is a network protocol. The
operations are exposed the same way as a library, as a CLI, and, later, as an MCP server, whose
tools are already described in JSON Schema.

[ASSUMPTION: in v1 there is no network service; library + CLI, local, a single operator. The MCP
adapter arrives in v1.1 without changing the schema.]

### Contract decision
- **Format:** JSON Schema 2020-12, a single document with `$defs` per entity.
- **Location:** `contracts/v1/bloques.schema.json`, with examples in `contracts/v1/ejemplos/` and
  tests in `contracts/validar.py`.
- **Versioning:** SemVer in the `schema_version` field, mandatory in every instance, and the major
  version in the path (`contracts/v1/`).
  - New optional fields or enum values added at the end: minor.
  - Removing fields, making them mandatory, or changing types or semantics: major, with a
    `v2/` directory that coexists for one cycle.
- **Linter:**
  - `check-jsonschema --check-metaschema` and `Draft202012Validator.check_schema`.
  - Spectral does not apply because there is no OpenAPI. It would be added with the HTTP adapter, if it comes.
- **Type generation:**
  - Python: `datamodel-code-generator`, which generates Pydantic v2.
  - Rust: `typify`, which generates structs with serde.
  - The generated types **are not edited by hand**.
  - [ASSUMPTION: not verified in this session; access to PyPI and crates.io is blocked here.
    First step of the BE: check it in Aldemar's environment.]

### Endpoints / Operations
The same signature in the library (Rust / Python), in the CLI (`bloques <operacion> --in x.json`), and in MCP
(v1.1):

| Operation | Input → Output (`$defs`) | FN traceability | Type |
|---|---|---|---|
| `registrar_proceso` | ContratoExito → `proceso` \| Error | Capability 1 · "Proceso sin forma de verificar el éxito" | Mutating, idempotent by `proceso` |
| `compilar_bloque` | process + examples → Bloque(`candidato`) \| Error | Capability 2 · "Proceso sin bloque" | Mutating, costs money |
| `aprobar_bloque` | bloque_id + operator → Bloque(`aprobado`) | Capability 2 · RN6 | Mutating |
| `resolver_tarea` | TareaSolicitud → Resolucion \| Error | Capabilities 3–5 · all resolution scenarios | Mutating, **idempotent by key** |
| `reproducir` | evento_id → Resolucion \| Error(`REPRODUCCION_DIVERGENTE`) | Capability 6 · "Reproducir una decisión pasada" | Read-only, no calls to the generator |
| `resolver_escalado` | escalado_id + verdict → Escalado | Capability 5 · "El revisor no responde a tiempo" | Mutating |
| `informe` | process + period → aggregated metrics | Capability 7 | Read-only |
| `Evento` stream | JSONL, append-only | RN9 | Stream |

### Error handling
- **Envelope:** `$defs/Error` = `{codigo, mensaje, detalle?, reintentable}`. A raw
  exception is never thrown to the consumer.
- **Codes:**

  | Code | When |
  |---|---|
  | `CONTRATO_INVALIDO` | the contract does not meet the schema |
  | `SIN_VERIFICADOR` | RN1 |
  | `PROCESO_DESCONOCIDO` | the process is not registered |
  | `VALIDACION_INSUFICIENTE` | the block does not pass the validation examples (RN6) |
  | `PERFIL_INSUFICIENTE` | fewer than 30 examples for the validity domain (RN6) |
  | `BLOQUE_NO_APROBADO` | the block does not have the operator's approval |
  | `PRESUPUESTO_AGOTADO` | RN10; not retryable |
  | `GENERADOR_NO_DISPONIBLE` | retryable |
  | `JUEZ_NO_DISPONIBLE` | retryable; meanwhile, the provider treats the task as "not equivalent" (BDD RISK from the FN) |
  | `EVENTO_DESCONOCIDO` | the event to reproduce does not exist |
  | `REPRODUCCION_DIVERGENTE` | the reproduction does not match the original |
  | `ESQUEMA_INCOMPATIBLE` | the instance's schema version is not compatible |
  | `CLAVE_IDEMPOTENCIA_EN_CONFLICTO` | same key with different inputs |

- **Idempotency:**
  - `resolver_tarea` uses the key `(proceso, clave_idempotencia)`.
  - Same key and same `hash_entradas`: returns the stored `Resolucion`, at no cost.
  - Same key with different inputs: `CLAVE_IDEMPOTENCIA_EN_CONFLICTO`.
  - `reproducir` is pure by design: the artifacts (plans, programs, outputs) are in
    storage by hash.
- **Contract invariants**, enforced by the schema itself:
  - `escalar` / `no_resuelta` imply `salida = null` (RN8);
  - `reutilizar` / `verificacion_cruzada` require `bloque`;
  - a `resuelto` escalation requires a verdict;
  - an `aprobado` block requires an approver;
  - the profile requires ≥ 30 examples and validation ≥ 5 (RN6).

### AVAP Checklist

**🔷 API-First**
- [x] Consumers identified: agent hooks, own TUI, scripts, operator via CLI, auditor.
- [x] Use cases traceable to the FN scenarios (operations table).
- [x] Domain names in Spanish, consistent with the FN.
  - ⚠️ If opened to third parties, an English alias may be needed. [Minor AVAP RISK]
- [x] Can be tested without implementing:
  - real examples in `contracts/v1/ejemplos/`;
  - a simulated generator adapter that replays the executions recorded in `exp/`.
- [x] Explicit versioning.

**📝 Contract-First**
- [x] Format: JSON Schema 2020-12.
- [x] Location: `contracts/v1/`.
- [x] The contract precedes the production implementation. The prototypes in `*.py` are research
  code and are **not** promoted: the BE is written from the contract.
- [x] Linter assigned (metaschema).
- [x] Breaking / non-breaking change policy.
- [ ] Type generation defined but **not verified** in this environment. [AVAP RISK]

**🔴 TDD**
- [x] Contract tests (`contracts/validar.py`): valid metaschema, 9/9 positives built with
  real data from the experiments and **16/16 negatives rejected**, one per business rule.
- [x] Unit tests for the logic: defined in ADR-001-BE.
- [x] CI gate: blocks the merge if the metaschema, the contract tests, the
  validation of every event emitted in the tests, or the golden corpus parity (BE) fails.
- [x] Pyramid: unit (cascade, threshold, profile) → contract (schema and golden corpus) → E2E
  (benchmark with a simulated generator; with a real generator, only nightly and with a budget).

### Consumers
- **ADR-001-BE:** provider.
- **Agent hooks and our own terminal:** consumers via the library and, in v1.1, via MCP.
- **Operator and auditor:** consumers via the CLI.

### Consequences
- ✅ **Positive:**
  - A single schema feeds the Python and Rust types, CI validation, and, later,
    the MCP tools.
  - The critical business rules (RN1, RN6, RN8, RN9) are enforced by the schema itself, not just the
    code.
  - Reproduction is guaranteed by contract: events with artifacts by hash.
- ⚠️ **Risks:**
  - `estado_objetivo` is a free-form JSON Schema inside the contract; a poorly written process
    schema passes structural validation.
  - Events store fingerprints, but the artifacts may contain sensitive data; the retention
    policy is decided in the BE.
- 🔄 **Accepted technical debt:**
  - No authentication or authorization in v1: a single local operator.
  - No AsyncAPI for the event stream (JSONL + schema); reevaluated if there are remote
    consumers.
- [AVAP RISK: Python and Rust type generation not verified here. Mitigation: first ticket of
  the BE, before writing the domain.]

### Evaluation
| Dimension      | Score | Justification |
|----------------|-------|---------------|
| Scale          | 4/5   | Proportional to one operator and one's own processes. One schema, no server |
| Security       | 3/5   | No authentication (accepted for local v1). Sensitive artifact and escalation data is handled in BE [ASSUMPTION] |
| Operability    | 4/5   | Validatable in CI with standard tools. Errors with a code and `reintentable` |
| Evolution      | 4/5   | SemVer and `schema_version` in every instance, major version by directory, MCP adapter without changing the schema |
| Cost           | 5/5   | No infrastructure. The only variable expense is calls to the generator and the judge, measured in every event |
| Simplicity     | 4/5   | One document, 11 entities, and 7 operations. OpenAPI/HTTP ruled out for v1 since there is no remote consumer |
| Contract       | 4/5   | Formal, versioned, and tested (16 negatives per rule). One point less for unverified type generation |
