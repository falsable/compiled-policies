# compiled-policies

Research on compiling natural-language policies into validated deterministic programs ("blocks") that
are reused through the cascade identical text → equivalence judge → parameters → generate. Status and
verdicts of H1–H5 are in `README.md`; decisions in `docs/adr/ADR-001-{FN,API,BE}.md`; lessons in
`docs/RETROSPECTIVE.md`; prior art in `docs/PRIOR-ART.md`.
Docs and commits in English (Aldemar, 2026-09-27). Code identifiers, comments, prompts and data keys
stay in Spanish: the prompts are part of the golden-corpus contract (the replay compares them byte for byte).

## Commands
- No manifest. Dependencies: `pip install pydantic numpy "pandas<3" scipy matplotlib jsonschema`
  (the README omits `jsonschema`, used by `contracts/validar.py` and `corpus/grabar.py`). On Windows:
  a local `.venv` (`uv venv .venv`) and `PYTHONUTF8=1`, or `contracts/validar.py` fails with cp1252.
- Run everything from the repo root (the scripts import `benchmark` as a module).
- Free gates ($0), in this order:
  - `python -m benchmark.verificar benchmark/out` — benchmark self-test;
  - `python contracts/validar.py` — contract tests of schema v1;
  - `python corpus/grabar.py reproducir` — golden-corpus replay, 130/130 on the recording machine
    (~43 s); 103/130 on Windows on 2026-09-27, cause not found (bead 698.1).

## Gotchas
- **These scripts spend money:** `experimento_kappa.py`, `reutilizacion.py compilar|evaluar`,
  `comprobaciones.py compilar_b|evaluar`, `corpus/grabar.py grabar`,
  `reutilizacion_parametros.py parametrizar|evaluar` and `negativos_parametros.py` call `claude -p`
  (list price); `juez_jev.py` calls Jev through OpenRouter (`OPENROUTER_API_KEY`). Always with a budget
  (`--presupuesto-usd` where it exists), and only with a yes from Aldemar in the turn. `analizar`,
  `reproducir`, `kill_test.py` and `graficar.py` do not call models.
- Generated programs (`sol.py`) run as a subprocess in a tmpdir, with no Docker or real sandbox.
- The replay compares each prompt with the recorded one and fails with "divergencia" if it changed:
  touching the pipeline prompts invalidates `corpus/v1/`.
- `corpus/grabar.py` is disposable; the corpus is what is kept. The future Rust core must reproduce
  `corpus/v1/esperado.jsonl` (ADR-001-BE).

## Conventions
- The kill criterion is written in the README **before** running. Anything decided after seeing the
  data is marked `[post-hoc]` and does not count as confirmation. Assumed numbers: `[ASSUMPTION]`.
- Calibrate with the cheapest arm (~$1) before any expensive run.
- Artifacts always by hash (sha256). Costs in list-price USD; the `_eur` fields are 1:1.

## Never
- Overwrite `exp/`, `corpus/v1/` or `resultados/`: they are the evidence behind the README numbers. A
  new run goes to a new folder in `exp/` (like `piloto_v2`).

Task tracking: beads — run `bd prime` for the workflow.
