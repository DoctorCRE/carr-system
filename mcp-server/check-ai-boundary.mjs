import { parse } from '@babel/parser';
import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(process.argv[2] || fileURLToPath(new URL('./src', import.meta.url)));
const wrapper = fileURLToPath(new URL('./src/ai-budget.js', import.meta.url));
let failures = 0;

function staticString(node) {
  if (node.type === 'StringLiteral') return node.value;
  if (node.type === 'BinaryExpression' && node.operator === '+') {
    const left = staticString(node.left), right = staticString(node.right);
    if (left !== undefined && right !== undefined) return left + right;
  }
  if (node.type === 'TemplateLiteral') {
    const values = node.expressions.map(staticString);
    if (values.every(value => value !== undefined))
      return node.quasis.map((part, index) => part.value.cooked + (values[index] || '')).join('');
  }
}

function scan(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const file = path.join(directory, entry.name);
    if (entry.isDirectory()) { scan(file); continue; }
    if (!/\.(?:[cm]?js|tsx?)$/.test(entry.name) || file === wrapper) continue;
    const source = parse(readFileSync(file, 'utf8'), {
      sourceType: 'module', plugins: ['typescript'], attachComment: false,
    });
    function visit(node) {
      if (!node || typeof node !== 'object') return;
      if ((node.type === 'Identifier' && node.name === 'AI')
          || staticString(node) === 'AI'
          || (node.type === 'TemplateElement' && node.value.cooked === 'AI')) {
        console.error(`${file}:${node.loc.start.line}: AI binding must stay inside ai-budget.js`);
        failures++;
      }
      for (const [key, value] of Object.entries(node)) {
        if (['comments', 'loc', 'extra'].includes(key)) continue;
        if (Array.isArray(value)) value.forEach(visit);
        else if (value && typeof value === 'object') visit(value);
      }
    }
    visit(source.program);
  }
}

scan(root);
if (failures) process.exitCode = 1;
else console.log('Workers AI binding access is confined to ai-budget.js');
