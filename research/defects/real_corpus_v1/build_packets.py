"""Generate a partition-blind evidence packet and human coding templates."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.defects.real_corpus_v1.pipeline import blind_packet, sha256_file


@dataclass(frozen=True)
class PacketOutputs:
    evidence_packet: Path
    h1_pass_a: Path
    h2_pass_a: Path
    agent_pass_a: Mapping[str, Path] = field(default_factory=dict)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _pass_a_template(
    coder_id: str,
    packet_digest: str,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "corpus_id": "agent-real-defects-v1",
        "pass_id": "pass_a",
        "coder_id": coder_id,
        "manual_version": "1.0",
        "packet_sha256": packet_digest,
        "entries": [
            {
                "defect_id": item["defect_id"],
                "decision": None,
                "exclusion_reason": "",
                "boundaries": [],
                "operator_ids": [],
                "trigger": "",
                "symptom": "",
                "root_cause": "",
                "impact": "",
                "evidence_ids": [],
                "rationale": "",
            }
            for item in candidates
        ],
        "completion": None,
    }


def _agent_pass_a_template(
    annotator_id: str,
    packet_digest: str,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    template = _pass_a_template(annotator_id, packet_digest, candidates)
    template["manual_version"] = "2.0"
    template["agent_provenance"] = {
        "protocol_id": "agent-review-v1",
        "annotator_id": annotator_id,
        "model_id": None,
        "prompt_sha256": None,
        "input_sha256": packet_digest,
        "artifact_revision": None,
        "started_at": None,
    }
    return template


def build_packets(
    selected: Sequence[Mapping[str, Any]],
    output_dir: Path,
    *,
    agent_annotators: Sequence[str] = (),
) -> PacketOutputs:
    """Write one blind packet and partition-blind blank judgments."""

    candidates = blind_packet(selected)
    evidence_packet = output_dir / "evidence_packet.json"
    _write_json(
        evidence_packet,
        {
            "schema_version": 1,
            "corpus_id": "agent-real-defects-v1",
            "manual_version": "1.0",
            "candidates": candidates,
        },
    )
    packet_digest = sha256_file(evidence_packet)
    h1 = output_dir / "coding_templates" / "H1" / "pass_a.json"
    h2 = output_dir / "coding_templates" / "H2" / "pass_a.json"
    _write_json(h1, _pass_a_template("H1", packet_digest, candidates))
    _write_json(h2, _pass_a_template("H2", packet_digest, candidates))
    agent_pass_a = {
        annotator_id: (
            output_dir
            / "formal"
            / "agent-review-v1"
            / "templates"
            / annotator_id
            / "pass_a.json"
        )
        for annotator_id in agent_annotators
    }
    for annotator_id, path in agent_pass_a.items():
        _write_json(
            path,
            _agent_pass_a_template(annotator_id, packet_digest, candidates),
        )
    return PacketOutputs(
        evidence_packet=evidence_packet,
        h1_pass_a=h1,
        h2_pass_a=h2,
        agent_pass_a=agent_pass_a,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.loads(args.selected.read_text(encoding="utf-8"))
    selected = payload.get("candidates", payload)
    outputs = build_packets(selected, args.output)
    print(json.dumps({
        "evidence_packet": str(outputs.evidence_packet),
        "packet_sha256": sha256_file(outputs.evidence_packet),
        "h1_pass_a": str(outputs.h1_pass_a),
        "h2_pass_a": str(outputs.h2_pass_a),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
