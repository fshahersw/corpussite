'use strict';
// Small DOM contract checks for regressions that hide valid saved content.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
class Node {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {}; this.classList = { add() {}, remove() {} }; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener() {}
}
const body = new Node('div'), main = new Node('main');
const context = {
  URLSearchParams, URL, encodeURIComponent, main,
  location: { origin: 'https://archive.example', pathname: '/', hostname: 'archive.example' },
  document: { createElement: tag => new Node(tag), querySelector: () => body },
  window: {}, route: { view: 'laws', params: new URLSearchParams() },
  displayTitle: item => item.title, kindLabel: value => value || '', human: value => value || '',
  evidenceDates: () => new Node('dl'), metaRow() {}, link: () => null,
  copyButton: () => new Node('button'), citationText: () => 'citation',
  rawStructuredText: text => text.trim().startsWith('{'),
  prose: (text, className) => Object.assign(new Node('div'), { textContent: text, className }),
  datasetName: value => value, visibleQuality: () => '', readingNote: () => '',
  renderSources() {}, renderDocumentOutline() {}, announce() {},
};
vm.createContext(context);
for (const name of ['el', 'append', 'makeHash', 'routeLink', 'filterField', 'lawHubOpen', 'countyTabs', 'countyResourceProse', 'renderRecord', 'action', 'errorState']) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `Missing ${name}`);
  let end = source.indexOf('\nfunction ', start + 1);
  const asyncEnd = source.indexOf('\nasync function ', start + 1);
  if (asyncEnd !== -1 && (end === -1 || asyncEnd < end)) end = asyncEnd;
  vm.runInContext(source.slice(start, end === -1 ? undefined : end), context);
}

for (const filter of ['file_type', 'subtype', 'review', 'jur_level', 'record_type', 'date_type', 'dfrom', 'dto', 'validity', 'view']) {
  context.route = { view: 'laws', params: new URLSearchParams({ state: 'Montana', [filter]: 'selected' }) };
  assert.equal(context.lawHubOpen(), false, `${filter} must open actual filtered results`);
}
context.route = { view: 'laws', params: new URLSearchParams({ state: 'Montana', toc: 'statutes', node: '1' }) };
assert.equal(context.lawHubOpen(), true, 'Outline navigation must retain the law browser');
assert.equal(context.filterField('From', 'dfrom', 'date', '2026-09-01').input.type, 'date');
assert.equal(context.filterField('Search', 'q', 'search', 'rules').input.type, 'search');

context.route = { view: 'county', params: new URLSearchParams({ tab: 'rules', category: 'rules' }) };
context.countyTabs({ state: 'California', geoid: '06059' });
const documentTab = main.children[0].children.find(node => node.textContent === 'Other saved documents');
assert.equal(new URLSearchParams(documentTab.href.split('?')[1]).has('category'), false, 'The documents tab must not hide non-rule documents');

const flatten = node => [node.textContent || '', ...node.children.map(flatten)].join('\n');
const item = { id: 'sample', title: 'Civil filing guide', dataset: 'focused', text: 'File the pleading with the clerk.', has_text: true };
context.renderRecord(item);
assert.match(flatten(body), /File the pleading with the clerk/);
assert.doesNotMatch(flatten(body), /no readable preview|No readable text/);
context.renderRecord({ ...item, dataset: 'county_litigation' });
assert.match(flatten(body), /File the pleading with the clerk/);
assert.doesNotMatch(flatten(body), /no readable preview|No readable text/);
context.renderRecord({ ...item, text: '', has_text: false });
assert.match(flatten(body), /No readable text is saved/);
context.renderRecord({ ...item, text: '{"source":"unparsed"}' });
assert.match(flatten(body), /no readable preview/);
context.errorState(body, new Error('Unavailable'), () => {});
assert.doesNotMatch(flatten(body), /launch instructions/);
context.location.hostname = '127.0.0.1';
context.errorState(body, new Error('Unavailable'), () => {});
assert.match(flatten(body), /launch instructions/);
console.log('UI release regression checks passed (advanced filters, date input, county tabs, reader empty states, hosted errors).');
