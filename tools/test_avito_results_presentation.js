// Execute the shipped result renderer, resume path and form handlers without network.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'avito_web', 'app.js'), 'utf8');
const extract = (startText, endText) => {
  const start = source.indexOf(startText);
  const end = source.indexOf(endText, start + startText.length);
  assert(start >= 0 && end > start, `Missing source region: ${startText}`);
  return source.slice(start, end);
};
const element = () => ({innerHTML: '', textContent: '', hidden: false});
const elements = Object.fromEntries(['results', 'warnings', 'summary', 'costs', 'audit',
  'adminWarnings', 'ownerPreview', 'operatorPanel', 'activeFilters', 'advancedHint'].map(name => [name, element()]));
let remembered = 0;
let cleared = 0;
let status = '';
const context = vm.createContext({...elements, URL, Intl, Date,
  escapeHtml: value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])),
  activeSearchPayload: {query: 'PlayStation 5', mode: 'bargain', desiredResults: 3},
  authenticatedOwner: false, activeJobId: null, activeJobIsDataset: false,
  rememberVerifiedExample: () => { remembered += 1; },
  setStatus: value => { status = value; }, clearStatus: () => { status = ''; },
  clearActiveJob: () => { cleared += 1; },
});
vm.runInContext(extract('function money(', '\nfunction updateProgress('), context);
const listingUrl = id => `https://www.avito.ru/yaroslavl/igry_pristavki/console_${id}`;
const verified = {role: 'TOP', belowMarket: true, comparableSellerCount: 10, comparableCount: 10,
  marketConfidence: 'moderate', marketMedian: 50000, savingAmount: 5000, savingPercent: 10,
  listing: {id: '1234567890', title: 'Sony PlayStation 5', price: 45000, acquisitionPrice: 45000,
    url: listingUrl('1234567890'), verificationStatus: 'verified', verifiedAt: new Date().toISOString()},
  analysis: {complete: true, matchesRequest: true, verdict: 'approve'}};
const ps4 = {...verified, discoveryStatus: 'mismatch', listing: {...verified.listing, id: '1234567891',
  title: 'Sony PlayStation 4 Slim', url: listingUrl('1234567891')},
  analysis: {...verified.analysis, matchesRequest: false, mismatchReason: 'Другое поколение'}};
const report = changes => ({mode: 'bargain', resultPolicy: 'verified_exact_matches_with_optional_savings', requestedResults: 3,
  recommendations: [verified], discoveredListings: [ps4], ...changes});
const render = changes => context.renderReport(report(changes));
const cards = () => (elements.results.innerHTML.match(/<article /g) || []).length;

assert.equal(render(), true);
assert.equal(cards(), 1);
assert(elements.results.innerHTML.includes('Sony PlayStation 5'));
assert(!elements.results.innerHTML.includes('Sony PlayStation 4'));
assert(elements.results.innerHTML.includes('Ниже рынка'));
assert(elements.summary.textContent.includes('Подтверждено: 1'));
assert(!elements.summary.textContent.includes('из 3'));
render({recommendations: [{...verified, historyWarnings: ['История ремонта не указана.', 'Оригинальность деталей не подтверждена.']}]});
assert.equal(cards(), 1);
assert(elements.results.innerHTML.includes('<p class="history-warning">История ремонта не указана. Оригинальность деталей не подтверждена.</p>'));
assert(elements.results.innerHTML.indexOf('class="history-warning"') < elements.results.innerHTML.indexOf('<details'));

render({recommendations: [{...verified, savingAmount: 300, savingPercent: 0.6}]});
assert.equal(cards(), 1);
assert(elements.results.innerHTML.includes('−0,6%'));
render({recommendations: [{...verified, savingAmount: 1, savingPercent: 0}]});
assert.equal(cards(), 1);
assert(!elements.results.innerHTML.includes('−0%'));
for (const count of [3, 5, 9]) {
  render({recommendations: [{...verified, belowMarket: false, belowComparables: true,
    comparableCount: count, comparableSellerCount: count, marketConfidence: 'limited', savingPercent: 0.6,
    reasons: ['Цена ниже медианы найденных аналогов. <script>unsafe()</script>']}]});
  assert.equal(cards(), 1);
  assert(elements.results.innerHTML.includes('Дешевле найденных аналогов'));
  assert(elements.results.innerHTML.includes(`Сравнили с ${count} продавцами · небольшая выборка`));
  assert(!elements.results.innerHTML.includes('deal-badge">Ниже рынка'));
  assert(!elements.results.innerHTML.includes('<script>'));
  assert(elements.results.innerHTML.includes('&lt;script&gt;unsafe()&lt;/script&gt;'));
}
for (const count of [0, 1, 2]) {
  render({recommendations: [{...verified, belowMarket: false, belowComparables: false,
    savingAmount: null, savingPercent: null, comparableCount: count, comparableSellerCount: count,
    marketConfidence: 'insufficient'}]});
  assert.equal(cards(), 1);
  assert(!elements.results.innerHTML.includes('deal-badge'));
  assert(elements.results.innerHTML.includes('Точное совпадение'));
  assert(elements.results.innerHTML.includes('Выгода не подтверждена'));
}

for (const bad of [ps4, {...verified, role: 'CAUTION'},
  {...verified, analysis: {...verified.analysis, matchesRequest: false}},
  {...verified, analysis: {...verified.analysis, complete: false}},
  {...verified, analysis: {...verified.analysis, conflicts: ['Название противоречит характеристикам']}},
  {...verified, listing: {...verified.listing, verifiedAt: ''}},
  {...verified, listing: {...verified.listing, verifiedAt: new Date(Date.now() - 16 * 60000).toISOString()}},
  {...verified, listing: {...verified.listing, url: 'javascript:alert(1)'}},
  {...verified, listing: {...verified.listing, acquisitionPrice: null}}]) {
  render({recommendations: [bad], warnings: ['Нет выгоды', 'Мало аналогов', 'Только 0 из 3']});
  assert.equal(cards(), 0);
  assert(elements.results.innerHTML.includes('Нет точных совпадений с завершёнными проверками и подтверждённой актуальной ценой'));
  assert.equal(elements.warnings.innerHTML, '');
  assert.equal(status, '');
  assert(!elements.results.innerHTML.includes('Измените запрос'));
}
render({recommendations: Array.from({length: 7}, (_, index) => ({...verified,
  listing: {...verified.listing, id: String(1234567800 + index), url: listingUrl(1234567800 + index)}}))});
assert.equal(cards(), 3);
render({recommendations: [verified, {...verified, listing: {...verified.listing, id: 'different',
  url: listingUrl('1234567890') + '?utm_source=duplicate'}}]});
assert.equal(cards(), 1);
render({recommendations: [], emptyReason: 'Найдены другие модели; они исключены.'});
assert(elements.results.innerHTML.includes('<p>Найдены другие модели; они исключены.</p>'));
render({recommendations: [], emptyReason: '<img src=x onerror=alert(1)>'});
assert(!elements.results.innerHTML.includes('<img'));
assert(elements.results.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;'));
render({recommendations: [], emptyReason: 'Нет точных совпадений с завершёнными проверками и подтверждённой актуальной ценой'});
assert.equal((elements.results.innerHTML.match(/Нет точных совпадений с завершёнными проверками и подтверждённой актуальной ценой/g) || []).length, 1);
assert(elements.results.innerHTML.includes('Мы не стали показывать объявления'));
render({emptyReason: 'Пустой результат'});
assert(!elements.results.innerHTML.includes('Пустой результат'));

const memoryBefore = remembered;
for (const invalid of [{resultPolicy: undefined}, {resultPolicy: 'legacy'}, {resultPolicy: 'verified_bargains_only'},
  {mode: 'find', resultPolicy: 'verified_matches_only'}, {mode: 'unknown', resultPolicy: undefined}]) {
  assert.equal(render(invalid), false);
  assert.equal(cards(), 0);
  assert(elements.results.innerHTML.includes('Поиск нужно повторить после обновления'));
}
assert.equal(remembered, memoryBefore);
assert(cleared >= 4);

context.activeSearchPayload = {mode: 'find', desiredResults: 5};
render({mode: 'find', resultPolicy: 'verified_matches_only', recommendations: [{...verified, belowMarket: false}]});
assert.equal(cards(), 1);
assert(!elements.results.innerHTML.includes('Ниже рынка'));
render({mode: 'find', resultPolicy: 'verified_matches_only', recommendations: []});
assert(elements.results.innerHTML.includes('Подтверждённых предложений, соответствующих запросу, пока нет'));
context.activeSearchPayload = {mode: 'bargain', desiredResults: 3};

render({recommendations: [], adminRecommendations: [ps4], adminDiscoveredListings: [ps4]});
assert.equal(elements.ownerPreview.innerHTML, '');
context.authenticatedOwner = true;
render({recommendations: [], adminRecommendations: [ps4], adminDiscoveredListings: [ps4]});
assert.equal(cards(), 0);
assert.equal((elements.ownerPreview.innerHTML.match(/<article /g) || []).length, 1);
assert(elements.ownerPreview.innerHTML.includes('Sony PlayStation 4'));
assert(!elements.ownerPreview.innerHTML.includes('deal-badge'));
render({preview: true, resultPolicy: undefined, adminRecommendations: [verified], adminDiscoveredListings: [ps4]});
assert.equal(cards(), 0);
assert(!elements.results.innerHTML.includes('Sony PlayStation'));
assert(elements.ownerPreview.innerHTML.includes('Диагностика владельца'));
assert(!elements.ownerPreview.innerHTML.includes('deal-badge'));

const fields = Object.fromEntries(Object.entries({query: 'PlayStation 5', location: 'Ярославль',
  category: 'gaming', requiredCondition: 'Отличное', priceMin: '20000', priceMax: '45000', priority: 'balanced', mode: 'bargain', desiredResults: '3'})
  .map(([key, value]) => [key, {value, setCustomValidity() {}, focus() {}}]));
fields['pickup-only'] = {checked: true};
let attributes = [{dataset: {attribute: 'storage'}, value: '1 ТБ'}, {dataset: {attribute: 'edition'}, value: 'Slim с дисководом'}];
const advanced = {open: false};
const listeners = {};
const resetButton = {addEventListener: (name, handler) => { listeners.resetClick = handler; }};
Object.assign(context, {form: {elements: fields, addEventListener: (name, handler) => { listeners[name] = handler; }},
  FormData: class { get(key) { return fields[key]?.value || ''; } },
  categorySelect: {get selectedOptions() { return [{textContent: fields.category.value === 'all' ? 'Определить автоматически' : 'Игры и приставки'}]; }}, dynamicFields: {},
  $$: () => attributes, $: selector => selector === '#reset-extra-filters' ? resetButton : advanced,
  renderDynamicFields: (category, values = {}) => {
    attributes = Object.entries(values).map(([key, value]) => ({dataset: {attribute: key}, value}));
  }, updateButtonLabel: () => {}, window: {setTimeout: fn => fn()},
});
vm.runInContext(extract('const CATEGORY_FIELDS =', '\nconst BASE_EXAMPLES ='), context);
vm.runInContext(extract('function optionalNumber(', '\nfunction renderDynamicFields('), context);
vm.runInContext(extract('function updateAdvancedHint(', '\nfunction searchPayload('), context);
vm.runInContext(extract('function searchPayload(', '\nfunction validateSearch('), context);
vm.runInContext(extract('function resetExtraFilters(', '\nfunction setStatus('), context);
vm.runInContext(extract('function applyExample(', '\nfunction updateTheme('), context);
vm.runInContext(extract("form.addEventListener('input'", "\nform.addEventListener('submit'"), context);
vm.runInContext(extract("$('#reset-extra-filters').addEventListener(", '\nexamplesList.addEventListener('), context);
const threeResultPayload = context.searchPayload();
assert.equal(threeResultPayload.pickupOnly, true);
fields['pickup-only'].checked = false;
assert.equal(context.searchPayload().pickupOnly, false);
context.updateAdvancedHint();
assert(!elements.activeFilters.textContent.includes('Только самовывоз'));
fields['pickup-only'].checked = true;
fields.desiredResults.value = '5';
const fiveResultPayload = context.searchPayload();
assert.equal(threeResultPayload.desiredResults, 3);
assert.equal(fiveResultPayload.desiredResults, 5);
assert.equal(threeResultPayload.maxResults, 200);
assert.equal(threeResultPayload.maxResults, fiveResultPayload.maxResults);
fields.desiredResults.value = '3';
context.updateAdvancedHint();
assert(elements.activeFilters.textContent.includes('Игры и приставки'));
assert(elements.activeFilters.textContent.includes('Состояние: Отличное'));
assert(elements.activeFilters.textContent.includes('Бюджет: от'));
assert(elements.activeFilters.textContent.includes('1 ТБ'));
assert(elements.activeFilters.textContent.includes('Slim с дисководом'));
assert(elements.activeFilters.textContent.includes('Только самовывоз'));
listeners.input({target: fields.query});
assert.equal(advanced.open, false, 'Typing a natural query must not force open advanced filters');
context.applyExample({query: 'Nintendo Switch OLED', category: 'gaming', condition: 'Хорошее',
  mode: 'bargain', priority: 'balanced', priceMin: '', priceMax: '', attributes: {storage: '64 ГБ'}});
assert.equal(fields.location.value, 'Ярославль');
assert.equal(fields.priceMin.value, '20000');
assert.equal(fields.priceMax.value, '45000');
assert.equal(fields['pickup-only'].checked, true, 'Examples preserve pickup-only choice');
fields.query.value = 'PlayStation 5';
fields.mode.value = 'find';
fields.priority.value = 'quality';
listeners.input({target: fields.query});
assert.equal(context.searchPayload().requiredStorage, '64 ГБ', 'Manual query edits do not silently reset filters');
const preservedFields = ['query', 'location', 'priceMin', 'priceMax', 'mode', 'desiredResults'];
const beforeReset = Object.fromEntries(preservedFields.map(key => [key, fields[key].value]));
listeners.resetClick();
for (const key of preservedFields) assert.equal(fields[key].value, beforeReset[key], `Reset must preserve ${key}`);
const afterReset = context.searchPayload();
assert.equal(afterReset.category, 'all');
assert.equal(afterReset.priority, 'balanced');
assert.equal(afterReset.requiredCondition, '');
assert.equal(afterReset.requiredStorage, '');
assert.equal(Object.keys(afterReset.attributes).length, 0);
assert.equal(afterReset.location, 'Ярославль');
assert.equal(afterReset.priceMin, 20000);
assert.equal(afterReset.priceMax, 45000);
assert.equal(afterReset.pickupOnly, true, 'Reset preserves selected pickup-only choice');
assert.equal(advanced.open, true);
assert(elements.activeFilters.textContent.includes('Определить автоматически'));
assert(elements.activeFilters.textContent.includes('Только самовывоз'));
fields['pickup-only'].checked = false;
listeners.resetClick();
assert.equal(context.searchPayload().pickupOnly, false, 'Reset also preserves unchecked pickup-only choice');

(async () => {
  let requests = 0;
  let searches = 0;
  Object.assign(context, {ACTIVE_JOB_KEY: 'test-job', JOB_RETENTION_MS: 15 * 60000,
    storage: {get: () => JSON.stringify({jobId: 'saved-old', payload: {query: 'PlayStation 5', mode: 'bargain'}, savedAt: Date.now()})},
    requestJSON: async (url, options) => {
      requests += 1;
      assert.equal(url, '/api/avito/jobs/saved-old');
      assert(!options.method || options.method === 'GET');
      return {state: 'complete', result: report({resultPolicy: 'verified_bargains_only'})};
    },
    startSearch: () => { searches += 1; },
    renderSearchError: error => { throw error; },
  });
  vm.runInContext(extract('async function resumeSavedJob(', "\nrenderDynamicFields('all');"), context);
  await context.resumeSavedJob();
  assert.equal(requests, 1);
  assert.equal(searches, 0, 'A legacy result must never start another paid search automatically');
  assert.equal(cards(), 0);
  assert(elements.results.innerHTML.includes('Поиск нужно повторить после обновления'));
  process.stdout.write('Avito results presentation: all scenarios passed\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
