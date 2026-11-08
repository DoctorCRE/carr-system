import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
const { Validator } = createRequire(new URL('../mcp-server/package.json', import.meta.url))('@cfworker/json-schema');

export function checkProof({ response, original, expectedText }) {
  const schema = JSON.parse(readFileSync(new URL('../docs/contracts/deal-document-text.v1/response.schema.json', import.meta.url)));
  if (!new Validator(schema).validate(response).valid) throw new Error('response_contract');
  if (response.status !== 'produced') throw new Error('text_not_produced');
  if (response.content_hash !== createHash('sha256').update(original).digest('hex')) throw new Error('original_hash_mismatch');
  if (response.lines.map(line => line.text).join('') !== expectedText) throw new Error('text_mismatch');
  return { ok: true, content_hash: response.content_hash, text_hash: response.text_hash };
}

if (process.argv[1]?.endsWith('check-deal-document-text-proof.mjs')) {
  try {
    const [responseFile, originalFile, textFile] = process.argv.slice(2);
    if (!responseFile || !originalFile || !textFile) throw new Error('three evidence files required');
    const result = checkProof({ response: JSON.parse(readFileSync(responseFile)), original: readFileSync(originalFile), expectedText: readFileSync(textFile, 'utf8') });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(JSON.stringify({ ok: false, error: error.message }));
    process.exitCode = 1;
  }
}
