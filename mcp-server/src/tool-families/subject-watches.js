import { subjectWatchTools } from '../subject-watches.js';

export default {
  source: 'mcp-server/src/subject-watches.js',
  createTools: ({ withEnvelope, ToolError }) => subjectWatchTools({ withEnvelope, ToolError }),
};
