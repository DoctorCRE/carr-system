// Compose reviewed market study record groups without publication effects.
import { RECORD_LAYER_GROUPS, validateMarketStudyPackage } from './market-study-package.js';

const identityFields = ['deal_id', 'client_id'];
const internalFields = new Set(['internal_notes', 'internal_note', 'client_uploads', 'deal_financials', 'stored_deal_financials']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const pointer = key => key.replaceAll('~', '~0').replaceAll('/', '~1');
const failure = (stage, errors) => ({ ok: false, stage, errors });

function unknownFields(value, allowed, path, errors) {
  for (const key of Object.keys(value).sort()) {
    if (!allowed.includes(key)) errors.push({ path: `${path}/${pointer(key)}`, message: 'Unexpected field.' });
  }
}

function internalFieldErrors(value, path = '') {
  if (!value || typeof value !== 'object') return [];
  return Object.keys(value).sort().flatMap(key => {
    const childPath = `${path}/${pointer(key)}`;
    return internalFields.has(key)
      ? [{ path: childPath, message: 'Internal record fields cannot enter a market study.' }]
      : internalFieldErrors(value[key], childPath);
  });
}

function checkEnvelope(value, target, path, errors) {
  if (!object(value)) {
    errors.push({ path, message: 'A reviewed record envelope is required.' });
    return;
  }
  unknownFields(value, [...identityFields, 'data'], path, errors);
  for (const field of identityFields) {
    if (value[field] !== target[field]) errors.push({ path: `${path}/${field}`, message: 'Record identity must match the target.' });
  }
  if (!Object.hasOwn(value, 'data')) errors.push({ path: `${path}/data`, message: 'Reviewed package data is required.' });
}

export function composeMarketStudyPackage(research, reviewed, target) {
  const partial = validateMarketStudyPackage(research, { mode: 'partial' });
  if (!partial.ok) return failure('research', partial.errors);
  const privateResearch = internalFieldErrors(research);
  if (privateResearch.length) return failure('research', privateResearch);

  const errors = [];
  if (!object(target)) return failure('target', [{ path: '/', message: 'Target deal and client identity is required.' }]);
  unknownFields(target, identityFields, '', errors);
  for (const field of identityFields) {
    if (typeof target[field] !== 'string' || !target[field].trim()) {
      errors.push({ path: `/${field}`, message: 'A nonempty target identity is required.' });
    }
  }
  if (errors.length) return failure('target', errors);
  if (!object(reviewed)) return failure('reviewed', [{ path: '/', message: 'Reviewed record groups are required.' }]);
  unknownFields(reviewed, RECORD_LAYER_GROUPS, '', errors);
  for (const group of RECORD_LAYER_GROUPS) {
    if (!Object.hasOwn(reviewed, group)) {
      errors.push({ path: `/${group}`, message: 'An explicit reviewed group is required.' });
    } else if (group === 'properties') {
      if (!Array.isArray(reviewed[group])) {
        errors.push({ path: `/${group}`, message: 'Reviewed property envelopes must be an array.' });
      } else {
        reviewed[group].forEach((row, index) => checkEnvelope(row, target, `/${group}/${index}`, errors));
      }
    } else {
      checkEnvelope(reviewed[group], target, `/${group}`, errors);
    }
  }
  if (errors.length) return failure('reviewed', errors);

  const pkg = { ...research };
  for (const group of RECORD_LAYER_GROUPS) {
    pkg[group] = group === 'properties' ? reviewed[group].map(row => row.data) : reviewed[group].data;
  }
  const full = validateMarketStudyPackage(pkg);
  if (!full.ok) return failure('composed', full.errors);
  const privateOutput = internalFieldErrors(pkg);
  if (privateOutput.length) return failure('composed', privateOutput);
  const ids = new Set();
  pkg.properties.forEach((property, index) => {
    if (ids.has(property.id)) errors.push({ path: `/properties/${index}/id`, message: 'Duplicate property ID.' });
    ids.add(property.id);
  });
  if (pkg.ownership && !ids.has(pkg.ownership.default_property)) {
    errors.push({ path: '/ownership/default_property', message: 'Ownership default must name a reviewed property.' });
  }
  if (errors.length) return failure('composed', errors);
  return { ok: true, package: structuredClone(pkg), errors: [] };
}
