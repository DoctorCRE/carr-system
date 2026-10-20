"""Synthetic exercise facts shared by board behavior and payload tests."""


def exercise_fields(source="1" * 40, repo="jbookout/carr-system"):
    return {"repo": repo, "pr_phase": "Merged", "merge_sha": source,
            "delivery_source_sha": source,
            "delivery_target": "https://exercise.example.invalid/target",
            "delivery_workflow": "synthetic outcome",
            "consumer_proof": {"schema": "carr-consumer-proof.v1", "outcome": "pass",
                               "repo": repo, "source_sha": source,
                               "target": "https://exercise.example.invalid/target", "workflow": "synthetic outcome",
                               "evidence_ref": "synthetic-receipt", "observed_at": "2026-10-01T12:00:00Z"}}
