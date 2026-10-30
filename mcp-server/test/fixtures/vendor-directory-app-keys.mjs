// Key rules copied from jbookout/doctorcre-app js/workspace-business-model.js
// at 5b98c524cd763887c0725849017e73cc764a0dcf, rowValid and validRecordPayload.
// Required keys and extendedKeys semantics are preserved without cross-repo imports.
export const vendorRowRequired = ["id", "ref", "name", "party_kind", "city", "state", "recorded_category", "recorded_category_label",
    "recorded_stage", "recorded_stage_label", "recorded_disposition", "recorded_disposition_label",
    "recorded_disposition_workable", "relationship_level", "relationship_level_label", "referral_active",
    "is_target", "out_of_market", "last_touch", "owner_label", "owned_by_viewer", "updated_at"];
export const vendorDetailRequired = ["id", "ref", "name", "party_kind", "city", "state", "county", "title", "phone", "cell", "email",
    "contact_state", "contact_state_reason", "contact_state_until", "contact_state_cadence", "recorded_category",
    "recorded_category_label", "recorded_stage", "recorded_stage_label", "recorded_disposition",
    "recorded_disposition_label", "recorded_disposition_workable", "relationship_level", "relationship_level_label",
    "relationship_level_note", "verticals", "territory", "offers", "seeking", "rivalry_group", "originated",
    "referral_active", "is_target", "out_of_market", "last_touch", "intro_notes", "links_label", "owner_label",
    "owned_by_viewer", "record_version", "created_at", "updated_at"];
export const vendorRowOptional = ['relationship', 'vertical', 'deal_type', 'last_deal_at', 'territory'];
export const vendorDetailOptional = ['relationship', 'loan_programs'];
export function appKeysValid(value, required, optional) {
 return value && typeof value === 'object' && required.every(k => k in value) &&
  Object.keys(value).every(k => required.includes(k) || optional.includes(k));
}
