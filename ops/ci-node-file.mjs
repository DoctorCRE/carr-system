import { tracingChannel } from 'node:diagnostics_channel';

if (process.env.NODE_TEST_CONTEXT === 'child-v8') {
  // Nested tools must not report themselves as additional test files.
  const option = `--import=${import.meta.url}`;
  process.env.NODE_OPTIONS = process.env.NODE_OPTIONS.replaceAll(option, '');
  process.execArgv = process.execArgv.filter(arg => arg !== option);
  let registered = false;
  tracingChannel('node.test').start.subscribe(() => { registered = true; });
  process.on('exit', () => console.log(`CARR_TEST_FILE ${Number(registered)}`));
}
