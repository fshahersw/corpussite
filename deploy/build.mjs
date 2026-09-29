import { mkdir, copyFile, readFile } from 'node:fs/promises';
import path from 'node:path';

const root = process.cwd();
const source = path.join(root, 'delivery', 'archive-directory');
const output = path.join(root, 'dist');
const files = ['index.html', 'app.js', 'areas.js', 'usmap.js', 'statsviz.js', 'judgeui.js', 'regsui.js', 'lawreader.js', 'enrichment.js', 'insights-data.js', 'official-caseload.js', 'insights.js', 'styles.css',
  'assets/us-counties-albers-10m.json', 'assets/us-counties-albers-10m.SOURCE.txt'];
for (const name of files) {
  const destination = path.join(output, name);
  await mkdir(path.dirname(destination), { recursive: true });
  await copyFile(path.join(source, name), destination);
}
const index = await readFile(path.join(output, 'index.html'), 'utf8');
for (const match of index.matchAll(/(?:src|href)="\/?([^"#:]+\.(?:js|css))"/g)) {
  if (!files.includes(match[1])) throw new Error(`Missing frontend asset: ${match[1]}`);
}
console.log(`Built ${files.length} frontend assets. Categorized data is served by the private Supabase API.`);
