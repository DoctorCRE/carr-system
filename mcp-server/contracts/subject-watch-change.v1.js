import { Validator } from '@cfworker/json-schema';
import schema from './subject-watch-change.v1.json' with { type: 'json' };
const validator = new Validator(schema);
export function parseSubjectWatchChanges(value) {
  if (!validator.validate(value).valid) throw new Error('invalid_subject_watch_change_v1');
  return value;
}
