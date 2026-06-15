/**
 * Print use_figma script payloads for agent/MCP execution.
 * Usage: node figma-demo/run-scripts.js [01|02|03|all]
 */
import { script as s01 } from './scripts/01-create-variables.js';
import { script as s02 } from './scripts/02-foundations-page.js';
import { script as s03 } from './scripts/03-preferences-drawer.js';

const step = process.argv[2] || 'all';
const scripts = {
  '01': { name: 'Create variables', code: s01 },
  '02': { name: 'Foundations page', code: s02 },
  '03': { name: 'Preferences drawer', code: s03 },
};

if (step === 'all') {
  console.log(JSON.stringify({
    fileName: 'Antigravity Trading Terminal',
    order: ['01', '02', '03'],
    scripts: Object.fromEntries(
      Object.entries(scripts).map(([k, v]) => [k, { name: v.name, codeLength: v.code.length }]),
    ),
    note: 'Pass each script.code to use_figma with skillNames: figma-use,figma-generate-library (01) or figma-use,figma-generate-design (02,03)',
  }, null, 2));
} else if (scripts[step]) {
  console.log(scripts[step].code);
} else {
  console.error('Usage: node figma-demo/run-scripts.js [01|02|03|all]');
  process.exit(1);
}
