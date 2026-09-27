## ADR-001-BE: Validated-block engine with cost-based routing — Backend

### Status
[x] Proposed | [ ] In review | [ ] Approved | [ ] Deprecated

### Type
Backend implementation slice — depends on ADR-001-API

### Parents
- ADR-001-FN — functional
- ADR-001-API — contract to implement (`contracts/v1/bloques.schema.json`)

### Context
Three facts shape the implementation:

1. **Blocks are generated Python programs.** A Python runtime is needed to run them, and the
   AI adapters (generator and judge) are simpler in Python.
2. **The decision logic must be single and reproducible.** RN9 and the `reproducir` operation
   require it. Two implementations (reference and production) are two versions that can drift
   silently apart: the same correlated failure that H3 measured, but in our own code.
3. **Your standard is Python for experimentation and Rust in production.** Also, the agent
   hooks need to start up fast on the reuse path, which takes milliseconds.

**Decided (Aldemar, Sep 27, 2026):** a single Rust core with Python bindings, instead of the
"two implementations + parity test" pattern from the memory module. Rationale in Options.

**Environment note (Sep 27, 2026):** in the cloud session, PyPI, crates.io, and npm respond with
403 (egress policy). Steps 1–5 (crates and packages) are built on Aldemar's machine. Step 6
(golden corpus) is moved up because it only needs the AI CLI and the standard library. The
recorder that produces it is **disposable**: what is kept is the corpus, not a second
implementation.

### Options considered
| Option | Pro | Con | Verdict |
|---|---|---|---|
| **A. Rust core + PyO3/maturin bindings; adapters and experimentation in Python** | A single implementation of the decision, so replay is exact by construction. Binary CLI for the hooks. Rust in production | Two toolchains. You have to compile to experiment with the core | **Chosen** |
| B. Reference Python + production Rust + parity | Pattern you already use. Free experimentation in Python | Parity only covers the golden corpus; outside it, silent drift. Double maintenance | Discarded per RN9 |
| C. Python only (modular monolith) | Simplest. Prototypes already exist | Slow startup for hooks (pandas/pydantic). Moves away from your production standard | Plan B if Rust isn't a hard requirement |

### Implementation decision
- **Language / framework:**
  - Crate `bloques-nucleo` in Rust: domain, cascade, profile, invariants, log, and replay.
  - Binary `bloques`: the CLI.
  - Python package `bloques`: PyO3 bindings via maturin, plus the generator, judge, and
    sandbox adapters.
  - Types generated from the schema, never hand-edited: `typify` in Rust and
    `datamodel-code-generator` in Python. [ASSUMPTION pending verification: ticket 1]
- **Domain layer (Rust, no I/O):**
  - Schema entities: `ContratoExito`, `Bloque`, `TareaSolicitud`, `Resolucion`, `Evento`,
    `Escalado`.
  - Value objects: `Hash`, `Eur`, `Prob`, `Umbral`.
  - `MaquinaResolucion`: each task's resolution is an **explicit state machine**
    (crate `statig`, MIT) with these states:
    `Recibida → HashComparado → [JuezConsultado] → PerfilEvaluado → EjecutandoBloque → [Generando → Comparando] → Entregada | Escalada | NoResuelta`.
    Each transition emits a `PasoEvento`. It's your original idea (flows as a state machine
    for observability) in its minimal form: the network is fixed and small, and the only
    dynamic part is the branch.
  - `CalculadoraUmbral` (RN3): `p* = 1 − (c_gen + (1 − q_gen)·V)/V`, with `c_gen` and `q_gen`
    measured from the process log. By default it uses the values measured in H2: $0.20 and 0.93.
  - `Perfil` (RN6):
    - infers the domain of validity with ≥ 30 normal inputs and checks new inputs against it;
    - features are obtained with per-input-type extractors (`ExtractorRasgos`: input JSON,
      CSV, …);
    - new values in a set → alarm; out of range × 1.5 → alarm.
  - `Invariantes`:
    - validates the output against `estado_objetivo` (JSON Schema);
    - applies per-process predicates, small and declared in the process registry.
  - `VerificacionCruzada` (RN7, RN8): compares normalized outputs. **Never** uses two versions
    of the same block.
- **Persistence:**
  - SQLite: processes, blocks, escalations, idempotency index, and metrics cache.
  - Event log in JSONL, which is the source of truth (append-only).
  - Content-addressed artifact store: `artefactos/sha256/ab/cd…` with plans,
    programs, inputs, and outputs.
  - Versioned SQL migrations in `migrations/`. [ASSUMPTION: `rusqlite` + embedded migrations]
- **Side effects:**
  - Calls to the generator and judge, always with a per-task budget cap (RN10) and logging
    of the actual cost.
  - Program execution in the sandbox.
  - Event emission.
  - Idempotency via `(proceso, clave_idempotencia)` + `hash_entradas`, with the index in SQLite
    (ADR-001-API).
- **Adapters (Python, behind ports):**
  - `GeneradorClaude`: the plan with the highest-capacity model (RN5) and the program with the
    cheap one. This is the configuration measured in H1/H2.
  - `JuezEquivalencia`: the current one uses a cheap LLM (40/40 in H2, ≈ $0.014 per call).
    `JuezLaya` and `JuezJev` remain future adapters. Laya in English supports 512 tokens and the
    judge's input takes up 500–1,100, so it will need the multilingual version or a fine-tune
    with the labeled pairs that escalations generate.
  - `SandboxDocker` in production: an ephemeral container with no network, bounded CPU, memory,
    and time, with a read-only filesystem except `/work`. `SandboxSubproceso` only in
    development.
  - `GeneradorGrabado` / `JuezGrabado`: replay saved responses. They are the "mock" from
    ADR-001-API and the basis of the golden corpus.
- **Observability:**
  - Metrics are derived from the event log, with no separate system in v1:
    - total cost per task (RN2);
    - reuse rate;
    - histogram of `juez_p` against the threshold;
    - per-feature profile alarms;
    - disagreements, escalations, and expirations;
    - per-step latency.
  - `bloques informe` computes these per period.
  - Structured logs with `evento_id` on every line.
  - Optional OpenTelemetry export in v1.1.

### Patterns applied
- **Hexagonal:** the domain knows nothing about AI, Docker, or SQLite. Only ports.
- **Event log as source of truth** (lightweight event sourcing): replay, metrics, and
  recalibration all come from the same place.
- **State machine per resolution** (statecharts via `statig`).
- **Content-addressed storage** for artifacts.
- **Strategy** for judge, generator, sandbox, and feature extractors.
- **Circuit breaker** on generator and judge: after N consecutive errors, fast `*_NO_DISPONIBLE`.

### SOLID Checklist
- **S**
  - [x] Cascade, threshold, profile, invariants, cross-verification, and logging are separate
    modules, each with one reason to change.
  - [x] No "god orchestrator" class: the state machine only transitions and delegates.
- **O**
  - [x] New judges, generators, sandboxes, and extractors come in by implementing a trait or
    protocol, without touching the domain.
  - ⚠️ Per-process invariant predicates can grow into "god plugins."
    [SOLID RISK: limit each predicate to one property and review them when a process is
    onboarded.]
- **L**
  - [x] Recorded adapters are substitutable for the real ones (same ports), and that is what
    makes the golden corpus possible.
- **I**
  - [x] Narrow ports, each with a single capability:
    - `Generador::plan` / `Generador::programa`
    - `Juez::equivalencia`
    - `Sandbox::ejecutar`
    - `AlmacenArtefactos::{poner, leer}`
    - `LogEventos::añadir`
    - `ColaEscalados::{abrir, resolver}`
- **D**
  - [x] The domain depends on traits. Adapters are injected at startup (CLI or Python).
    No building AI clients inside the domain.

### Contract test plan
- **Tools:**
  - `contracts/validar.py`: schema, 9 positive and 16 negative cases.
  - `cargo test` with `proptest`:
    - the threshold is monotonic in V and c_gen;
    - a profile input never triggers an alarm against its own profile.
  - **Golden corpus:** the 130 tasks in `benchmark/` with a logged decision (50 normal,
    40 policy-variant, and 40 with drift), with recorded generator and judge responses and the
    expected decision, taken from `exp/reutilizacion` and `exp/deriva`. The core has to
    reproduce **every** decision.
  - **Replay test:** every event emitted in the tests replays without calling the generator and
    with the same output.
- **Location in CI:** before merge, in this order: meta-schema → contract tests → `cargo test`
  → golden corpus → replay. The E2E with the real generator runs only overnight, with a
  maximum budget of $5.
- **Blocking criterion:** any instance invalid against the schema, any corpus decision that
  differs from the expected one, or any divergent replay.

### Validation gate (before "Approved")
The FN requires it because H3 was post-hoc:

1. **Pre-register criteria:**
   - 0 detectable incorrect deliveries;
   - escalations ≤ 10%;
   - total cost ≤ 20% of always generating, on tasks without drift.
2. Run **in shadow mode** on one of your real processes: the engine decides and logs, but does
   not deliver; compared against what you do today (gated-downshift or by hand) over ≥ 100
   tasks.
3. Only if it passes do FN, API, and BE get approved.

### Implementation order
1. Verify type generation (`typify`, `datamodel-code-generator`) from the v1 schema.
   Closes the AVAP RISK.
2. Event log, content-addressed store, idempotency, and `reproducir`. Replay first,
   because everything else is tested with it.
3. `MaquinaResolucion` + `CalculadoraUmbral` with recorded adapters.
4. `Perfil` + `Invariantes` + `VerificacionCruzada`.
5. PyO3 bindings + real adapters (generator, judge, `SandboxDocker`) + CLI.
6. Re-record the golden corpus with full artifacts. The experiments saved `ok`,
   not the outputs; it will cost ≈ $10–15.
7. Shadow mode over a real process, which is the gate.
8. MCP adapter (v1.1) for hooks and the in-house terminal.

### Progress (Sep 27, 2026)
- **Step 6 done (moved up):** `corpus/v1`.
  - 130 tasks, 398/398 instances valid against the contract.
  - **Replay 130/130 identical with no calls** (43 s, $0).
  - Recording cost: $13.19.
  - The corpus is the oracle for the Rust core: same decision, same `hash_salida`, and same
    steps.
- **Steps 1–5:** pending on Aldemar's machine (package registries blocked in the cloud).

### Status of RN7 (data drift)
Gate attempt 1 **fails** (see ADR-001-FN). Three concrete design gaps:

1. **Cross-verification doesn't break the correlated failure:** the cheap generator writes the
   same incorrect parser as the block.
2. **"Block crashes + new input" delivers the new solution without verifying it.**
3. **The new solution crashing is treated as a disagreement**, instead of retrying.

**Proposal for H4** (pre-register before testing):

- **Drift = recompile, not patch task by task** (the classic pattern of retraining when the
  data drifts):
  1. Profile alarms send the task to **quarantine**.
  2. The highest-capacity generator recompiles the block **given the alarm reasons**
     (e.g. `formas_celda: nuevo ['9.9,9']`).
  3. The operator labels 3 to 5 new cases.
  4. The candidate has to pass the old validation and the new cases.
  5. After approval, the quarantine is processed.
- **Cheap adjustments:**
  - retry once if the new solution crashes;
  - never deliver a new out-of-domain solution without a second opinion.
- **Proposed death criterion:** with the same drift as H3, 0 incorrect deliveries, ≤ 10% of
  tasks requiring a human, and total cost with drift ≤ that of "always generate" ($0.371).

### Consequences
- ✅ **Positive:**
  - A single implementation of the decision, so replay is exact and auditing is reliable.
  - All the experiment knowledge is encoded:
    - cost-derived threshold;
    - fixed cascade;
    - highest-capacity planner;
    - cross-verification with different information.
  - The judge is swappable, so Laya or Jev come in without touching the domain.
- ⚠️ **Risks:**
  - **Executing generated code is the main attack vector.** `SandboxDocker` mitigates it,
    but on macOS Docker adds latency and RAM usage, which is already your bottleneck.
    [ASSUMPTION: acceptable, since it only runs on reuse and generation, not on every prompt]
  - **Sensitive data** in artifacts and escalations. Configurable retention (30 days by default)
    and a "fingerprints only" mode that gives up replay for those tasks. [ASSUMPTION]
  - **Two toolchains** (Rust + Python): the documented plan C is needed if this gets too heavy.
- 🔄 **Accepted technical debt:**
  - No authentication in v1.
  - Metrics computed from JSONL (doesn't scale to millions of events; revisit above
    10⁵ events per process).
  - Local escalation queue with no integrations (Slack/Jira in v1.1).
- [SOLID RISK: per-process invariant predicates; see the O in the checklist.]

### Evaluation
| Dimension      | Score | Justification |
|----------------|-------|---------------|
| Scale          | 4/5   | Local, single operator. SQLite and JSONL are enough up to ~10⁵ events per process; the limit is stated |
| Security       | 3/5   | Generated code is isolated in a network-less container. No authentication (accepted), and retention of sensitive data is configurable but [ASSUMPTION] |
| Operability    | 4/5   | Binary + Python package. Metrics and replay from the log. Circuit breaker on AI dependencies |
| Evolution      | 4/5   | Hexagonal with narrow ports. Judge, generator, and extractors swappable. Versioned schema |
| Cost           | 5/5   | No infrastructure. The variable spend is AI, capped per task and measured on every event |
| Simplicity     | 3/5   | Two languages + PyO3 + Docker, justified by RN9 and the hooks. Plan C (Python only) is documented |
| Design         | 4/5   | SOLID satisfied; one declared risk (per-process predicates) |
