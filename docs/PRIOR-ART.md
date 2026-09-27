# Prior art for H2 (B17, 2026-09-27)

Question: does the cascade "identical text → equivalence judge → generate" (§7, §11 of the README)
already exist, or are there pieces to take? Channels: `gh search repos` (8 phrasings), skills
catalog (`npx skills find "semantic cache"`: nothing specific), and a literature agent (Sonnet,
~125k tokens). Every arXiv paper was checked against its page (title and date); what wasn't
verified is flagged.

## Verdict

**This isn't a new invention, and it doesn't already exist as-is either.** The central idea (reuse
a saved artifact if a cheap judge says it applies; otherwise, generate with the expensive model)
is semantic caching plus a cascade, published between 2023 and 2026. No prior work was found
applied to *validated deterministic programs* generated from *natural-language specifications*
with the "cosmetic vs. substantive" judgment. Decision: **build the integration, adapting proven
pieces**. Our own numbers (−84% cost, 40/40) are consistent with published ones (APC −50% cost;
FrugalGPT up to −98%).

| Piece | What to take | Source (verified) | Verdict |
|---|---|---|---|
| Fixed 0.8 threshold | Per-block calibrated threshold with a user-chosen error bound | vCache, arXiv 2502.03771 (2025) | ADAPT |
| Threshold guarantee | Cascade with distribution-free guarantees | Conformal Cascade, arXiv 2607.25018 (2026) | ADAPT (alternative to vCache) |
| Cases near the threshold | Verify off the critical path with a better judge and promote | Krites, arXiv 2602.13165 (2026) | ADAPT |
| Cheap judge with escalation | Accept if confident, escalate if not | JEV-as-a-Judge, arXiv 2609.26550 (2026) | ADOPT the pattern: a p-band that escalates to Haiku before generating |
| Judge signal as noisy | Noisy verification handled with a router, not a hard threshold | AutoMix, arXiv 2310.12963 (2023) | ADAPT |
| Fallback against silent failures | Decide equivalence by running generated tests, not by reading | SemaDiff, arXiv 2607.13111 (2026); EquiBench, arXiv 2502.12466 (2025): LLMs reason poorly about program equivalence | ADAPT: tests derived from the new specification run against the old block before reuse |
| Changes that are just parameters | Separate template from parameters and reuse with new parameters | Agentic Plan Caching, arXiv 2506.14852 (2025) | ADAPT: several substantive benchmark cases (S1_umbrales, S1_umbral_z, S2_iva_ventana, S4_categoria) are parameter changes; they're regenerated today |
| Judge as entailment | Requirement change framed as NLI | arXiv 2511.00262 (2025); Arora et al., RE'15 | ADAPT if it's necessary to explain which rule changed |
| Judge calibration | Linear probes instead of verbalized probability | arXiv 2512.22245 (2025) | Only for the LLM judge; Jev already gives a probability |

**Independent warning about Jev:** `github.com/priorbench/jev` (preregistered evaluation, 5,721
calls) reports that with a wrong criterion description, accuracy drops to 16.7%, worse than
chance. The 40/40 in §11 depends on the wording of the criteria: changing it requires
re-measuring.

**Doesn't apply:** GPTCache (fixed threshold; stores responses, not programs), MeanCache
(multi-user privacy), ExpeL (heuristics, not artifacts), Voyager (only the skill-library
pattern).

**Not verified:** "MiniCache/GenCache" (the cited arXiv paper, 2607.20507, is a different paper:
CacheSpec); the GenCache PDF (NeurIPS 2025); the calibration note from beri.net; TypeSafe's own
figures.
