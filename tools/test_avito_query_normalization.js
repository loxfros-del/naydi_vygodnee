'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {normalizeSearchQuery} = require(path.join(__dirname, '..', 'avito_web', 'query_normalization.js'));

const ps5 = normalizeSearchQuery('ps5');
assert.equal(ps5.family, 'ps5');
assert.equal(ps5.category, 'gaming');
assert.equal(ps5.canonicalQuery, 'PlayStation 5');
assert.equal(ps5.attributes.edition, undefined, 'Generic PS5 must not force a version');

const explicitIphone = normalizeSearchQuery('iphone 16 pro идеал состояние');
assert.equal(explicitIphone.family, 'iphone');
assert.equal(explicitIphone.fields.requiredCondition, 'Отличное');
assert(explicitIphone.recognized.some((item) => item.value === 'iPhone 16 Pro'));

const compactIphone = normalizeSearchQuery('айфон 16 про 256 мск до 100к');
assert.equal(compactIphone.family, 'iphone');
assert.equal(compactIphone.category, 'phones');
assert.equal(compactIphone.fields.requiredStorage, '256 ГБ');
assert.equal(compactIphone.fields.location, 'Москва');
assert.equal(compactIphone.fields.priceMax, 100000);
assert.match(compactIphone.canonicalQuery, /^iPhone 16 про 256/);

const macbook = normalizeSearchQuery('макбук air m2 в хорошем состоянии');
assert.equal(macbook.category, 'computers');
assert.equal(macbook.fields.requiredCondition, 'Хорошее');

process.stdout.write('Avito query normalization: all scenarios passed\n');
