# Retrospective — Compiled Policies framework (step 7)

From September 25 to 27, 2026. Total spend ≈ $64 (list price, golden corpus included). Result: ADR-001
(FN + API + BE) and the v1 contract.

## Was the initial diagnosis correct?

**No; it pivoted twice, both times because of data:**

| Moment | We believed | The data said | Pivot |
|---|---|---|---|
| Idea | Jev as the trigger for a Petri net/Markov chain that dynamically chooses | — | Framing: "flexible, cheap, and auditable at the same time" |
| H1 | An MDP between cheap and expensive models saves money | There's coupling (δ = 0.23), but the real Opus/Haiku ratio is 3.2×: a fixed rule wins | Drop dynamic selection between generators |
| H2 | Reusing blocks saves money if a classifier decides coverage | −84% cost, 100% accuracy; the judge scored 40/40 | Core of the framework |
| H3 | Runtime checks catch drift | Invariants and N-version: 0%. 5-example profile: 34% false alarms. What worked was post-hoc | Cross-verification with different information + human, pending confirmation in shadow |

## Assumptions that turned out false

1. **"€ per task solved" as the main metric.** It rewards failing cheaply. Replaced with total
   cost per task (with V).
2. **25× cost ratio between tiers.** The effective ratio was 3×, because the cheap model spends
   more tokens.
3. **"A cheap plan is better than none".** It scores the same as not planning and costs more.
4. **The benchmark measured something.** It first had a ceiling (12/12 with no plan) and then
   was missing information (the list of approved options). The solvability self-test was added
   for that reason.
5. **"Two independent versions catch errors".** If they come from the same process, they fail
   the same way.
6. **"A 5-example profile defines the domain of validity".** It gives 34% false alarms.
7. **"Cross-verification with different information avoids the correlated failure"** (H3,
   post-hoc). Replicating it with the design locked in: 2 errors and 15% escalations. The block
   and the new program repeated the same bug. **The post-hoc result didn't hold up.** That is
   exactly what the gate was for.

## Where time (and money) was lost

- **Running on an uncalibrated benchmark:** invalid v1 pilot on `negocio`, ≈ $3.6.
  Calibrate first with the cheap arm (~$1) before any expensive run.
- **Not saving the artifacts** (programs and outputs) in the early experiments. The golden
  corpus had to be recorded separately ($13.19).
- **Formats the tool didn't expect** (CSV with ';' in the input description): 5 tasks had to be
  repeated.

## Reusable heuristics

1. **Death criterion before running**, written down in the README. Anything decided after
   seeing the data is marked `[post-hoc]` and doesn't count as confirmation.
2. **Benchmark self-test before spending:**
   - the target passes;
   - the perturbations fail;
   - reproducible by seed;
   - solvable with what's visible;
   - traps are active.
3. **Calibrate difficulty with the cheapest arm** until far from both the ceiling and the floor.
4. **Strong, tuned baselines** (router with optimal τ, FrugalGPT-style cascade): otherwise the
   advantage is an artifact.
5. **Measure the effective cost ratio per call**, not the price per token.
6. **Always save artifacts by hash.** Without them there's no replay and no golden corpus.
7. **The diversity that catches errors comes from different information**, not from sampling
   again.

## How to pick this back up in six months

- `README.md`: §3 simulation, §5–§6 H1, §7 H2, §8 H3. Each section with criteria, results, and
  verdict.
- `docs/adr/ADR-001-{FN,API,BE}.md`: the decisions and why.
- `contracts/v1/`: the executable contract (`python contracts/validar.py`).
- `exp/*/resumen.json`: the exact numbers behind every sentence.

## Should the problem-solving framework itself change?

**Yes, two additions to step 3 (research) and step 6 (execution):**

- **Step 3:** if the research includes experiments, the death criterion is written *before*,
  together with the timebox.
- **Step 6:** "calibrate with the cheap option before the expensive one" as a Shisa Kanko check
  before any run that costs money.
