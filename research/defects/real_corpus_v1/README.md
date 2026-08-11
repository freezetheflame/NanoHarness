# Real-Defect Corpus v1 Retrieval Snapshot

Status: candidate retrieval, blind-packet construction, and independent
A1/A2/A3 Agent Pass A are complete. Pass A remains unadjudicated, the targeted
human audit has not started, and no verified-defect or
operator-representativeness claim is supported yet.

## Authoritative sampling frame

The sampling frame is the complete first-parent history of each pinned default
branch between 2024-01-01 and 2026-06-30. Commit subjects are retained when the
case-insensitive whole-word expression `\b(fix|bug|regression|revert)\b`
matches. This produced:

| Repository | Pinned commit | Candidate commits | Selected |
|---|---|---:|---:|
| `freezetheflame/NanoHarness` | `43fd031f3d89becdd6b2c293d4ca84f1ad04ca65` | 2 | 2 |
| `langchain-ai/langgraph` | `5931a5f0b313feff24e2516a586c55601b868ac1` | 637 | 25 |
| `microsoft/autogen` | `027ecf0a379bcc1d09956d46d12d44a3ad9cee14` | 422 | 25 |
| `crewAIInc/crewAI` | `ba2dafdeda7944aae84057f3433a8347d05faf2c` | 539 | 25 |

The complete universe contains 1,600 candidates. Repository-stratified SHA-256
ranking selects 77: all two NanoHarness candidates and 25 from each external
repository. The private split contains 47 derivation and 30 validation
candidates; partition fields are absent from the human packet.

Selection does not imply inclusion. Documentation, formatting, dependency, and
non-Agent fixes deliberately remain available as human-screening negatives.

## Search API correction

The initially frozen GitHub Search lanes were executed for evidence discovery.
One LangGraph pull-request query reached GitHub Search's 1,000-result ceiling,
and the original long pull-request expression returned HTTP 422. The exact
superseded report is retained under `protocol_corrections/`. Before human coding,
the long expression was split to satisfy GitHub's Boolean-operator limit and
the complete pinned Git history replaced Search as the sampling frame.

The 81 retained Search response pages are stored in deterministic
`raw/search_pages.zip`; `raw/search_pages_index.json` records the path, size, and
SHA-256 of every original page. Search data remains supplementary and is not
used to calculate the 1,600-candidate universe.

## Human packet

- `evidence_packet.json`: 77 partition-blind candidates.
- `patches/`: one immutable patch per candidate.
- `coding_templates/H1/pass_a.json`: historical, superseded blank H1 template.
- `coding_templates/H2/pass_a.json`: historical, superseded blank H2 template.
- `selected_candidates.private.json`: original hidden split.
- `selected_candidates.enriched.private.json`: hidden split plus patch metadata.

The evidence packet SHA-256 is
`92fc19ca1f96e183dfb21087aae966445ce46e3ae75e81dbdbacfa1f74f82593`.
Neither coder may receive either private file or a machine pre-code.

## Formal independent Agent Pass A

The completed, bound A1/A2/A3 run is under
`formal/agent-review-v1/`. Each strict submission contains all 77 candidates.
The raw agreement report is
`formal/agent-review-v1/analysis/pass_a_agreement.json` (SHA-256
`38d4f5150f2ca233d18ec2d422f5293b736dc3662147b1d2b018f7aff7884ed0`).
It records unadjudicated inter-Agent agreement, not human inter-rater
reliability and not human-verified labels.

The targeted human packet is
`formal/agent-review-v1/analysis/human_audit_packet.json` (SHA-256
`8bfb7bc288de2890af2268978b0efa8cc69820081ee4151d8861f5586f4d369c`).
It contains 30 records, but every human-review field remains blank and the
human audit has not started. Unreviewed unanimous Agent judgments are not
individually human verified.

The planned audit response is a separate, mutable
`human_audit_response.json` draft stored outside the formal evidence tree.
Its shape is versioned by
`human_audit_response.schema.json`; the
public `human_audit.py` validator binds any response to the frozen packet's
digest, record IDs and order, copied selection reasons, and record-local
evidence IDs. A completed response is accompanied by a separate manifest that
binds the response filename and digest to the source digest. No response has
been created and the human audit has not started.

## Reproduction

Run the focused pipeline tests:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv\Scripts\python.exe -m pytest `
  tests\test_defect_coding.py `
  tests\test_real_defect_pipeline.py `
  tests\test_defects.py -q
```

Regenerate each `git_history.json` with `collect_git_history.py`, then run:

```powershell
.venv\Scripts\python.exe `
  research\defects\real_corpus_v1\build_candidate_index.py `
  --raw research\defects\real_corpus_v1\raw `
  --manifest research\defects\real_corpus_v1\manifest.json `
  --output research\defects\real_corpus_v1
```

Repository checkout paths are local inputs to `enrich_candidates.py` and are
never retained. After regeneration, verify every entry in `SHA256SUMS`.

## Claim boundary

The counts above describe retrieval and deterministic sampling only. They are
not verified defect counts, defect prevalence, Operator support, held-out
mapping, Mutation Score, Agent performance, or confirmation of Claim C2. C2
remains planned. Targeted human audit and adjudication, derivation-only human
Operator-Catalog signoff, A1/A2/A3 Pass B and its audit, held-out mapping,
readiness, and checksum gates remain pending.
