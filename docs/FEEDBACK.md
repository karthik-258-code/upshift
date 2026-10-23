# Platform feedback from building Upshift

Everything below was hit while building and running Upshift between 19 and 23 October
2026, with reproduction details. Where we worked around something, the workaround is in
the code and linked. We think most of these are cheap for the platform to fix and would
remove real friction for agent builders.

## Nebius Token Factory and the Nemotron family

### 1. Model IDs are case-sensitive and inconsistently cased

The hackathon brief listed `nvidia/nvidia-nemotron-3-nano-30b-a3b`; the API only accepts
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`. Across the family the casing differs:
`nvidia/Nemotron-3_5-Lightning`, `nvidia/nemotron-3-super-120b-a12b`,
`nvidia/Nemotron-3-Ultra-550b-a55b`. A wrong-case ID returns a generic 404-style error.

*Suggestion:* accept IDs case-insensitively, or at least return "did you mean" in the error,
and publish one canonical table. We now probe every model on first use
(`scripts/probe_models.py`) and refuse to route to an ID that did not answer.

### 2. Reasoning is on by default, undocumented to switch off, and billed against `max_tokens`

All four Nemotron tiers reason by default. The reasoning tokens count against `max_tokens`,
so a schema-constrained request for a 2k-token JSON answer can return `finish_reason=length`
with empty content after spending the whole budget on thinking. The switch is the vLLM-style
`extra_body={"chat_template_kwargs": {"enable_thinking": false}}`, which is not in the
OpenAI-compatible API documentation we found.

*Suggestion:* document the switch prominently; support the standard `reasoning_effort`
parameter; report reasoning tokens separately in `usage` and do not count them against the
caller's `max_tokens` (or document that they do). Our client applies the switch by default
and agents opt into thinking explicitly (`upshift/llm/client.py`).

### 3. Latency variance on Ultra and Lightning, and what it does to agents

On 22 October evening, deep-reasoning calls to Ultra (about 19k-token budgets) regularly
exceeded 120 seconds; several took 4 to 8 minutes including retries. Lightning ingesting a
400k-character changelog occasionally exceeded 120 seconds too. An agent with a fixed
timeout retries the identical slow request and burns both time and money.

*Suggestion:* publish p50/p95 latency per model and per output length; consider a
`Retry-After`-style hint on 5xx/timeouts; recommend streaming for long generations in the
docs. We now scale the per-call timeout with the output budget (up to 15 minutes).

### 4. Repetition collapse at temperature 0 on long outputs

Both Super and Ultra, asked for whole-file rewrites at temperature 0, sometimes degenerate
into repetition (`from typing import Annotated, Annotated as AnnotatedType, Annotated as
AnnotatedAlias ...`) until the token cap. Re-sending the same prompt reproduces it exactly.
Super did this often enough that we stopped routing code edits to it.

*Suggestion:* expose `repetition_penalty` / `frequency_penalty` in the docs with recommended
values for code generation, or apply a sane default server-side. Our workaround: never
retry with the same input (temperature ramp, prompt notice), cap output to the size of the
files being edited, and abort after four no-op attempts.

### 5. Strict `json_schema` works everywhere: please say so

Good news that deserves documentation: `response_format={"type": "json_schema",
"json_schema": {"strict": true, ...}}` worked on all four tiers in our probes, as did
parallel tool calls. We rely on it for the Historian, Reviewer, Auditor and research planner.
One caveat: with thinking left on, strict mode plus a small `max_tokens` is the fastest way
to get an empty answer (see 2).

### 6. Output token limits per model are not published

Lightning truncated structured output at 12k tokens in one configuration and accepted 24k in
another; we could not find the per-model maximum output length in the docs and had to
discover it by retrying.

*Suggestion:* add max output tokens (and the reasoning budget interaction) to each model
card.

### 7. Cost visibility

We compute cost from `usage` tokens and the published per-million prices. A per-response
cost field, or a usage API with per-key daily spend, would let agents enforce budgets
exactly rather than approximately. Our budget guard (`upshift/core/budget.py`) is an
estimate for that reason.

### 8. What worked well

Lightning's 1M context is the reason the Historian can read a whole changelog instead of
chunking and losing cross-references; at $0.06/M input it costs a third of a cent per
dossier. Ultra, given the installed API surface and the real traceback, writes correct
migrations for pydantic v2, SQLAlchemy 2.x and Flask 3 for about ten cents each. The
OpenAI-compatible surface let us use the standard SDK unchanged.

## Nebius Serverless AI Jobs and compute

### 9. No native Windows CLI

The CLI installation page documents Linux and macOS; Windows users are pointed at WSL. For
REST-first integration that is fine (we implemented the service-account JWT exchange in
Python and never needed the CLI), but the `nebius iam get-access-token` shortcut is CLI-only.

*Suggestion:* a Windows binary, or a documented REST path to a short-lived user token.

### 10. Jobs: moving files in and out

Injected files are capped at 64 KiB each; output reaches only logs or mounted volumes. There
is no documented REST endpoint for job logs (the CLI has `nebius ai job logs`). We built the
executor around object storage with presigned URLs instead (`upshift/sandbox/nebius_job.py`).

*Suggestion:* a REST logs endpoint; a larger injected-file limit or a documented "upload a
directory" primitive; an "artifacts" primitive to collect files from the container when the
job ends. These three would make Jobs a natural sandbox for coding agents.

### 11. CPU-only presets and regional availability

The Jobs creation form in `eu-west2` listed a single GPU platform (B300, $9.53/h); a CPU
option at about $0.10/h is reachable in the UI but we could not find CPU platform and preset
identifiers in the documentation to use them from the API. The VM creation page in the same
project listed only GPU platforms, several marked "different region".

*Suggestion:* document CPU platform/preset IDs per region, and show them in the public price
list, since agent sandboxes are CPU workloads.

### 12. Credits and hosting

Token Factory credits do not apply to compute. A hackathon team therefore cannot host a
small always-on demo (a CPU VM is about $130 for the judging period) without a card. We
shipped the demo as a recorded replay on GitLab Pages instead.

*Suggestion:* a small CPU-compute allowance alongside inference credits for hackathons, or
a free tier for a 2-vCPU VM.

## Tavily

### 13. Extract on large changelog pages

Tavily Extract returned 120k to 400k characters for pydantic, SQLAlchemy and Flask changelog
pages reliably, which is what makes whole-changelog ingestion possible. Two notes: the
`raw_content` for GitHub release pages is paginated by GitHub itself (we got the latest
releases only), and we saw occasional `RemoteDisconnected` on search during long runs, which
our cache and retry absorb.

*Suggestion:* an option to follow "older releases" pagination for GitHub releases, and
documented rate/latency expectations for Extract on very large pages.

## Documentation

- The OpenAI-compatible endpoint docs should carry the Nemotron-specific notes above
  (thinking switch, output limits, ID casing) in one place.
- The Serverless Jobs quickstart would benefit from a complete example that injects a
  script, runs it, and retrieves a result file.

## Summary table

| # | Area | Impact on agents | Cost to fix (our guess) |
| --- | --- | --- | --- |
| 1 | Model ID casing | Silent 404s on first integration | Low |
| 2 | Reasoning default and accounting | Empty structured outputs, wasted spend | Low (docs), medium (API) |
| 3 | Latency variance | Timeouts, duplicate requests | Low (docs) |
| 4 | Repetition at temp 0 | Runaway outputs, wasted spend | Medium |
| 6 | Output limits undocumented | Trial-and-error | Low |
| 7 | Cost visibility | Approximate budgets | Medium |
| 10 | Jobs I/O primitives | Sandbox integration needs object storage | Medium |
| 11 | CPU presets undocumented | Cannot plan CPU workloads | Low |
| 12 | Compute credits | No hosted demos for teams | Policy |
