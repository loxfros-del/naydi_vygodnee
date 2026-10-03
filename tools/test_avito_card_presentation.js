// Exercise the actual card renderer with deterministic data; no browser/network.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'avito_web', 'app.js'), 'utf8');
const start = source.indexOf('const DISCOVERY_STATUSES =');
const end = source.indexOf('\nfunction clientWarning(', start);
assert(start >= 0 && end > start);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const context = vm.createContext({escapeHtml, money: value => `${value} ₽`,
  safeUrl: () => '', safeListingUrl: value => value || '', listingCity: value => value || '',
  formatCollectedAt: () => ''});
vm.runInContext(source.slice(start, end), context);
const base = {role: 'TOP', belowMarket: true, marketConfidence: 'moderate',
  comparableSellerCount: 10, comparableCount: 10, savingAmount: 5000, savingPercent: 10,
  marketMedian: 50000, listing: {title: 'Телефон <пример>', price: 45000, acquisitionPrice: 45000,
    verificationStatus: 'verified', verifiedAt: new Date().toISOString(),
    url: 'https://www.avito.ru/moskva/telefony/phone_1234567890'},
  analysis: {complete: true, matchesRequest: true, verdict: 'approve'}};
const render = (changes = {}, mode = 'bargain') => context.renderCard({...base, ...changes}, 0, mode);
assert(render().includes('deal-badge'));
assert(render().includes('Экономия 5000'));
assert(render().includes('Телефон &lt;пример&gt;'));
for (const count of [3, 4, 9]) {
  const result = render({belowMarket: false, belowComparables: true, marketConfidence: 'limited',
    comparableCount: count, comparableSellerCount: count, savingAmount: 300, savingPercent: 0.6});
  assert(result.includes('Дешевле найденных аналогов'));
  assert(result.includes(`Сравнили с ${count} продавцами · небольшая выборка`));
  assert(result.includes('Экономия 300'));
  assert(result.includes('−0,6%'));
  assert(!result.includes('deal-badge">Ниже рынка'));
}
for (const changes of [{comparableSellerCount: 2}, {comparableCount: 2}, {marketConfidence: 'insufficient'},
  {savingAmount: 0}, {savingPercent: -1}]) {
  const result = render({belowMarket: false, belowComparables: true, marketConfidence: 'limited',
    comparableSellerCount: 3, comparableCount: 3, ...changes});
  assert(!result.includes('deal-badge'));
  assert(!result.includes('Дешевле найденных аналогов'));
}
const limitedZeroPercent = render({belowMarket: false, belowComparables: true, marketConfidence: 'limited',
  comparableSellerCount: 3, comparableCount: 3, savingAmount: 1, savingPercent: 0});
assert(limitedZeroPercent.includes('Экономия 1'));
assert(!limitedZeroPercent.includes('−0%'));
const unsafeLimited = render({belowMarket: false, belowComparables: true, marketConfidence: 'limited',
  comparableSellerCount: 3, comparableCount: 3, reasons: ['<img src=x onerror=alert(1)>']});
assert(unsafeLimited.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert(!unsafeLimited.includes('<img src=x'));
const uncertainHistory = render({historyWarnings: ['История ремонта не указана.', 'Оригинальность деталей не подтверждена. <script>unsafe()</script>']});
assert(uncertainHistory.includes('deal-badge'), 'Missing history is disclosed, not rejected by the renderer');
assert(uncertainHistory.includes('<p class="history-warning">История ремонта не указана.'));
assert(uncertainHistory.indexOf('class="history-warning"') < uncertainHistory.indexOf('<details'));
assert(!uncertainHistory.includes('<script>'));
assert(uncertainHistory.includes('&lt;script&gt;unsafe()&lt;/script&gt;'));
assert(!render().includes('class="history-warning"'));
const exactWithoutMarket = render({belowMarket: false, belowComparables: false,
  comparableCount: 0, comparableSellerCount: 0, savingAmount: null, savingPercent: null});
assert(exactWithoutMarket.includes('Точное совпадение'));
assert(exactWithoutMarket.includes('Выгода не подтверждена'));
assert(!exactWithoutMarket.includes('deal-badge'));
assert(!exactWithoutMarket.includes('Экономия '));
assert(exactWithoutMarket.includes('Проверьте товар у продавца перед покупкой.'));
for (const role of ['CAUTION', 'REJECTED', 'DO_NOT_BUY', 'UNKNOWN']) {
  const result = render({role});
  assert(!result.includes('deal-badge'));
  assert(!result.includes('Экономия 5000'));
  assert(result.includes('Выгода не подтверждена'));
}
for (const changes of [{marketConfidence: 'insufficient'}, {comparableSellerCount: 9},
  {discoveryStatus: 'has_risks'}, {listing: {...base.listing, acquisitionPrice: null}},
  {analysis: {...base.analysis, matchesRequest: false}},
  {analysis: {...base.analysis, mismatchReason: 'В объявлении PlayStation 4'}},
  {listing: {...base.listing, verifiedAt: new Date(Date.now() - 16 * 60000).toISOString()}},
  {savingAmount: 0}]) {
  const result = render(changes);
  assert(!result.includes('deal-badge'));
  assert(!result.includes('Экономия 5000'));
  assert(result.includes('Открыть на Avito'));
}
assert(!render({}, 'preview').includes('deal-badge'));
assert(render({analysis: {complete: false}, reasons: []}).includes('Основание выбора уточняется'));
process.stdout.write('Avito card presentation: all scenarios passed\n');
