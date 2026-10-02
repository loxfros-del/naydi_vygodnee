(function initQueryNormalization(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.NaydiQueryNormalization = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  'use strict';

  const CONDITION_ALIASES = [
    {pattern: /(?:^|\s)(?:идеал|идеальн[а-яё]*)(?:\s+состояни[а-яё]*)?(?=\s|$)/iu, value: 'Отличное'},
    {pattern: /(?:^|\s)отличн[а-яё]*(?:\s+состояни[а-яё]*)?(?=\s|$)/iu, value: 'Отличное'},
    {pattern: /(?:^|\s)хорош[а-яё]*(?:\s+состояни[а-яё]*)?(?=\s|$)/iu, value: 'Хорошее'},
    {pattern: /(?:^|\s)нов[а-яё]*(?:\s+состояни[а-яё]*)?(?=\s|$)/iu, value: 'Новое'},
  ];
  const CITY_ALIASES = [
    {pattern: /(?:^|\s)(?:мск|москва)(?=\s|$)/iu, value: 'Москва'},
    {pattern: /(?:^|\s)(?:спб|санкт[-\s]?петербург)(?=\s|$)/iu, value: 'Санкт-Петербург'},
    {pattern: /(?:^|\s)ярославл[ья](?=\s|$)/iu, value: 'Ярославль'},
  ];
  const PRODUCT_HINTS = [
    {family: 'iphone', category: 'phones', pattern: /(?:iphone|айфон)\s*16\s*(?:pro|про)(?=\s|$)/iu, model: 'iPhone 16 Pro'},
    {family: 'macbook-air', category: 'computers', pattern: /(?:macbook|макбук)\s*air\s*m2\b/iu, model: 'MacBook Air M2'},
    {family: 'ps5', category: 'gaming', pattern: /(?:\bps\s*5\b|\bпс\s*5\b|play\s*station\s*5|playstation\s*5|плойк\w*\s*5)/iu, model: 'PlayStation 5'},
  ];

  const compact = (value) => String(value || '').trim().replace(/\s+/g, ' ');

  function normalizeStorage(text) {
    const match = String(text).match(/\b(\d{2,4})\s*(гб|gb|тб|tb)\b/iu);
    if (!match) return '';
    const unit = /тб|tb/iu.test(match[2]) ? 'ТБ' : 'ГБ';
    return `${Number(match[1])} ${unit}`;
  }

  function normalizeBudget(text) {
    const match = String(text).match(/(?:^|\s)(?:до|максимум|бюджет(?:ом)?\s*(?:до)?)\s*(\d[\d\s]{0,8})(?:\s*(к|k|тыс(?:яч)?\.?))?(?=\s|$)/iu);
    if (!match) return null;
    const numeric = Number(match[1].replace(/\s/g, ''));
    if (!Number.isFinite(numeric)) return null;
    const multiplier = match[2] ? 1000 : 1;
    const value = numeric * multiplier;
    return value > 0 && value <= 100_000_000 ? value : null;
  }

  function canonicalizeQuery(text) {
    return compact(text)
      .replace(/(^|\s)айфон(?=\s|$)/giu, '$1iPhone')
      .replace(/(^|\s)макбук(?=\s|$)/giu, '$1MacBook')
      .replace(/\b(?:пс\s*5|ps\s*5|плойк\w*\s*5)\b/giu, 'PlayStation 5')
      .replace(/(\d{2,4})\s*(?:гб|gb)\b/giu, '$1 GB')
      .replace(/(\d{1,2})\s*(?:тб|tb)\b/giu, '$1 TB');
  }

  function normalizeSearchQuery(value) {
    const originalQuery = compact(value);
    const canonicalQuery = canonicalizeQuery(originalQuery);
    const hint = PRODUCT_HINTS.find((item) => item.pattern.test(originalQuery));
    const recognized = [];
    const fields = {};
    const attributes = {};

    if (hint) recognized.push({key: 'model', label: 'Модель', value: hint.model});

    const condition = CONDITION_ALIASES.find((item) => item.pattern.test(originalQuery));
    if (condition) {
      fields.requiredCondition = condition.value;
      recognized.push({key: 'requiredCondition', label: 'Состояние', value: condition.value});
    }

    const city = CITY_ALIASES.find((item) => item.pattern.test(originalQuery));
    if (city) {
      fields.location = city.value;
      recognized.push({key: 'location', label: 'Город', value: city.value});
    }

    const impliedStorage = hint?.family === 'iphone'
      ? originalQuery.match(/(?:^|\s)(64|128|256|512|1024)(?=\s|$)/u)?.[1] : '';
    const storage = normalizeStorage(originalQuery) || (impliedStorage ? `${Number(impliedStorage)} ГБ` : '');
    if (storage) {
      fields.requiredStorage = storage;
      attributes.storage = storage;
      recognized.push({key: 'storage', label: 'Память', value: storage});
    }

    const priceMax = normalizeBudget(originalQuery);
    if (priceMax) {
      fields.priceMax = priceMax;
      recognized.push({key: 'priceMax', label: 'Бюджет до', value: `${new Intl.NumberFormat('ru-RU').format(priceMax)} ₽`});
    }

    if (hint?.family === 'ps5') {
      const form = /\b(?:slim|слим)\b/iu.test(originalQuery) ? 'Slim'
        : /\bpro\b|\bпро\b/iu.test(originalQuery) ? 'Pro' : '';
      const drive = /без\s+дисковод\w*|\bdigital\b|\bцифров\w*/iu.test(originalQuery) ? 'Digital'
        : /с\s+дисковод\w*|\bdisc\b/iu.test(originalQuery) ? 'с дисководом' : '';
      const edition = [form, drive].filter(Boolean).join(' ');
      if (edition) {
        attributes.edition = edition;
        recognized.push({key: 'edition', label: 'Версия', value: edition});
      }
    }

    return {
      originalQuery,
      canonicalQuery,
      family: hint?.family || '',
      category: hint?.category || 'all',
      recognized,
      fields,
      attributes,
    };
  }

  return {normalizeSearchQuery, normalizeStorage, normalizeBudget};
}));
