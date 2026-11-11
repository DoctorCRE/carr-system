export type CoordinateReview = {
  schema: "tour-coordinate-review.v1";
  property_id: string;
  as_of: string;
  status: "unknown" | "candidates";
  reason: "no_observed_candidates" | null;
  property_evidence: PropertyEvidenceRow[];
  candidates: CoordinateCandidate[];
};

export type PropertyEvidenceRow = {
  field: string;
  value: unknown;
  review_state: string;
  as_of: string;
  effective_from: string;
  effective_to: string | null;
  source: { locator: string | null; evidence_class: string | null; retrieved_at: string | null };
};

export type EntranceReceipt = {
  verification_receipt_id: string;
  verifier_actor_id: string;
  verified_at: string;
  evidence_reference: string;
  native_navigation_proof: unknown;
  receipt_digest: string;
};

export type CoordinateCandidate = {
  coordinate_candidate_id: string;
  property_id: string;
  coordinate_role: "parcel_centroid" | "building_centroid" | "address_point" | "geocoder_candidate" | "entrance" | "driveway" | "parking_access" | "other";
  precision_class: "unknown" | "approximate" | "parcel" | "building" | "address" | "entrance" | "surveyed";
  observed_at: string;
  review_state: "unreviewed" | "reviewed" | "rejected" | "superseded";
  position: { latitude: number; longitude: number } | null;
  access_notes: string | null;
  source: { source_evidence_id: string; provider: string | null; stable_locator: string | null; retrieved_at: string | null; retrieval_status: string | null };
  rights: {
    rights_receipt_id: string;
    status: "usable" | "unavailable";
    provider: string | null;
    policy_key: string | null;
    receipt_version: number | null;
    terms_url: string | null;
    allowed_use_classes: string[] | null;
    allowed_field_classes: string[] | null;
    effective_at: string | null;
    expires_at: string | null;
    revoked_at: string | null;
    receipt_status: string | null;
  };
  approval: { status: "verified_entrance"; receipt: EntranceReceipt } | {
    status: "rights_unavailable" | "ineligible_role" | "candidate_not_reviewed" | "unverified";
    receipt: null;
  };
};
