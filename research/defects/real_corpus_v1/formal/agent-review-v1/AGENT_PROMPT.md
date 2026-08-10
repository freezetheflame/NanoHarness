# Formal independent defect annotation: Pass A

You are one of three independent formal annotators operating in a fresh, isolated context. Perform the complete 77-item Pass A annotation under frozen protocol `agent-review-v1`. Do not rely on memory or context from any earlier task.

## Resolve your assignment from your canonical task name

Inspect your own canonical task name and use its final path component. Apply exactly this fixed mapping:

| Canonical task-name suffix | Annotator ID | Assigned template | Sole permitted output |
| --- | --- | --- | --- |
| `formal_a1` | `A1` | `research/defects/real_corpus_v1/formal/agent-review-v1/templates/A1/pass_a.json` | `research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A1.json` |
| `formal_a2` | `A2` | `research/defects/real_corpus_v1/formal/agent-review-v1/templates/A2/pass_a.json` | `research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A2.json` |
| `formal_a3` | `A3` | `research/defects/real_corpus_v1/formal/agent-review-v1/templates/A3/pass_a.json` | `research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A3.json` |

The suffix must match exactly one row. If it does not, refuse the task with `unknown canonical task name`; do not guess an annotator, template, or output path. The dispatcher sends these exact prompt bytes to every annotator: no annotator ID, task-specific instruction, or output path is substituted into the message.

## Frozen inputs you must read

Locate the NanoHarness and AgentMutationTestingPaper repositories in the provided workspace. Before judging any item, read all of the following:

1. `research/defects/real_corpus_v1/formal/agent-review-v1/protocol.json` in NanoHarness.
2. Paper manual 2.0 at exact Paper revision `91674e63aab0cf9da599ae52f341cf99004ddd4a`, path `experiments/design/DEFECT_CODING_MANUAL.md`. Read the revision-bound bytes (for example, with `git show <revision>:<path>`), not a different working-tree revision.
3. Paper instructions at the same exact Paper revision, path `experiments/defects/real-corpus-v1/AGENT_ANNOTATOR_INSTRUCTIONS.md`, also using revision-bound bytes.
4. NanoHarness `research/defects/real_corpus_v1/evidence_packet.json`.
5. Only the assigned canonical template selected by the mapping above.
6. All 77 patches in `research/defects/real_corpus_v1/patches/`, matching the 77 `defect_id` values in the assigned template. Do not omit an item.

Verify that the SHA-256 of the evidence-packet bytes equals both the protocol `packet_sha256` and the assigned template `packet_sha256` before annotating. Stop without writing output if this or any other frozen identity check fails.

## Annotation and output requirements

- Independently annotate every one of the 77 template entries according to manual 2.0 and the Paper annotator instructions. Preserve the template's schema, entry order, IDs, and all unrelated fixed fields.
- Write exactly one file: the sole permitted output from your mapping row. Do not modify the assigned template in place.
- This is Pass A. Every `operator_ids` value must remain `[]`. Do not perform operator mapping or projection.
- Every `evidence_ids` value must contain only evidence IDs present for that same defect in `evidence_packet.json`. Do not invent IDs or cite a patch path as an evidence ID.
- Populate `agent_provenance.protocol_id`, `annotator_id`, `model_id`, `prompt_sha256`, `input_sha256`, `artifact_revision`, and `started_at` from your resolved identity, actual execution, and the frozen protocol. Use `gpt-5.6-sol` as `model_id`; `input_sha256` is the verified packet SHA; `artifact_revision` is the protocol's pre-freeze NanoHarness code/artifact revision. Copy `prompt_sha256` from the frozen protocol. Do not embed or recompute a prompt hash from this file, because the prompt cannot self-contain its own digest.
- Record the actual start of your independent work as timezone-aware `agent_provenance.started_at` (the independent_started_at) and the actual finish as timezone-aware `completion.completed_at`. Set `completion.independent` to `true` and copy the verified packet SHA to `completion.packet_sha256`. Do not use the protocol's common dispatch timestamp as your independent start time.
- The frozen model configuration is reasoning effort `high`. The output schema has no reasoning-effort field, so do not add one.
- Validate the completed JSON against the assigned canonical shape and Pass A constraints before finishing.

## Blindness and isolation restrictions

Do not read, search, list contents from, infer from, or use any of the following:

- any `private` input, including private selected-candidate material;
- any hidden selection, ranking, or partition information;
- either of the other two Agent templates, or any other Agent's output; only your assigned template and output are permitted;
- formal analysis or derived formal review artifacts other than this prompt, the frozen protocol, your assigned template, and your assigned output;
- the Operator Catalog, operator taxonomy mapping, or mapping projection;
- legacy H1, H2, or H2-CODEX templates, annotations, outputs, or reports;
- extracted handoff material or prior annotator summaries;
- machine precode, machine-precode scripts or outputs, or any machine-produced pre-annotation.

Do not delegate the annotation or spawn another agent. Do not communicate with, request help from, or summarize findings for another annotator. Do not alter any other file. Do not commit, stage, merge, push, open a PR, or update an artifact pointer. After the sole permitted JSON is valid and complete, report only concise completion status to the dispatcher; do not include annotation contents or a cross-annotator summary.
