// Compose reviewed market study record groups without publication effects.
import schema from '../../client-portal/market-study.v1.schema.json' with { type: 'json' };
import { RECORD_LAYER_GROUPS, validateMarketStudyPackage } from './market-study-package.ts';

const identityFields = ['deal_id', 'client_id'];
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const pointer = key => key.replaceAll('~', '~0').replaceAll('/', '~1');
const failure = (stage, errors) => ({ ok: false, stage, errors });

function unknownFields(value, allowed, path, errors) {
  for (const key of Object.keys(value).sort()) {
    if (!allowed.includes(key)) errors.push({ path: `${path}/${pointer(key)}`, message: 'Unexpected field.' });
  }
}

const geometryTypes = new Set(['Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon']);

function publicGeoJSON(value, path, errors) {
  const invalid = () => {
    errors.push({ path, reason: 'invalid_public_geometry', message: 'A public GeoJSON structure is required.' });
    return null;
  };
  if (!object(value)) return invalid();
  const { type } = value;
  if (type === 'FeatureCollection' || type === 'GeometryCollection') {
    const key = type === 'FeatureCollection' ? 'features' : 'geometries';
    if (!Array.isArray(value[key])) return invalid();
    return { type, [key]: value[key].map((child, index) => publicGeoJSON(child, `${path}/${key}/${index}`, errors)) };
  }
  if (type === 'Feature') {
    return { type, properties: {}, geometry: value.geometry === null ? null : publicGeoJSON(value.geometry, `${path}/geometry`, errors) };
  }
  if (!geometryTypes.has(type)) return invalid();
  function coordinates(value) {
    if (Array.isArray(value)) return value.map(coordinates);
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    return invalid();
  }
  return { type, coordinates: coordinates(value.coordinates) };
}

function publicPackage(value, shape, path, assets, errors) {
  if (shape.$ref === '#/$defs/GeoJSON') return publicGeoJSON(value, path, errors);
  if (shape.$ref === '#/$defs/Asset') assets.push({ path, data: value });
  if (shape.$ref) shape = schema.$defs[shape.$ref.split('/').at(-1)];
  if (Array.isArray(value)) {
    return value.map((child, index) => publicPackage(child, shape.prefixItems?.[index] || shape.items || {}, `${path}/${index}`, assets, errors));
  }
  if (!object(value)) return value;
  const fields = Object.assign({}, shape.properties, ...(shape.oneOf || []).map(branch => branch.properties),
    ...(shape.allOf || []).map(branch => branch.then?.properties));
  return Object.fromEntries(Object.entries(value).filter(([key]) => Object.hasOwn(fields, key) || object(shape.additionalProperties))
    .map(([key, child]) => [key, publicPackage(child, fields[key] || shape.additionalProperties, `${path}/${pointer(key)}`, assets, errors)]));
}

const assetKey = asset => JSON.stringify([asset?.sha256, asset?.name, asset?.type]);

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

export function composeMarketStudyPackage(research, reviewed, target, membership) {
  const partial = validateMarketStudyPackage(research, { mode: 'partial' });
  if (!partial.ok) return failure('research', partial.errors);

  const errors = [];
  if (!object(target)) return failure('target', [{ path: '/', message: 'Target deal and client identity is required.' }]);
  unknownFields(target, identityFields, '', errors);
  for (const field of identityFields) {
    if (typeof target[field] !== 'string' || !target[field].trim()) {
      errors.push({ path: `/${field}`, message: 'A nonempty target identity is required.' });
    }
  }
  if (errors.length) return failure('target', errors);
  if (!object(membership) || !object(membership.research) || !Array.isArray(membership.assets)) {
    return failure('membership', [{ path: '/', reason: 'membership_required', message: 'Scoped research identity and asset envelopes are required.' }]);
  }
  unknownFields(membership, ['research', 'assets'], '', errors);
  unknownFields(membership.research, identityFields, '/research', errors);
  for (const field of identityFields) {
    if (membership.research[field] !== target[field]) errors.push({ path: `/research/${field}`, reason: 'research_identity_mismatch', message: 'Research identity must match the target.' });
  }
  const assetScopes = new Map();
  membership.assets.forEach((row, index) => {
    if (!object(row) || !object(row.data)) {
      errors.push({ path: `/assets/${index}`, reason: 'invalid_asset_envelope', message: 'A scoped asset envelope is required.' });
      return;
    }
    unknownFields(row, [...identityFields, 'data'], `/assets/${index}`, errors);
    const key = assetKey(row.data);
    const matches = identityFields.every(field => row[field] === target[field]);
    assetScopes.set(key, matches && assetScopes.get(key) !== false);
  });
  if (errors.length) return failure('membership', errors);
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
  const assets = [];
  const output = publicPackage(pkg, schema, '', assets, errors);
  for (const { path, data } of assets) {
    const scope = assetScopes.get(assetKey(data));
    if (scope !== true) errors.push({ path, reason: scope === false ? 'asset_identity_mismatch' : 'asset_membership_missing', message: 'Asset must resolve inside the target deal and client.' });
  }
  const ids = new Set();
  pkg.properties.forEach((property, index) => {
    if (ids.has(property.id)) errors.push({ path: `/properties/${index}/id`, message: 'Duplicate property ID.' });
    ids.add(property.id);
  });
  if (pkg.ownership && !ids.has(pkg.ownership.default_property)) {
    errors.push({ path: '/ownership/default_property', message: 'Ownership default must name a reviewed property.' });
  }
  if (errors.length) return failure('composed', errors);
  return { ok: true, package: structuredClone(output), errors: [] };
}
