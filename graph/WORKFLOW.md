# Repeatable operator prompt · version 2026.09.18

For the newer four-stage Pi path, use [preservation safeguards and recovery](preservation-workflow.md). The prompt below describes the older general extractor path; cached-job reuse applies to `extract.py`, not the historical fixed-root Pi runners.

Use this prompt with a coding assistant to operate the existing harness. A prompt is not a sandbox; enforcement lives in the reviewed code and Docker configuration.

> Build a candidate Alexandria knowledge graph using `Alexandria/graph/README.md` and its existing harness. Read applicable repository instructions first. Keep PR #1 unmerged and preserve unrelated edits.
>
> Confirm the requested commit, exact document paths and new output directory. Snapshot only those committed sources. Start with an offline plan and the container security probe. Do not mount the working repository, home directory, credentials or Docker socket into the worker. Do not add shell/tool execution to model responses.
>
> Build the structural graph. If external extraction is explicitly authorized, use only the configured zero-price model and approved document scope through `extract.py`. Missing model, credentials or privacy approval blocks external calls, not local structural work. Never choose a paid fallback, weaken privacy controls, or bypass a failed safety check. Never print credentials or private source text in general logs.
>
> Incorporate the planned [Humanizer and ambiguity stages](editorial-workflow.md) before expanding this workflow. Mark unresolved statements without choosing an interpretation. Apply Humanizer to derived prose while preserving source evidence, uncertainty and markers, then check meaning again. The newer bounded Pi path implements these stages; record which runner actually executed them. The general `extract.py` path alone does not.
>
> Validate returned assertions and build a separate candidate using the identical source commit. Keep proposals, inferred claims and human acceptance distinct. Exact quotation matches prove location, not truth. Do not merge entities merely because names match across documents.
>
> Inspect sample entity-to-source navigation and review the meaning of sampled assertions against original passages. Report omissions, incorrect relationships, ambiguous names and contradictions. Compare selected questions with existing retrieval before claiming improvement. Do not automatically publish candidates or alter the active MCP/LanceDB index.
>
> On quota exhaustion or interruption, retain checkpoints and report what remains. On rerun, reuse verified cached jobs. Do not delete prior evidence or results to make a failing run pass.
>
> Finish with a concise receipt: source revision and scope, code/config identity, executed checks, external requests, actual model/provider where available, candidate artifact paths, limitations, and the next decision. A successful graph build is not a claim that the graph is good.
