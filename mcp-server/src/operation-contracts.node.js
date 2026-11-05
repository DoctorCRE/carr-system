// Compose committed expected operation rows without generating checkout files.
import { readFileSync, readdirSync } from 'node:fs';
import { composeOperations } from './registry-layout.js';
const directory = new URL('../contracts/operations/', import.meta.url);
export const SCAC_MUTATION_OPERATIONS = composeOperations(readdirSync(directory, {withFileTypes:true})
  .sort((a,b)=>a.name < b.name ? -1 : a.name > b.name ? 1 : 0).map(entry => {
    if (!entry.isFile() || !/^[a-z][a-z0-9-]*\.json$/.test(entry.name)) throw new Error(`invalid operation path: ${entry.name}`);
    return [entry.name.slice(0,-5), JSON.parse(readFileSync(new URL(entry.name,directory),'utf8'))];
  }));
