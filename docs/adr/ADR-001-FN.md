## ADR-001-FN: Validated Block Engine with Cost-Based Routing — Functional

### Status
[x] Proposed | [ ] Under review | [ ] Approved | [ ] Deprecated

### Type
Functional slice (parent of API/BE)

### Context
Automating a process with AI today forces a choice between two extremes:

- **drawing the flow by hand**: cheap and auditable, but rigid;
- **letting an agent decide each step**: flexible, but expensive, not reproducible, and with
  errors that no one sees.

The engine looks for the middle ground. Each task in a process is solved in the cheapest way
that meets its **success contract**, and **a wrong answer is never delivered without warning**
when there is a way to detect it.

The decision rests on three of our own experiments (evidence in `README.md` §5–§8, data in `exp/`):

| Hypothesis | Result | Consequence for behavior |
|---|---|---|
| H1: dynamically choosing between cheap and expensive generators | A plan from the cheap generator damages the next stage (δ̂ = 0.23 [0.11; 0.36]) and gets it right as often as not planning at all (72% versus 73%), while costing more. The actual cost ratio is 3.2×, below where optimizing pays off | Fixed rule: planning is always done by the highest-capacity generator. **No** dynamic choice between generators |
| H2: reusing a validated block when the task is covered | Total cost -84% and 100% accuracy versus 93% when always generating. The equivalence judge got it right 40/40. A signature based on numbers let 6 logic changes slip through | Reuse is the default path, decided by the cascade "identical text → equivalence judge → generate" |
| H3: data drift with the policy unchanged | Reusing blindly: 2 silent errors out of 40. The second version of the same process failed the same way (0% detection). The validity profile with 5 examples gave 34% false alarms; with 30, 0% [post-hoc]. Profile + cross-verification + human: 0 wrong deliveries and 3 out of 40 escalated [post-hoc] | Outside the validity domain, it is never reused blindly: it is cross-verified and, if there is disagreement, it is escalated |

[ASSUMPTION: the first user is the operator himself (Aldemar) working on his own processes;
use by a team comes later.]
[ASSUMPTION: the human reviewer for escalations is the same operator; there is no multi-person
approval flow.]

### Actors
- **Process operator**: defines the policy, the success contract, and the value V of solving
  a task. Approves the blocks.
- **Requester**: agent or system that asks to solve a task in a process and receives the
  resolution.
- **Generator**: AI that writes a plan and a program when there is no block that fits.
- **Equivalence judge**: classifier that estimates whether a new policy is logically
  equivalent to the block's.
- **Human reviewer**: receives escalations with all the evidence and issues a verdict.
- **Auditor**: reproduces any past decision from the log.

### Functional capabilities
1. **Register a process** with its success contract: target state, verifiers from cheapest
   to most expensive, minimum success probability, and value V.
2. **Register a block**: a validated program with examples, together with its **validity domain**
   (which inputs are considered normal).
3. **Solve a task**: decide between reusing, generating, or escalating, and deliver the resolution
   with its evidence and its cost.
4. **Detect inputs outside the validity domain** and cross-verify them, comparing
   the block with a freshly generated program.
5. **Escalate to human review** when the block and the alternative disagree, without delivering anything.
6. **Log each decision** as an immutable event and **reproduce it** later.
7. **Report** per process: total cost per task, accuracy, reuse rate, escalations, and
   alarms.
8. **Recalibrate** the reuse threshold using actual costs and verdicts.

### BDD — Expected behavior

```gherkin
# language: es
Característica: Resolver tareas de un proceso al menor coste total sin entregar errores silenciosos
  Para automatizar procesos repetitivos sin pagar cada vez por regenerar la solución
  Como solicitante de un proceso dado de alta
  Quiero que cada tarea se resuelva por el camino más barato que cumpla su contrato de éxito

  Antecedentes:
    Dado un proceso con contrato de éxito, verificadores y valor V dados de alta
    Y un bloque validado con su dominio de validez para la política vigente del proceso

  Escenario: Política idéntica y entrada dentro del dominio → se reutiliza el bloque
    Dado una tarea cuya política coincide exactamente con la del bloque
    Y cuyas entradas están dentro del dominio de validez
    Cuando el solicitante pide resolverla
    Entonces la resolución se entrega con decisión "reutilizar"
    Y el coste de generación de la tarea es cero

  Escenario: Política redactada de otra forma pero equivalente → se reutiliza
    Dado una tarea cuya política difiere en redacción pero no en lógica
    Cuando el solicitante pide resolverla
    Entonces el juez estima una probabilidad de equivalencia por encima del umbral de reutilización
    Y la resolución se entrega con decisión "reutilizar"

  Escenario: Política con un cambio sustantivo → se genera de nuevo
    Dado una tarea cuya política cambia un umbral, un porcentaje o una regla
    Cuando el solicitante pide resolverla
    Entonces el bloque no se usa
    Y la resolución se entrega con decisión "generar" y con el coste de generación

  Escenario: Entrada fuera del dominio de validez y ambas soluciones coinciden
    Dado una tarea con la política del bloque pero con entradas fuera de su dominio de validez
    Cuando el solicitante pide resolverla
    Entonces se genera una solución nueva y se compara con la del bloque
    Y al coincidir se entrega con decisión "verificacion_cruzada"

  Escenario: Entrada fuera del dominio de validez y las soluciones discrepan
    Dado una tarea con entradas fuera del dominio de validez
    Cuando el bloque y la solución nueva producen resultados distintos
    Entonces no se entrega ningún resultado
    Y se abre un escalado para el revisor humano con ambas salidas y los motivos de la alarma

  Escenario: El bloque falla de forma visible → se genera de nuevo
    Dado una tarea para la que el bloque termina con error o sin salida válida
    Cuando el solicitante pide resolverla
    Entonces se genera una solución nueva y se entrega si su salida es válida

  Escenario: Proceso sin bloque → se genera y se propone como bloque candidato
    Dado un proceso sin bloque registrado
    Cuando se resuelven tareas generando soluciones
    Entonces la solución que supere todos los ejemplos de validación se propone como bloque candidato
    Y el bloque solo se usa después de que el operador lo apruebe

  Escenario: Reproducir una decisión pasada
    Dado el registro de una tarea ya resuelta
    Cuando el auditor pide reproducirla
    Entonces se obtiene la misma decisión y la misma salida sin nuevas llamadas al generador

  Escenario: Proceso sin forma de verificar el éxito
    Dado un operador que intenta dar de alta un proceso sin ningún verificador
    Cuando envía el contrato
    Entonces el alta se rechaza indicando que falta el verificador

  Escenario: El revisor no responde a tiempo
    Dado un escalado abierto
    Cuando vence el plazo de revisión sin veredicto
    Entonces la tarea queda como "no resuelta" y cuenta como coste V
    Y nunca se entrega por defecto ninguna de las dos salidas
```

### Business rules
- **RN1. Success.** A task succeeds if its final state meets the contract's postcondition,
  checked by a verifier fixed before execution that the executor cannot modify.
- **RN2. Total cost per task.** It is what was spent plus V if the task is not resolved (a
  detected wrong delivery, an escalation, or a failure). It is the primary metric. Using "cost per
  resolved task" as the primary metric is prohibited, because it rewards failing cheaply (finding H1).
- **RN3. Reuse threshold.** It is reused by equivalence only if
  `P(equivalente) > 1 − (c_gen + (1 − q_gen) · V) / V`, with c_gen and q_gen measured from the process.
  The threshold is not set by hand.
- **RN4. Fixed cascade.** The order is: identical text, then equivalence judge, then generate.
  Consulting the judge when the text is identical is prohibited, because it adds nothing and costs money.
- **RN5. Planning.** It is always done by the highest-capacity generator; planning with the
  cheap one is prohibited (H1). There is no dynamic choice of generator.
- **RN6. Block registration.** Requires passing all validation examples (minimum 5) and a validity
  domain inferred from at least 30 normal inputs. A block without the operator's approval is not
  used.
- **RN7. Out of domain.** Never reused blindly: always cross-verified with a
  new solution. If they disagree, it is escalated. Two versions of the same block **do not** count as
  cross-verification (correlated failure, H3).
  **[NOT VALIDATED in gate attempt 1: 2 wrong deliveries and 15% escalations.]**
- **RN8. Plan B is not an oracle either.** A new solution that disagrees with the block is not delivered
  just for being new.
- **RN9. Traceability.** Every resolution leaves an immutable event with:
  - the fingerprint of the inputs;
  - the block version;
  - the probabilities and thresholds used;
  - the costs and the decision.
- **RN10. Budget.** Each task has a spending cap. If it is exceeded, the task is escalated; no
  further spending happens.

### Acceptance criteria
Against the reference benchmark (`benchmark/`) and, for the final gate, against real processes
in shadow mode:

- [ ] **Flow without drift** (H2 mix):
  - total cost per task ≤20% of "always generate";
  - accuracy ≥ that of "always generate".
- [ ] **Flow with drift** (H3): 0 wrong deliveries and escalations ≤10% of tasks with drift.
- [ ] **Improper reuse:** 0 tasks with a substantively different policy resolved with the
  block (H2: 0/20).
- [ ] **Reproduction:** 100% of the logged tasks give the same decision and output without calling
  the generator.
- [ ] **Contracts without a verifier:** 100% of registration attempts are rejected.
- [ ] **Events:** 100% of those emitted are valid against the contract and carry cost,
  probabilities, and version.
- [ ] **Confirmation gate:** the validity profile and the cross-verification are re-evaluated
  with pre-registered criteria on new or real drift (in H3 they were post-hoc). This is
  mandatory before marking this ADR as Approved.

### Gate result — attempt 1 (Sep 27, 2026)
Replication with the design **fixed in this ADR** (30-example profile + cross-verification +
human) on the same 40 tasks with drift, with new generations (`corpus/v1`).

| Criterion | Target | Result | Pass? |
|---|---|---|---|
| Wrong deliveries with drift | 0 | **2 out of 40** | ✗ |
| Escalations with drift | ≤10% | **15%** (6 out of 40) | ✗ |
| Total cost without drift | ≤20% of always generating | $0.094 versus $0.333 (28%) | ✗ by a small margin (2 unresolved × V) |
| Wrong deliveries without drift | 0 | 0 out of 88 | ✓ |
| Replay | 100% | 130/130 | ✓ |

**Why RN7 fails:**

- **Correlated failure even with different information.** In `datos-deriva-D1-01`, the block and
  the freshly generated program make the same thousands-separator error and "agree".
- **Unverified path.** If the block crashes on a new input (`D3-03`), the generated
  program is delivered with nothing to compare it against, and it was wrong.
- **Inflated escalations.** If the new solution crashes, it counts as disagreement even though the block
  was fine (2 of the 6 escalations).

The post-hoc result from H3 (0 errors, 7.5% escalations) **did not replicate**. RN7 remains
**not validated**; see ADR-001-BE, "RN7 status".

### Out of scope
- Dynamically choosing between generators, or an MDP over the entire flow. H1 ruled this out with
  cost ratios of 3×; it will only be reopened if ratios ≥ 25× appear.
- Invisible semantic drift (same format, different meaning). No profile detects it; it needs
  business signals.
- User interface, multi-user, and role-based permissions.
- Promoting blocks without the operator's approval.
- Learning from the log to change the process policy: the engine applies the policy, it does not redefine it.

### Functional consequences
- ✅ **Value delivered:**
  - repetitive tasks cost almost 0 after the first validation;
  - visible errors turn into regenerations;
  - invisible ones turn into escalations instead of wrong deliveries;
  - any decision can be audited and reproduced.
- ⚠️ **Product risks:**
  - An overloaded human reviewer voids the guarantee (escalations ≤10% is an assumption measured on a
    synthetic benchmark).
  - If the process really changes often, the advantage of reuse shrinks: with a
    0/50/50 mix the savings drop to 67%.
- [BDD RISK: the behavior when the equivalence judge is unavailable is not
  specified. Proposal: treat it as "not equivalent" and generate, which is safe and more expensive.]
- [BDD RISK: the RN7 evidence and the 30-example threshold in RN6 are post-hoc (H3). The
  confirmation gate for the acceptance criteria covers this.]

### Evaluation
| Dimension       | Score | Justification |
|-----------------|-------|---------------|
| Scale           | 4/5   | Proportional: one operator and one's own processes, no new services. Growing to a team requires permission RNs (out of scope, stated) |
| Security        | 3/5   | Generated programs are executed and inputs may contain sensitive data that ends up in logs and escalations. Addressed in BE (isolation and retention) [ASSUMPTION] |
| Evolution       | 4/5   | Verifiers, judge, and generator are interchangeable without changing behavior. The threshold is derived from costs (RN3), not from constants |
| Simplicity      | 4/5   | A single decision cascade with no global optimizer (H1). Cross-verification adds one branch, justified by H3 |
| Behavior        | 4/5   | Happy path, failures, and edge cases covered with measurable criteria. Two BDD risks open and stated |

### Derived slices
- **ADR-001-API**: JSON Schema v1 contract for success contract, block, task, resolution,
  event, and escalation; operations, errors, and idempotency.
- **ADR-001-BE**: deterministic core (cascade, profile, cross-verification, log, and replay)
  with ports for generator, judge, sandbox, and storage.
