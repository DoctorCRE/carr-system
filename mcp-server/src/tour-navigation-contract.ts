// Versioned internal Tour navigation evidence for the later app consumer.
export const TOUR_NAVIGATION_CONTRACT = "tour-navigation-evidence.v1";

export type TourNavigationEvidence =
  | {
      contract_version: typeof TOUR_NAVIGATION_CONTRACT;
      status: "unavailable";
      reason: string;
      evidence: null;
    }
  | {
      contract_version: typeof TOUR_NAVIGATION_CONTRACT;
      status: "ready";
      reason: null;
      evidence: {
        route_version_id: string;
        route_version: number;
        projection_id: string;
        projection_version: number;
        projection_digest: string;
        as_of: string;
        promotion: {
          receipt_id: string;
          receipt_digest: string;
          reviewed_at: string;
          reviewer_actor_id: string;
          route_version: number;
          canonical_dataset_version: string;
          component_registry_version: string;
          selected_prototype_id: string;
          brief_version: string;
          provider_rights_receipt_ids: string[];
          mobile_test_evidence: object;
          native_navigation_test_evidence: object;
          offline_test_evidence: object;
          required_checks: Record<string, boolean>;
        };
        stops: {
          route_stop_id: string;
          property_id: string;
          route_sequence: number;
          route_label: string;
          position: { latitude: number; longitude: number };
          source: {
            coordinate_candidate_id: string;
            coordinate_role: "entrance" | "driveway" | "parking_access";
            precision_class: string;
            observed_at: string;
            review_state: "reviewed";
            source_evidence_id: string;
            stable_locator: string;
            content_digest: string | null;
            retrieved_at: string;
            retrieval_status: "read";
            rights_receipt_id: string;
            entrance_verification_receipt_id: string;
            receipt_digest: string;
            verifier_actor_id: string;
            verified_at: string;
            evidence_reference: string;
            native_navigation_proof: object;
          };
        }[];
      };
    };
