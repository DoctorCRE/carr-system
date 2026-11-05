import schema from '../../client-portal/explainer.v1.schema.json' with { type: 'json' };
import { compileSchema } from '../../workspace/contracts/schema-validator.mjs';
import { sha256 } from './sha256.ts';

const validate = compileSchema(schema);

export function prepareExplainerRevision(input) {
  if (!validate(input).valid) throw new Error('explainer_payload_invalid');
  const payload = {
    kind: input.kind,
    section_key: input.section_key,
    title: input.title,
    paragraphs: input.paragraphs.map(paragraph => Object.freeze(paragraph.map(run => {
      if ('text' in run) return Object.freeze({ text: run.text });
      let url;
      try { url = new URL(run.link.url); }
      catch { throw new Error('explainer_payload_invalid'); }
      if (url.protocol !== 'https:' || url.username || url.password)
        throw new Error('explainer_payload_invalid');
      return Object.freeze({ link: Object.freeze({ label: run.link.label, url: run.link.url }) });
    }))),
  };
  const canonical = JSON.stringify(payload);
  if (new TextEncoder().encode(canonical).length > 32768)
    throw new Error('explainer_payload_too_large');
  Object.freeze(payload.paragraphs);
  return Object.freeze({ payload: Object.freeze(payload), content_revision: `sha256:${sha256(canonical)}` });
}
