import { subjectWatchChangeTools } from '../subject-watch-changes.js';
import { subjectWatchTools, subjectWatchV2Tools } from '../subject-watches.js';

export default {
  source: 'mcp-server/src/subject-watches.js',
  createTools: ({ withEnvelope, ToolError }) => ({ ...subjectWatchTools({ withEnvelope, ToolError }), ...subjectWatchV2Tools({ withEnvelope, ToolError }), ...subjectWatchChangeTools({ ToolError }) }),
};
