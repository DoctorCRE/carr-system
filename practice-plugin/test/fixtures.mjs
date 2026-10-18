export const runwayFixture = () => ({ start_month: '2027-01', horizon_months: 3, opening_month: 1, delay_months: 0, initial_cash_usd: 100000, reserve_floor_usd: 10000,
  cash_injections: [], one_time_costs: [{ id: 'startup', month: 1, timing: 'fixed_calendar', amount_usd: 20000 }],
  recurring_costs: [{ id: 'operations', start_month: 1, end_month: 3, timing: 'fixed_calendar', monthly_usd: 20000 }], receipts: { mode: 'cash', monthly_usd: [0, 10000, 30000] }, missing_cost_categories: [] });
const condition = () => ({ status: 'verified', evidence_ref: 'survey', verified_on: '2026-10-01', value: 200, units: 'A', voltage: 240, phase: 'single' });
const requirement = () => ({ id: 'power', category: 'power', applicability: 'yes', mandatory: true, type: 'numeric_minimum', required_value: 400, units: 'A', voltage: 240, phase: 'single',
  evidence_ref: 'engineer', requirement_verified_on: '2026-10-01', jurisdiction_ref: 'local', edition_ref: 'current', existing: condition(), remediation_scope_ids: ['construction'] });
export const conversionFixture = () => ({ as_of_date: '2026-10-01', jurisdiction_resolved: true, jurisdiction_ref: 'local', intended_services: ['routine_exams'], building_type: 'office', proposed_occupancy: 'outpatient',
  usable_sf: 2000, rentable_sf: 2400, providers: 1, treatment_rooms: 3, equipment: [], requirements: [requirement()], construction_contingency_fraction: .1, required_scope_ids: ['construction', 'equipment', 'fees'],
  scope_lines: [
    { id: 'construction', category: 'construction', shared_scope_group: 'build', low_lump_usd: 80000, high_lump_usd: 100000, tax_inclusive: true, separate_tax_scope_id: null, quote_date: '2026-10-01', evidence_classification: 'owner_input' },
    { id: 'equipment', category: 'equipment', shared_scope_group: 'devices', low_lump_usd: 20000, high_lump_usd: 20000, tax_inclusive: true, separate_tax_scope_id: null, quote_date: '2026-10-01', evidence_classification: 'owner_input' },
    { id: 'fees', category: 'professional_fees', shared_scope_group: 'design', low_lump_usd: 10000, high_lump_usd: 10000, tax_inclusive: true, separate_tax_scope_id: null, quote_date: '2026-10-01', evidence_classification: 'planning_assumption' },
  ], tenant_allowance: { maximum_usd: 30000, eligible_scope_ids: ['construction'], confirmed: true, evidence_ref: 'allowance', reimbursement_month: 6 } });
export const fact = (value = 'yes') => ({ value, evidence_ref: value === 'unknown' ? null : 'document' });
const option = (id, start_date, end_exclusive) => ({ id, start_date, end_exclusive, transferable_to_buyer: fact(), exercisable_by_buyer: fact(), conditions_satisfied: fact(), notice_deadline: '2026-12-01', notice_delivered: fact() });
export const saleFixture = () => ({ as_of_date: '2026-10-01', expected_closing_date: '2027-01-01', base_term_end_exclusive: '2032-01-01', assignment_status: 'permitted', assignment_evidence_ref: 'lease',
  lender_required_months: 120, lender_evidence_ref: 'lender', lender_verified_on: '2026-10-01', documents_complete: true, documents_conflict: false,
  renewal_options: [option('first', '2032-01-01', '2037-01-01'), option('second', '2037-01-01', '2042-01-01')], termination_rights: [], personal_guarantee_release: fact(), defaults_resolved: fact(), permitted_use_match: fact(), lender_waiver: fact(), lender_waiver_applicable: fact('no'), required_consents_obtained: fact(), rent_escalations: [] });
