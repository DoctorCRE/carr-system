// Pure CARR lease-term comparison math.
//
// This reproduces the client-facing LeaseComparison-5vs10Year workbook's
// five-year comparison block. It deliberately does not read a record, call a
// model, fill a workbook, write a file, or choose terms. An AI may use the
// result as decision support, but the source workbook remains required before
// anything is client-facing.

const HORIZON_YEARS = 5;
const ROOT_FIELDS = new Set(["square_feet", "long_term", "short_term", "financing"]);
const OPTION_FIELDS = new Set([
  "label", "term_years", "base_rent_per_sf", "operating_expenses_per_sf", "free_rent_months",
  "free_opex_months", "buildout_cost_per_sf", "landlord_ti_per_sf",
]);
const FINANCING_FIELDS = new Set(["annual_interest_rate", "amortization_years"]);

class LeaseComparisonInputError extends Error {
  constructor(payload) {
    super(payload.error);
    this.payload = payload;
  }
}

function refuse(ErrorType, error, hint) {
  throw new ErrorType({ error, ...(hint ? { hint } : {}) });
}

function exactObject(value, fields, ErrorType, label) {
  if (!value || typeof value !== "object" || Array.isArray(value))
    refuse(ErrorType, `invalid_${label}`, `${label} must be an object`);
  if (Object.keys(value).some((key) => !fields.has(key)))
    refuse(ErrorType, `unexpected_${label}_arguments`);
}

function finiteNumber(value, ErrorType, label, minimum, maximum) {
  if (typeof value !== "number" || !Number.isFinite(value) ||
      value < minimum || value > maximum)
    refuse(ErrorType, `invalid_${label}`);
  return value;
}

function boundedInteger(value, ErrorType, label, minimum, maximum) {
  if (!Number.isInteger(value) || value < minimum || value > maximum)
    refuse(ErrorType, `invalid_${label}`);
  return value;
}

function validateOption(value, ErrorType, side) {
  exactObject(value, OPTION_FIELDS, ErrorType, `${side}_term`);
  if (typeof value.label !== "string" || !value.label.trim() || value.label.trim().length > 80)
    refuse(ErrorType, `invalid_${side}_term_label`);
  const requiredTerm = side === "long" ? 10 : 5;
  if (value.term_years !== requiredTerm)
    refuse(ErrorType, `invalid_${side}_term_years`,
      `${side}_term.term_years must be ${requiredTerm} for this fixed workbook comparison`);
  if (!Array.isArray(value.base_rent_per_sf) ||
      value.base_rent_per_sf.length !== HORIZON_YEARS)
    refuse(ErrorType, `invalid_${side}_term_base_rent_schedule`);
  const baseRent = value.base_rent_per_sf.map((amount) =>
    finiteNumber(amount, ErrorType, `${side}_term_base_rent_schedule`, 0, 10000));

  return {
    label: value.label.trim(),
    termYears: requiredTerm,
    baseRent,
    operatingExpenses: finiteNumber(value.operating_expenses_per_sf, ErrorType,
      `${side}_term_operating_expenses`, 0, 1000),
    freeRentMonths: boundedInteger(value.free_rent_months, ErrorType,
      `${side}_term_free_rent_months`, 0, 60),
    freeOpexMonths: boundedInteger(value.free_opex_months, ErrorType,
      `${side}_term_free_opex_months`, 0, 60),
    buildoutCost: finiteNumber(value.buildout_cost_per_sf, ErrorType,
      `${side}_term_buildout_cost`, 0, 10000),
    landlordTi: finiteNumber(value.landlord_ti_per_sf, ErrorType,
      `${side}_term_landlord_ti`, 0, 10000),
  };
}

function validate(args, ErrorType) {
  exactObject(args, ROOT_FIELDS, ErrorType, "arguments");
  for (const required of ROOT_FIELDS) {
    if (!(required in args)) refuse(ErrorType, "invalid_arguments", `missing ${required}`);
  }
  const squareFeet = finiteNumber(args.square_feet, ErrorType, "square_feet", 1, 1000000);
  const longTerm = validateOption(args.long_term, ErrorType, "long");
  const shortTerm = validateOption(args.short_term, ErrorType, "short");

  exactObject(args.financing, FINANCING_FIELDS, ErrorType, "financing");
  for (const required of FINANCING_FIELDS) {
    if (!(required in args.financing))
      refuse(ErrorType, "invalid_financing", `missing ${required}`);
  }
  const financing = {
    annualInterestRate: finiteNumber(args.financing.annual_interest_rate, ErrorType,
      "annual_interest_rate", 0, 1),
    amortizationYears: boundedInteger(args.financing.amortization_years, ErrorType,
      "amortization_years", 1, 40),
  };
  return { squareFeet, longTerm, shortTerm, financing };
}

function money(value) {
  return Math.sign(value) * Math.round((Math.abs(value) + Number.EPSILON) * 100) / 100;
}

function ratio(value) {
  return Math.sign(value) * Math.round((Math.abs(value) + Number.EPSILON) * 10000) / 10000;
}

function totalFinancingInterest(principal, annualRate, years) {
  const monthlyRate = annualRate / 12;
  if (principal === 0 || monthlyRate === 0) return 0;
  const periods = years * 12;
  const discountedPeriods = -Math.expm1(-periods * Math.log1p(monthlyRate));
  return principal * (periods * monthlyRate / discountedPeriods - 1);
}

function calculateOption(option, squareFeet, financing) {
  const rawSchedule = option.baseRent.map((perSf, index) => ({
    year: index + 1,
    per_sf: perSf,
    annual_base_rent: perSf * squareFeet,
  }));
  const annualOpex = option.operatingExpenses * squareFeet;
  const costOfRent = rawSchedule.reduce((total, row) => total + row.annual_base_rent, 0) +
    (annualOpex * HORIZON_YEARS);
  const freeRent = option.freeRentMonths * rawSchedule[0].annual_base_rent / 12;
  const freeOpex = option.freeOpexMonths * annualOpex / 12;
  const tenantTi = Math.max((option.buildoutCost - option.landlordTi) * squareFeet, 0);
  const interest = totalFinancingInterest(tenantTi, financing.annualInterestRate,
    financing.amortizationYears);

  return {
    raw: {
      firstYearBaseRent: rawSchedule[0].annual_base_rent,
      annualOpex,
      costOfRent,
      concessions: freeRent + freeOpex,
      totalBuildout: tenantTi + interest,
    },
    output: {
      label: option.label,
      term_years: option.termYears,
      base_rent_schedule: rawSchedule.map((row) => ({
        ...row, annual_base_rent: money(row.annual_base_rent),
      })),
      annual_operating_expenses: money(annualOpex),
      cost_of_rent: money(costOfRent),
      free_base_rent: money(freeRent),
      free_operating_expenses: money(freeOpex),
      total_free_rent_and_opex: money(freeRent + freeOpex),
      estimated_buildout_cost: money(option.buildoutCost * squareFeet),
      landlord_ti_allowance: money(option.landlordTi * squareFeet),
      tenant_ti_contribution: money(tenantTi),
      total_interest_on_tenant_ti: money(interest),
      total_cost_of_buildout: money(tenantTi + interest),
    },
  };
}

export function compareLeaseTerms(args, ErrorType = LeaseComparisonInputError) {
  const { squareFeet, longTerm: longInput, shortTerm: shortInput, financing } =
    validate(args, ErrorType);
  const longCalculation = calculateOption(longInput, squareFeet, financing);
  const shortCalculation = calculateOption(shortInput, squareFeet, financing);
  const rentDifference = shortCalculation.raw.costOfRent - longCalculation.raw.costOfRent;
  const concessionsDifference = longCalculation.raw.concessions - shortCalculation.raw.concessions;
  const buildoutDifference = shortCalculation.raw.totalBuildout - longCalculation.raw.totalBuildout;
  const additionalCost = money(rentDifference + concessionsDifference + buildoutDifference);
  const longYearOneOccupancy = longCalculation.raw.firstYearBaseRent +
    longCalculation.raw.annualOpex;

  return {
    calculation_basis: "carr-lease-comparison-5vs10-v1",
    comparison_horizon_years: HORIZON_YEARS,
    square_feet: squareFeet,
    financing: {
      annual_interest_rate: financing.annualInterestRate,
      amortization_years: financing.amortizationYears,
    },
    long_term: longCalculation.output,
    short_term: shortCalculation.output,
    comparison: {
      rent_cost_difference: money(rentDifference),
      lost_concessions_difference: money(concessionsDifference),
      buildout_cost_difference: money(buildoutDifference),
      additional_cost_of_shorter_term: additionalCost,
      additional_years_of_long_term_year_one_rent: longYearOneOccupancy > 0
        ? ratio(additionalCost / longYearOneOccupancy)
        : null,
      direction: additionalCost > 0 ? "shorter_costs_more"
        : additionalCost < 0 ? "shorter_costs_less" : "equivalent",
    },
    client_facing_workbook_required: true,
    note: "Decision support only. Reconcile against the current CARR lease-comparison workbook " +
      "before client use; operating expenses are held at the entered year-one amount across the " +
      "five-year comparison horizon and may increase.",
    calls_models: false,
    writes_records: false,
    allowed_actions: [],
  };
}

function leaseOptionSchema(termYears) {
  return {
  type: "object",
  additionalProperties: false,
  properties: {
    label: { type: "string", minLength: 1, maxLength: 80 },
    term_years: { type: "integer", enum: [termYears] },
    base_rent_per_sf: {
      type: "array", minItems: HORIZON_YEARS, maxItems: HORIZON_YEARS,
      items: { type: "number", minimum: 0, maximum: 10000 },
      description: "exact annual base-rent schedule per SF for years 1-5",
    },
    operating_expenses_per_sf: { type: "number", minimum: 0, maximum: 1000 },
    free_rent_months: { type: "integer", minimum: 0, maximum: 60 },
    free_opex_months: { type: "integer", minimum: 0, maximum: 60 },
    buildout_cost_per_sf: { type: "number", minimum: 0, maximum: 10000 },
    landlord_ti_per_sf: { type: "number", minimum: 0, maximum: 10000 },
  },
  required: ["label", "term_years", "base_rent_per_sf", "operating_expenses_per_sf",
    "free_rent_months", "free_opex_months", "buildout_cost_per_sf", "landlord_ti_per_sf"],
  };
}

export function leaseTermComparisonTools({ ToolError }) {
  return {
    "compare-lease-terms": {
      write: false,
      description: "Pure CARR 5-vs-10 lease economics comparison. Given exact five-year rent " +
        "schedules, OpEx, free rent, TI and financing assumptions, reproduces the core comparison " +
        "math from the CARR LeaseComparison-5vs10Year workbook. It does not read records, infer " +
        "terms, call a model, write, send, or replace the client-facing workbook.",
      inputSchema: {
        type: "object",
        additionalProperties: false,
        properties: {
          square_feet: { type: "number", minimum: 1, maximum: 1000000 },
          long_term: leaseOptionSchema(10),
          short_term: leaseOptionSchema(5),
          financing: {
            type: "object",
            additionalProperties: false,
            properties: {
              annual_interest_rate: { type: "number", minimum: 0, maximum: 1,
                description: "decimal annual rate, for example 0.06 for 6%" },
              amortization_years: { type: "integer", minimum: 1, maximum: 40 },
            },
            required: ["annual_interest_rate", "amortization_years"],
          },
        },
        required: ["square_feet", "long_term", "short_term", "financing"],
      },
      handler: async (_client, _actor, args) => compareLeaseTerms(args, ToolError),
    },
  };
}
