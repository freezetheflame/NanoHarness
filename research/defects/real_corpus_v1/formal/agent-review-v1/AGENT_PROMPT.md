# Formal independent defect annotation: Pass A

You are one of three independent formal annotators operating in a fresh, isolated context. Perform the complete 77-item Pass A annotation under frozen protocol `agent-review-v1`. Do not rely on memory or context from any earlier task.

The dispatcher must create this task with model `gpt-5.6-sol`, reasoning effort `high`, and `fork_turns: "none"`. The actual spawn message payload must be the exact bytes of this `AGENT_PROMPT.md`; it must not be quoted, prefixed, suffixed, templated, or otherwise transformed.

Before creating any annotator task, the dispatcher must run the coordinator batch gate and save its complete `batch_preflight` object. This gate proves that all three canonical outputs are absent at one recorded timezone-aware instant:

```text
python research/defects/real_corpus_v1/agent_dispatch_cli.py preflight-batch --binding research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json --checked-at <timezone-aware-batch-checked-at>
```

After all three task-creation calls and all three individual start preflights return, the dispatcher creates `DISPATCH_RECORD.json` with `create-dispatch-record`, passing the exact batch object as `--batch-preflight-json` and, for each `--assignment`, these six values in order: annotator ID, canonical task name, returned task ID, timezone-aware task start time, saved start-preflight `checked_at`, and saved start-preflight `workspace_snapshot_sha256`. Do not synthesize or replace any receipt value.

For every assignment, the three timezone-aware timestamps must satisfy `batch_preflight.checked_at <= task started_at <= start_preflight.checked_at` by absolute instant after offset normalization. Equality is valid; any reversed order invalidates record creation and validation.

## Resolve your assignment from your canonical task name

Inspect your own canonical task name and use its final path component. Apply exactly this fixed mapping:

| Canonical task-name suffix | Annotator ID | Assigned template | Sole permitted output |
| --- | --- | --- | --- |
| `formal_a1` | `A1` | `research/defects/real_corpus_v1/formal/agent-review-v1/templates/A1/pass_a.json` | `research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A1.json` |
| `formal_a2` | `A2` | `research/defects/real_corpus_v1/formal/agent-review-v1/templates/A2/pass_a.json` | `research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A2.json` |
| `formal_a3` | `A3` | `research/defects/real_corpus_v1/formal/agent-review-v1/templates/A3/pass_a.json` | `research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A3.json` |

The suffix must match exactly one row. If it does not, refuse the task with `unknown canonical task name`; do not guess an annotator, template, or output path. The dispatcher sends these exact prompt bytes to every annotator: no annotator ID, task-specific instruction, or output path is substituted into the message.

## Mandatory byte-level preflight

The external binding is `research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json`, and the eventual external record is `research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_RECORD.json`. Before reading annotation inputs, choose exactly one start command below from your canonical mapping row and run it from the NanoHarness repository root:

```text
python research/defects/real_corpus_v1/agent_dispatch_cli.py preflight --binding research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json --task-name formal_a1 --expected-annotator A1 --expected-template research/defects/real_corpus_v1/formal/agent-review-v1/templates/A1/pass_a.json --expected-output research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A1.json --phase start --checked-at <timezone-aware-A1-start-preflight-time>
python research/defects/real_corpus_v1/agent_dispatch_cli.py preflight --binding research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json --task-name formal_a2 --expected-annotator A2 --expected-template research/defects/real_corpus_v1/formal/agent-review-v1/templates/A2/pass_a.json --expected-output research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A2.json --phase start --checked-at <timezone-aware-A2-start-preflight-time>
python research/defects/real_corpus_v1/agent_dispatch_cli.py preflight --binding research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json --task-name formal_a3 --expected-annotator A3 --expected-template research/defects/real_corpus_v1/formal/agent-review-v1/templates/A3/pass_a.json --expected-output research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A3.json --phase start --checked-at <timezone-aware-A3-start-preflight-time>
```

Stop without reading annotation inputs or writing output unless it returns `"valid": true`. Save its `binding_sha256`, `freeze_payload_revision`, `workspace_snapshot_sha256`, and `checked_at` exactly. Report these receipt values to the dispatcher, then wait for `DISPATCH_RECORD.json`, validate it, and confirm your exact saved start receipt appears under your assignment before annotating. The preflight verifies the top-level and nested checksum manifests, frozen Git payload, exact model/configuration/fork policy, prompt, protocol, packet, all three templates, all 77 patches, and revision-bound Paper inputs. Only read paths and Paper revision bytes approved by this binding. Do not substitute a working-tree Paper file or any unbound content. Every patch SHA-256 must have passed preflight before you use that patch.

## Frozen inputs you must read

Locate the NanoHarness and AgentMutationTestingPaper repositories in the provided workspace. Before judging any item, read all of the following:

1. The binding-approved `research/defects/real_corpus_v1/formal/agent-review-v1/protocol.json` in NanoHarness.
2. The binding-approved Paper manual 2.0 revision and path `experiments/design/DEFECT_CODING_MANUAL.md`. Read its revision-bound bytes (for example, with `git show <binding revision>:<path>`), not a working-tree revision.
3. The binding-approved Paper instructions at path `experiments/defects/real-corpus-v1/AGENT_ANNOTATOR_INSTRUCTIONS.md`, also using only its revision-bound bytes.
4. The binding-approved NanoHarness `research/defects/real_corpus_v1/evidence_packet.json`.
5. Only the assigned binding-approved canonical template selected by the mapping above.
6. All 77 binding-approved patches in `research/defects/real_corpus_v1/patches/`, matching the 77 `defect_id` values in the assigned template. Do not omit an item.

Verify that the SHA-256 of the evidence-packet bytes equals both the protocol `packet_sha256` and the assigned template `packet_sha256` before annotating. Stop without writing output if this or any other frozen identity check fails.

## Annotation and output requirements

- Independently annotate every one of the 77 template entries according to manual 2.0 and the Paper annotator instructions. Preserve the template's schema, entry order, IDs, and all unrelated fixed fields.
- Write exactly one file: the sole permitted output from your mapping row. Do not modify the assigned template in place.
- This is Pass A. Every `operator_ids` value must remain `[]`. Do not perform operator mapping or projection.
- Every `evidence_ids` value must contain only evidence IDs present for that same defect in `evidence_packet.json`. Do not invent IDs or cite a patch path as an evidence ID.
- Populate `agent_provenance.protocol_id`, `annotator_id`, `model_id`, `prompt_sha256`, `input_sha256`, `artifact_revision`, and `started_at` from your resolved identity, actual execution, and the frozen binding/protocol. Use `gpt-5.6-sol` as `model_id`; `input_sha256` is the verified packet SHA; `artifact_revision` is the saved binding `freeze_payload_revision`. Copy `prompt_sha256` from the frozen protocol and verify it equals the binding prompt digest. Do not embed or recompute a prompt hash from this file, because the prompt cannot self-contain its own digest.
- Copy the exact frozen ISO string `started_at` from your assignment in the validated dispatch record into `agent_provenance.started_at`; an equivalent instant written with a different offset or spelling is invalid. The schema has no separate `completion.independent_started_at`: `agent_provenance.started_at` is the independent start. Record the actual finish as timezone-aware `completion.completed_at`, whose absolute instant must be greater than or equal to the independent start; equality is valid. Set `completion.independent` to `true` and copy the verified packet SHA to `completion.packet_sha256`. Do not use the protocol's common dispatch timestamp as your independent start time.
- The frozen model configuration is reasoning effort `high`. The output schema has no reasoning-effort field, so do not add one.
- Validate the completed JSON against the assigned canonical shape and Pass A constraints before finishing.

Concurrent execution means another canonical A1/A2/A3 output may exist at your start or end gate. Its pathname is allowlisted solely as a concurrent-write exception: do not open, read, inspect, summarize, modify, or write another annotator's file. This is not OS-level author attribution. Your own output remains bound by strict coder, template, and provenance validation. The workspace gate records path, size, and `mtime_ns` for every pre-existing untracked file without reading its bytes; any metadata change, deletion, rename, or non-allowlisted addition fails the end gate.

Immediately before final submission, rerun your same preflight command with `--phase end` instead of `--phase start`, add `--dispatch-record research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_RECORD.json`, and append `--expected-binding-sha256 <saved-binding_sha256> --expected-freeze-payload-revision <saved-freeze_payload_revision> --expected-start-workspace-sha256 <saved-workspace_snapshot_sha256>`. Stop if it does not return `"valid": true`; this detects any mid-run replacement.

Then run exactly your mapped frozen submission-validation command:

```text
python research/defects/real_corpus_v1/agent_dispatch_cli.py validate-submission --binding research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json --dispatch-record research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_RECORD.json --protocol research/defects/real_corpus_v1/formal/agent-review-v1/protocol.json --expected-annotator A1 --template research/defects/real_corpus_v1/formal/agent-review-v1/templates/A1/pass_a.json --submission research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A1.json --expected-binding-sha256 <saved-binding_sha256> --expected-freeze-payload-revision <saved-freeze_payload_revision> --expected-start-workspace-sha256 <saved-workspace_snapshot_sha256>
python research/defects/real_corpus_v1/agent_dispatch_cli.py validate-submission --binding research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json --dispatch-record research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_RECORD.json --protocol research/defects/real_corpus_v1/formal/agent-review-v1/protocol.json --expected-annotator A2 --template research/defects/real_corpus_v1/formal/agent-review-v1/templates/A2/pass_a.json --submission research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A2.json --expected-binding-sha256 <saved-binding_sha256> --expected-freeze-payload-revision <saved-freeze_payload_revision> --expected-start-workspace-sha256 <saved-workspace_snapshot_sha256>
python research/defects/real_corpus_v1/agent_dispatch_cli.py validate-submission --binding research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_BINDING.json --dispatch-record research/defects/real_corpus_v1/formal/agent-review-v1/DISPATCH_RECORD.json --protocol research/defects/real_corpus_v1/formal/agent-review-v1/protocol.json --expected-annotator A3 --template research/defects/real_corpus_v1/formal/agent-review-v1/templates/A3/pass_a.json --submission research/defects/real_corpus_v1/formal/agent-review-v1/pass_a/A3.json --expected-binding-sha256 <saved-binding_sha256> --expected-freeze-payload-revision <saved-freeze_payload_revision> --expected-start-workspace-sha256 <saved-workspace_snapshot_sha256>
```

Do not report completion unless this command returns `"valid": true`.

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
