// Discover committed family descriptors read-only for fresh Node checkouts.
import { readdirSync } from 'node:fs';
import { orderFamilyIds, validateFamily } from './registry-layout.js';
const directory = new URL('./tool-families/', import.meta.url);
export const ids = orderFamilyIds(readdirSync(directory, {withFileTypes:true}).map(entry => {
  if (!entry.isFile() || !/^[A-Za-z0-9-]+\.js$/.test(entry.name)) throw new Error(`invalid family path: ${entry.name}`);
  return entry.name.slice(0,-3);
}));
const families = [];
for (const id of ids) families.push(validateFamily((await import(new URL(`${id}.js`,directory))).default,id));
export default Object.freeze(families);
