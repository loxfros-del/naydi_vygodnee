const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const form = $('#search-form');
const fileInput = $('#dataset-file');
const statusBox = $('#status');
const results = $('#results');
const warnings = $('#warnings');
const summary = $('#summary');
const readiness = $('#readiness');
const costs = $('#costs');
const audit = $('#audit');
const adminWarnings = $('#admin-warnings');
const operatorPanel = $('#operator-panel');
const accessDialog = $('#access-dialog');
const accessForm = $('#access-form');
const accessStatus = $('#access-status');
const submitButton = $('.primary', form);
const buttonLabel = $('.button-label', submitButton);
const categorySelect = $('#category-select');
const dynamicFields = $('#dynamic-fields');
const advancedHint = $('#advanced-hint');
const activeFilters = $('#active-filters');
const ownerPreview = $('#owner-preview');
const liveProgress = $('#live-progress');
const progressBar = $('#progress-bar');
const progressMessage = $('#progress-message');
const progressTime = $('#progress-time');
const progressTitle = $('#progress-title');
const progressJob = $('#progress-job');
const cancelSearchButton = $('#cancel-search');
const recognizedPanel = $('#recognized-panel');
const recognizedParameters = $('#recognized-parameters');
const ps5QuickFilters = $('#ps5-quick-filters');
const ps5Version = $('#ps5-version');
const ps5Drive = $('#ps5-drive');
const ps5Condition = $('#ps5-condition');
const lastSearchBox = $('#last-search');
const lastSearchLabel = $('#last-search-label');
const supportDialog = $('#support-dialog');
const supportForm = $('#support-form');
const supportStatus = $('#support-status');
const examplesPanel = $('.examples-panel');
const examplesList = $('#examples-list');
const examplesPrev = $('#examples-prev');
const examplesPause = $('#examples-pause');
const examplesNext = $('#examples-next');
const examplesPage = $('#examples-page');
const menuToggle = $('#menu-toggle');
const siteNav = $('#site-nav');
const themeToggle = $('#theme-toggle');

const escapeHtml = (value) => String(value ?? '')
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

const storage = {
  get(key, fallback = '') {
    try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ }
  },
  remove(key) {
    try { localStorage.removeItem(key); } catch { /* Storage is optional. */ }
  },
};

const CATEGORY_FIELDS = {
  all: [
    {key: 'details', label: 'Что обязательно должно быть', placeholder: 'Размер, материал, комплект или особенность', wide: true},
  ],
  phones: [
    {key: 'storage', label: 'Память', placeholder: 'Например, 256 ГБ'},
    {key: 'sim', label: 'SIM / регион', placeholder: 'Например, SIM + eSIM'},
    {key: 'color', label: 'Цвет', placeholder: 'Например, чёрный'},
  ],
  computers: [
    {key: 'ram', label: 'Оперативная память', placeholder: 'Например, от 16 ГБ'},
    {key: 'storage', label: 'Накопитель', placeholder: 'Например, SSD от 512 ГБ'},
    {key: 'screen', label: 'Экран', placeholder: 'Например, 15–16 дюймов'},
  ],
  household: [
    {key: 'appliance_type', label: 'Тип техники', placeholder: 'Например, стиральная машина'},
    {key: 'dimensions', label: 'Размер / габариты', placeholder: 'Например, ширина до 45 см'},
    {key: 'load', label: 'Вместимость / загрузка', placeholder: 'Например, от 6 кг'},
    {key: 'details', label: 'Комплект / особенности', placeholder: 'Например, зарядная база и насадки'},
  ],
  furniture: [
    {key: 'dimensions', label: 'Размер', placeholder: 'Например, высота от 120 см'},
    {key: 'material', label: 'Материал', placeholder: 'Например, ткань'},
    {key: 'details', label: 'Особенности', placeholder: 'Например, нагрузка от 120 кг'},
  ],
  gaming: [
    {key: 'edition', label: 'Версия', placeholder: 'Например, Slim с дисководом'},
    {key: 'storage', label: 'Память', placeholder: 'Например, 1 ТБ'},
    {key: 'details', label: 'Комплект', placeholder: 'Например, коробка и геймпад'},
  ],
  tools: [
    {key: 'power', label: 'Мощность', placeholder: 'Например, от 1200 Вт'},
    {key: 'size', label: 'Размер / диаметр', placeholder: 'Например, диск 125 мм'},
    {key: 'details', label: 'Комплект', placeholder: 'Например, аккумулятор и кейс'},
  ],
  auto: [
    {key: 'compatibility', label: 'Марка и модель', placeholder: 'Например, Kia Rio 4'},
    {key: 'year', label: 'Год / поколение', placeholder: 'Например, 2017–2020'},
    {key: 'details', label: 'Артикул / сторона', placeholder: 'Например, передняя левая'},
  ],
  fashion: [
    {key: 'size', label: 'Размер', placeholder: 'Например, 46 / M'},
    {key: 'material', label: 'Материал', placeholder: 'Например, натуральная кожа'},
    {key: 'color', label: 'Цвет', placeholder: 'Например, коричневый'},
  ],
  other: [
    {key: 'details', label: 'Опишите обязательные признаки', placeholder: 'Форма, материал, примерный размер и назначение', wide: true},
  ],
};

const BASE_EXAMPLES = [
  {id: 'iphone-15-pro', title: 'iPhone 15 Pro · 256 ГБ', description: 'SIM + eSIM · отличное состояние', query: 'Apple iPhone 15 Pro 256 ГБ', category: 'phones', mode: 'bargain', priority: 'balanced', condition: 'Отличное', attributes: {storage: '256 ГБ', sim: 'SIM + eSIM'}},
  {id: 'galaxy-s23', title: 'Samsung Galaxy S23 · 256 ГБ', description: 'точная модель · отличное состояние', query: 'Samsung Galaxy S23 256 ГБ', category: 'phones', mode: 'bargain', priority: 'balanced', condition: 'Отличное', attributes: {storage: '256 ГБ'}},
  {id: 'macbook-air-m1', title: 'MacBook Air M1 · 8/256', description: '13,3″ · SSD · отличное состояние', query: 'Apple MacBook Air M1 8/256 ГБ 13 дюймов', category: 'computers', mode: 'bargain', priority: 'balanced', condition: 'Отличное', attributes: {ram: '8 ГБ', storage: 'SSD 256 ГБ', screen: '13–14 дюймов'}},
  {id: 'ps5-slim', title: 'PlayStation 5 Slim', description: 'дисковод · 1 ТБ · геймпад', query: 'Sony PlayStation 5 Slim с дисководом 1 ТБ', category: 'gaming', mode: 'bargain', priority: 'balanced', condition: 'Отличное', attributes: {edition: 'Slim с дисководом', storage: '1 ТБ', details: 'оригинальный геймпад и кабели'}},
  {id: 'switch-oled', title: 'Nintendo Switch OLED', description: '64 ГБ · полный комплект', query: 'Nintendo Switch OLED 64 ГБ', category: 'gaming', mode: 'bargain', priority: 'balanced', condition: 'Отличное', attributes: {edition: 'OLED', storage: '64 ГБ', details: 'док-станция, Joy-Con и блок питания'}},
  {id: 'dyson-v8', title: 'Dyson V8 Absolute', description: 'точная модель · комплект насадок', query: 'пылесос Dyson V8 Absolute', category: 'household', mode: 'bargain', priority: 'quality', condition: 'Хорошее', attributes: {appliance_type: 'вертикальный пылесос', details: 'зарядка и основные насадки'}},
  {id: 'lg-washer', title: 'LG F2V3GS6W', description: 'узкая стиральная машина · 8,5 кг', query: 'стиральная машина LG F2V3GS6W', category: 'household', mode: 'bargain', priority: 'balanced', condition: 'Хорошее', attributes: {appliance_type: 'стиральная машина LG F2V3GS6W', dimensions: 'глубина до 48 см', load: '8,5 кг'}},
  {id: 'ikea-markus', title: 'Кресло IKEA MARKUS', description: 'ткань · высокая спинка', query: 'офисное кресло IKEA MARKUS тканевое', category: 'furniture', mode: 'bargain', priority: 'quality', condition: 'Хорошее', attributes: {material: 'ткань', details: 'высокая спинка, исправный газлифт и механизм качания'}},
  {id: 'makita-ddf485', title: 'Makita DDF485', description: '18 В · аккумулятор и кейс', query: 'шуруповёрт Makita DDF485 18 В', category: 'tools', mode: 'bargain', priority: 'balanced', condition: 'Хорошее', attributes: {power: '18 В', details: 'аккумулятор, зарядка и кейс'}},
  {id: 'robot-s10', title: 'Xiaomi Robot Vacuum S10', description: 'точная модель · база и зарядка', query: 'робот-пылесос Xiaomi Robot Vacuum S10', category: 'household', mode: 'bargain', priority: 'balanced', condition: 'Отличное', attributes: {appliance_type: 'робот-пылесос', details: 'зарядная база и контейнеры'}},
  {id: 'coach-tabby', title: 'Coach Tabby 26', description: 'кожаная сумка · чёрная', query: 'сумка Coach Tabby 26 чёрная', category: 'fashion', mode: 'find', priority: 'quality', condition: 'Отличное', attributes: {material: 'натуральная кожа', color: 'чёрный', details: 'модель Tabby 26'}},
  {id: 'aquarium-root', title: 'Коряга для аквариума', description: 'натуральное дерево · 80–120 см', query: 'натуральная коряга для аквариума 80–120 см', category: 'other', mode: 'find', priority: 'quality', condition: '', attributes: {details: 'изогнутая форма, длина 80–120 см, без гнили и покрытия'}},
];

const VERIFIED_EXAMPLES_KEY = 'naydi-verified-examples-v3';
const ACTIVE_JOB_KEY = 'naydi-active-job-v1';
const LAST_SEARCH_KEY = 'naydi-last-search-v1';
const EXAMPLES_PER_PAGE = 3;
const EXAMPLE_ROTATION_MS = 7000;
const SEARCH_LIMIT_MS = 375000;
const SOFT_TARGET_SECONDS = 45;
const JOB_RETENTION_MS = 15 * 60 * 1000;
let activeJobId = null;
let activeJobIsDataset = false;
let authenticatedOwner = false;
let accessToken = '';
try { accessToken = sessionStorage.getItem('naydi-access-token') || ''; } catch { /* Optional. */ }
let examplePageIndex = 0;
let exampleTimer = null;
let examplesPaused = false;
let activeSearchPayload = null;
let lastSearchPayload = null;
let progressStartedAt = 0;
let progressTicker = null;
let autoRecognizedFields = {};
let searchInFlight = false;
let cancelInFlight = false;
let searchGeneration = 0;

class ClientError extends Error {
  constructor(message, {code = 'CLIENT_ERROR', retryable = true, status = 0} = {}) {
    super(message);
    this.name = 'ClientError';
    this.code = code;
    this.retryable = retryable;
    this.status = status;
  }
}

function loadVerifiedExamples() {
  try {
    const saved = JSON.parse(storage.get(VERIFIED_EXAMPLES_KEY, '[]'));
    return Array.isArray(saved) ? saved.filter((item) => item?.query && item?.title).slice(0, 3) : [];
  } catch {
    return [];
  }
}

let verifiedExamples = loadVerifiedExamples();

function examplePool() {
  const seen = new Set();
  return [...verifiedExamples, ...BASE_EXAMPLES].filter((example) => {
    const key = `${example.category}:${String(example.query).toLocaleLowerCase('ru-RU')}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderExamples() {
  const pool = examplePool();
  const pageCount = Math.max(1, Math.ceil(pool.length / EXAMPLES_PER_PAGE));
  examplePageIndex = ((examplePageIndex % pageCount) + pageCount) % pageCount;
  const visible = Array.from({length: Math.min(EXAMPLES_PER_PAGE, pool.length)}, (_, index) => pool[(examplePageIndex * EXAMPLES_PER_PAGE + index) % pool.length]);
  examplesList.innerHTML = visible.map((example) => `<button type="button" data-example-id="${escapeHtml(example.id)}" class="${example.verified ? 'verified' : ''}" aria-label="Заполнить пример: ${escapeHtml(example.title)}"><b>${escapeHtml(example.title)}</b><small>${escapeHtml(example.description)}</small></button>`).join('');
  examplesPage.textContent = `${examplePageIndex + 1} / ${pageCount}`;
}

function changeExamplePage(direction = 1) {
  const pageCount = Math.max(1, Math.ceil(examplePool().length / EXAMPLES_PER_PAGE));
  if (pageCount <= 1) return;
  examplesList.classList.add('is-changing');
  window.setTimeout(() => {
    examplePageIndex = (examplePageIndex + direction + pageCount) % pageCount;
    renderExamples();
    examplesList.classList.remove('is-changing');
  }, 150);
}

function stopExampleRotation() {
  if (exampleTimer) window.clearInterval(exampleTimer);
  exampleTimer = null;
}

function startExampleRotation() {
  stopExampleRotation();
  const compact = window.matchMedia('(max-width: 780px)').matches;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!examplesPaused && !compact && !reduceMotion && !document.hidden) {
    exampleTimer = window.setInterval(() => changeExamplePage(1), EXAMPLE_ROTATION_MS);
  }
}

function setExamplesPaused(paused) {
  examplesPaused = paused;
  examplesPause.textContent = paused ? '▶' : 'Ⅱ';
  examplesPause.setAttribute('aria-pressed', String(paused));
  examplesPause.setAttribute('aria-label', paused ? 'Продолжить смену примеров' : 'Остановить смену примеров');
  if (paused) stopExampleRotation(); else startExampleRotation();
}

function rememberVerifiedExample(payload, count) {
  if (!payload || count < 3) return;
  const descriptionParts = [...Object.values(payload.attributes || {}).filter(Boolean).slice(0, 2), `вариантов: ${count}`];
  const example = {
    id: `verified-${String(payload.query).toLocaleLowerCase('ru-RU').replace(/[^a-zа-яё0-9]+/gi, '-').slice(0, 48)}`,
    title: payload.query,
    description: descriptionParts.join(' · '),
    query: payload.query,
    category: payload.category,
    mode: payload.mode,
    priority: payload.priority,
    condition: payload.requiredCondition,
    priceMin: payload.priceMin ?? '',
    priceMax: payload.priceMax ?? '',
    attributes: payload.attributes || {},
    verified: true,
  };
  verifiedExamples = [example, ...verifiedExamples.filter((item) => item.id !== example.id)].slice(0, 3);
  storage.set(VERIFIED_EXAMPLES_KEY, JSON.stringify(verifiedExamples));
  examplePageIndex = 0;
  renderExamples();
}

function optionalNumber(value) {
  if (value === null || String(value).trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function renderDynamicFields(category, values = {}) {
  const fields = CATEGORY_FIELDS[category] || CATEGORY_FIELDS.all;
  dynamicFields.innerHTML = fields.map((field) => `<label class="field ${field.wide ? 'wide' : ''}"><span>${escapeHtml(field.label)}</span><input data-attribute="${escapeHtml(field.key)}" value="${escapeHtml(values[field.key] || '')}" placeholder="${escapeHtml(field.placeholder)}" maxlength="160"></label>`).join('');
  updateAdvancedHint();
}

function updateAdvancedHint() {
  const data = new FormData(form);
  const min = String(data.get('priceMin') || '').trim();
  const max = String(data.get('priceMax') || '').trim();
  const category = categorySelect.selectedOptions?.[0]?.textContent || 'Определить автоматически';
  const filters = [`Категория: ${category}`, `Состояние: ${data.get('requiredCondition') || 'любое'}`];
  if (form.elements['pickup-only']?.checked === true) filters.push('Только самовывоз');
  filters.push(`Бюджет: ${min ? `от ${money(min)}` : ''}${min && max ? ' ' : ''}${max ? `до ${money(max)}` : ''}${!min && !max ? 'не ограничен' : ''}`);
  filters.push(`Приоритет: ${{quality: 'надёжность', balanced: 'баланс', budget: 'цена'}[data.get('priority') || 'balanced']}`);
  const fieldLabels = CATEGORY_FIELDS[data.get('category')] || CATEGORY_FIELDS.all;
  $$('[data-attribute]', dynamicFields).forEach((input) => {
    const value = input.value.trim();
    if (value) filters.push(`${fieldLabels.find((field) => field.key === input.dataset.attribute)?.label || input.dataset.attribute}: ${value}`);
  });
  activeFilters.textContent = filters.join(' · ');
  advancedHint.textContent = 'изменить';
}

function clearAutoRecognizedFields() {
  for (const [name, record] of Object.entries(autoRecognizedFields)) {
    const value = typeof record === 'object' ? record.value : record;
    const previous = typeof record === 'object' ? record.previous : '';
    if (name.startsWith('attribute:')) {
      const input = $(`[data-attribute="${name.slice(10)}"]`, dynamicFields);
      if (input && input.value === String(value)) input.value = String(previous || '');
    } else if (form.elements[name] && String(form.elements[name].value) === String(value)) {
      form.elements[name].value = String(previous ?? (name === 'category' ? 'all' : ''));
    }
  }
  autoRecognizedFields = {};
}

function applyRecognizedValue(name, value, {replaceDefault = false} = {}) {
  const input = form.elements[name];
  if (!input || value == null || value === '') return;
  const empty = String(input.value || '').trim() === '' || (name === 'category' && input.value === 'all');
  if (empty || replaceDefault) {
    const previous = input.value;
    input.value = String(value);
    autoRecognizedFields[name] = {value: String(value), previous};
  }
}

function renderRecognizedQuery(recognition) {
  const values = recognition?.recognized || [];
  recognizedPanel.hidden = values.length === 0;
  recognizedParameters.innerHTML = values.map((item) => (
    `<button type="button" class="recognized-chip" data-recognized-key="${escapeHtml(item.key)}" title="Изменить параметр в фильтрах"><span>${escapeHtml(item.label)}</span>${escapeHtml(item.value)}<i aria-hidden="true">↗</i></button>`
  )).join('');
  const ps5 = recognition?.family === 'ps5';
  ps5QuickFilters.hidden = !ps5;
  if (!ps5) {
    ps5Version.value = '';
    ps5Drive.value = '';
    ps5Condition.value = '';
    globalThis.NaydiPs5Selection = null;
  }
}

function applyQueryRecognition() {
  const normalize = globalThis.NaydiQueryNormalization?.normalizeSearchQuery;
  if (typeof normalize !== 'function') return null;
  const recognition = normalize(form.elements.query.value);
  clearAutoRecognizedFields();
  const category = recognition.category || 'all';
  if (category !== 'all' && (categorySelect.value === 'all' || autoRecognizedFields.category)) {
    applyRecognizedValue('category', category);
    renderDynamicFields(category, recognition.attributes);
  } else {
    for (const [key, value] of Object.entries(recognition.attributes || {})) {
      const input = $(`[data-attribute="${key}"]`, dynamicFields);
      if (input && !input.value.trim()) {
        const previous = input.value;
        input.value = value;
        autoRecognizedFields[`attribute:${key}`] = {value, previous};
      }
    }
  }
  applyRecognizedValue('requiredCondition', recognition.fields.requiredCondition);
  applyRecognizedValue('location', recognition.fields.location, {replaceDefault: Boolean(recognition.fields.location)});
  applyRecognizedValue('priceMax', recognition.fields.priceMax);
  globalThis.NaydiCurrentRecognition = recognition;
  renderRecognizedQuery(recognition);
  updateAdvancedHint();
  return recognition;
}

function scheduleQueryRecognition() {
  if (typeof globalThis.NaydiQueryNormalization?.normalizeSearchQuery !== 'function') return;
  if (globalThis.NaydiRecognitionTimer) window.clearTimeout?.(globalThis.NaydiRecognitionTimer);
  globalThis.NaydiRecognitionTimer = window.setTimeout(applyQueryRecognition, 280);
}
globalThis.scheduleQueryRecognition = scheduleQueryRecognition;

function applyPs5QuickFilters() {
  if (globalThis.NaydiCurrentRecognition?.family !== 'ps5') return;
  const parts = [ps5Version.value, ps5Drive.value].filter(Boolean);
  const edition = parts.join(' ');
  const input = $('[data-attribute="edition"]', dynamicFields);
  if (input) input.value = edition;
  if (ps5Condition.value) form.elements.requiredCondition.value = ps5Condition.value;
  globalThis.NaydiPs5Selection = {
    version: ps5Version.value,
    drive: ps5Drive.value,
  };
  updateAdvancedHint();
}

function searchPayload() {
  const data = new FormData(form);
  const desiredResults = Number(data.get('desiredResults') || 5);
  const attributes = {};
  $$('[data-attribute]', dynamicFields).forEach((input) => {
    const value = input.value.trim();
    if (value) attributes[input.dataset.attribute] = value;
  });
  const originalQuery = String(data.get('query') || '').trim();
  const recognition = globalThis.NaydiCurrentRecognition;
  let query = recognition?.originalQuery === originalQuery ? recognition.canonicalQuery : originalQuery;
  const ps5Selection = recognition?.family === 'ps5' ? globalThis.NaydiPs5Selection : null;
  if (ps5Selection) query = [query, ps5Selection.version, ps5Selection.drive].filter(Boolean).join(' ');
  return {
    query,
    location: String(data.get('location') || '').trim(),
    pickupOnly: form.elements['pickup-only']?.checked === true,
    category: data.get('category') || 'all',
    mode: data.get('mode') || 'bargain',
    priority: data.get('priority') || 'balanced',
    desiredResults,
    // The output quota must not shrink the evidence collection pool.
    maxResults: 200,
    priceMin: optionalNumber(data.get('priceMin')),
    priceMax: optionalNumber(data.get('priceMax')),
    requiredStorage: attributes.storage || '',
    requiredSim: attributes.sim || '',
    requiredCondition: String(data.get('requiredCondition') || '').trim(),
    attributes,
  };
}

function validateSearch() {
  const minInput = form.elements.priceMin;
  const maxInput = form.elements.priceMax;
  minInput.setCustomValidity('');
  maxInput.setCustomValidity('');
  const min = optionalNumber(minInput.value);
  const max = optionalNumber(maxInput.value);
  if (min !== null && max !== null && min > max) {
    maxInput.setCustomValidity('Цена до должна быть не меньше цены от.');
  }
  return form.reportValidity();
}

function resetExtraFilters() {
  form.elements.category.value = 'all';
  form.elements.requiredCondition.value = '';
  form.elements.priority.value = 'balanced';
  renderDynamicFields('all');
  updateAdvancedHint();
  $('.advanced', form).open = true;
}

function setStatus(text, type = '', actions = '') {
  statusBox.className = `status visible ${type}`.trim();
  statusBox.innerHTML = `<span>${escapeHtml(text)}</span>${actions}`;
}

function clearStatus() {
  statusBox.textContent = '';
  statusBox.className = 'status';
}

function money(value, currency = '₽') {
  if (value == null || String(value).trim() === '' || !Number.isFinite(Number(value))) return 'Цена уточняется';
  const suffix = currency === '₽' || currency === 'RUB' ? '₽' : currency;
  return `${new Intl.NumberFormat('ru-RU').format(Number(value))} ${suffix}`;
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ''));
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch {
    return '';
  }
}

function safeListingUrl(value) {
  const href = safeUrl(value);
  if (!href) return '';
  try {
    const hostname = new URL(href).hostname.toLocaleLowerCase('ru-RU');
    return hostname === 'avito.ru' || hostname.endsWith('.avito.ru') ? href : '';
  } catch {
    return '';
  }
}

function canonicalListingUrl(value) {
  const href = safeListingUrl(value);
  if (!href) return '';
  const url = new URL(href);
  const hostname = url.hostname.toLowerCase().replace(/^(www|m)\./, '');
  return `https://${hostname}${url.pathname.replace(/\/+$/, '')}`;
}

function listingCity(value) {
  const city = String(value || '').trim();
  const names = {moskva: 'Москва', yaroslavl: 'Ярославль', sanktpeterburg: 'Санкт-Петербург', spb: 'Санкт-Петербург', rostovnadonu: 'Ростов-на-Дону', stavropol: 'Ставрополь', omsk: 'Омск', balashiha: 'Балашиха'};
  return names[city.toLowerCase().replace(/[-_\s]/g, '')] || city || 'не указан';
}

function formatCollectedAt(value, label = 'собрано') {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return `${label} ${new Intl.DateTimeFormat('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}).format(date)}`;
}

const DISCOVERY_STATUSES = {
  needs_review: {label: 'Требует проверки', reason: 'Проверка этого объявления не завершена. Сверьте характеристики и состояние перед выбором.'},
  has_risks: {label: 'Есть риски', reason: 'В объявлении найдены риски. Прочитайте условия и уточните спорные сведения у продавца перед покупкой.'},
  mismatch: {label: 'Есть отличия', reason: 'Характеристики объявления отличаются от запроса. Отличия указаны в деталях.'},
  inactive: {label: 'Объявление неактивно', reason: 'При последнем обновлении страница объявления была неактивна.'},
};

function roleLabel(item, mode) {
  if (item.discoveryStatus) return (DISCOVERY_STATUSES[item.discoveryStatus] || DISCOVERY_STATUSES.needs_review).label;
  if (mode === 'preview') return 'Предпросмотр владельца';
  const labels = {TOP: 'ТОП-1', TOP1: 'ТОП-1', BEST: 'ТОП-1', BACKUP: 'Запасной', APPROVED_BACKUP: 'Запасной', BUDGET: 'Бюджетный', APPROVED_BUDGET: 'Бюджетный', CAUTION: 'Осторожно', DO_NOT_BUY: 'Осторожно'};
  return labels[item.role] || 'Подходящий вариант';
}

function listBlock(title, values) {
  const items = [...new Set((values || []).filter(Boolean))];
  if (!items.length) return '';
  return `<div class="finding"><h4>${escapeHtml(title)}</h4><ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>`;
}

function formatFreshness(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 45) return 'Проверено только что';
  if (seconds < 3600) return `Цена и активность проверены ${Math.max(1, Math.round(seconds / 60))} мин назад`;
  return formatCollectedAt(value, 'Цена и активность проверены');
}

function isVerifiedRecommendation(item, mode, allowExactBargain = false) {
  const listing = item?.listing || {};
  const analysis = item?.analysis || {};
  const verifiedAt = Date.parse(listing.verifiedAt || '');
  const age = Date.now() - verifiedAt;
  const safeRole = ['TOP', 'TOP1', 'BEST', 'BACKUP', 'APPROVED_BACKUP', 'BUDGET', 'APPROVED_BUDGET'].includes(item?.role);
  if (!safeRole || item.discoveryStatus || !safeListingUrl(listing.url)
      || !String(listing.title || '').trim() || !(Number(listing.acquisitionPrice) > 0)
      || analysis.complete !== true || analysis.matchesRequest !== true || analysis.verdict !== 'approve'
      || String(analysis.mismatchReason || '').trim() || (analysis.conflicts || []).length
      || listing.verificationStatus !== 'verified' || !Number.isFinite(verifiedAt)
      || age < -60000 || age > 15 * 60 * 1000) return false;
  const broaderEvidence = item.belowMarket === true && item.marketConfidence === 'moderate'
    && Number(item.comparableSellerCount) >= 10 && Number(item.comparableCount) >= 10;
  const comparableEvidence = item.belowComparables === true
    && ['limited', 'moderate'].includes(item.marketConfidence)
    && Number(item.comparableSellerCount) >= 3 && Number(item.comparableCount) >= 3;
  const exactBargainMatch = allowExactBargain && mode === 'bargain' && item.belowComparables !== true;
  return mode === 'find' || exactBargainMatch || (mode === 'bargain' && (comparableEvidence || broaderEvidence)
    && Number(item.savingAmount) > 0
    && Number.isFinite(Number(item.savingPercent)) && Number(item.savingPercent) >= 0);
}

function renderCard(item, index, mode) {
  const listing = item.listing || {};
  const discovered = Boolean(item.discoveryStatus);
  const discovery = DISCOVERY_STATUSES[item.discoveryStatus] || DISCOVERY_STATUSES.needs_review;
  const acquisitionPrice = Number(listing.acquisitionPrice);
  const hasTotalPrice = Number.isFinite(acquisitionPrice) && acquisitionPrice > 0;
  const exactMatch = mode === 'bargain' && isVerifiedRecommendation(item, 'find');
  const belowComparables = mode !== 'preview' && isVerifiedRecommendation(item, 'bargain');
  const belowMarket = belowComparables && item.belowMarket === true && item.marketConfidence === 'moderate'
    && Number(item.comparableSellerCount) >= 10 && Number(item.comparableCount) >= 10;
  const priceBasis = hasTotalPrice
    ? ({pickup_without_optional_delivery: 'Самовывоз; доставка отдельно', required_delivery_included: 'С обязательной доставкой'}[listing.priceBasis] || 'С обязательными доплатами')
    : 'Цена объявления; полная цена не подтверждена';
  const priceFacts = [`Цена объявления: ${money(listing.price, listing.currency)}`];
  if (Number(listing.mandatoryFeeRub) > 0) priceFacts.push(`Обязательные доплаты: ${money(listing.mandatoryFeeRub)}`);
  if (listing.deliveryRequired === true) priceFacts.push(`Обязательная доставка: ${listing.deliveryCostRub == null ? 'стоимость не подтверждена' : money(listing.deliveryCostRub)}`);
  if (hasTotalPrice) priceFacts.push(`Итого: ${money(acquisitionPrice, listing.currency)}. ${priceBasis}.`);
  const analysis = item.analysis || {};
  const condition = listing.conditionEvidence || {};
  const conditionClaims = [];
  if (Number(condition.batteryHealthPercent) > 0) conditionClaims.push(`Здоровье аккумулятора: ${Number(condition.batteryHealthPercent)}%`);
  const repairs = {never_repaired: 'Ремонт не проводился', repaired: 'Был ремонт'}[condition.repairStatus];
  const parts = {original: 'Детали оригинальные', non_original: 'Есть неоригинальные детали'}[condition.partsStatus];
  if (repairs) conditionClaims.push(repairs);
  if (parts) conditionClaims.push(parts);
  if (condition.completeness && condition.completeness !== 'unknown') conditionClaims.push(`Комплект: ${{full: 'полный', partial: 'неполный', device_only: 'только устройство'}[condition.completeness] || condition.completeness}`);
  const params = listing.parameters || {};
  const facts = [params['Модель'], params['Встроенная память'], params['SIM-карты'], params['Состояние']].filter(Boolean);
  const risks = [...new Set([...(item.risks || []), ...(analysis.conflicts || []), ...(analysis.priceConditions || []), ...(analysis.mismatchReason ? [analysis.mismatchReason] : [])])];
  const sellerKind = {company: 'Компания', private: 'Частный продавец'}[listing.seller?.kind] || 'Тип продавца не указан';
  const rating = Number(listing.seller?.rating) > 0 ? ` · рейтинг ${Number(listing.seller.rating).toFixed(1)}` : '';
  const imageUrl = safeUrl(listing.image);
  const listingUrl = safeListingUrl(listing.url);
  const image = imageUrl
    ? `<img class="card-image" src="${escapeHtml(imageUrl)}" alt="Фото: ${escapeHtml(listing.title)}" loading="lazy" referrerpolicy="no-referrer">`
    : '<div class="card-image empty">Нет фото</div>';
  const reason = discovered ? discovery.reason : (item.reasons || [])[0] || 'Основание выбора уточняется; изучите факты и вопросы перед покупкой.';
  const historyWarnings = Array.isArray(item.historyWarnings) ? item.historyWarnings.filter(Boolean) : [];
  const hasComparableMarket = Number(item.comparableCount) >= 3 && Number(item.comparableSellerCount) >= 3;
  const smallComparableCount = Math.max(0, Math.min(Number(item.comparableSellerCount) || 0,
    Number(item.comparableCount) || 0));
  const market = [];
  if (!discovered && hasComparableMarket) {
    if (belowComparables && Number(item.savingAmount) > 0) {
      const percent = Number(item.savingPercent);
      const percentNote = percent > 0 ? ` · −${escapeHtml(new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(percent))}%` : '';
      market.push(`<span class="saving">Экономия ${money(item.savingAmount)}${percentNote}</span>`);
    }
    market.push(`<span>${belowComparables && !belowMarket ? 'Медиана найденных аналогов' : 'Медиана объявлений'}: ${money(item.marketMedian)}</span>`);
    if (Number(item.marketRange?.min) > 0 && Number(item.marketRange?.max) > 0) market.push(`<span>Диапазон: ${money(item.marketRange.min)}–${money(item.marketRange.max)}</span>`);
    if (belowComparables && !belowMarket) {
      market.push(`<span>Сравнили с ${escapeHtml(item.comparableSellerCount)} продавцами${Number(item.comparableSellerCount) < 10 ? ' · небольшая выборка' : ''}</span>`);
    } else market.push(`<span>Цен: ${escapeHtml(item.comparableCount)} · продавцов: ${escapeHtml(item.comparableSellerCount)}</span>`);
    if (belowComparables && !belowMarket) market.push('<span>Для метки «Ниже рынка» нужны как минимум 10 сопоставимых продавцов.</span>');
  }
  if (!discovered && smallComparableCount > 0 && !hasComparableMarket) {
    market.push(`<span>Найдено ${escapeHtml(smallComparableCount)} точных аналогов; этого недостаточно для подтверждения выгоды.</span>`);
  }
  if (!belowComparables) market.push(`<span>${discovered ? 'Выгода не подтверждена: проверка объявления не завершена или найдены риски.' : smallComparableCount < 3 ? 'Выгода не подтверждена: недостаточно независимых сопоставимых продавцов.' : 'Выгода не подтверждена: положительная разница с сопоставимыми предложениями не установлена.'}</span>`);
  const fresh = listing.verificationStatus === 'verified' && Boolean(listing.verifiedAt);
  const checkedAt = fresh ? formatFreshness(listing.verifiedAt) : formatCollectedAt(listing.collectedAt);
  const pageStatus = item.discoveryStatus === 'inactive' ? 'Страница объявления неактивна' : fresh ? 'Цена обновлена; объявление активно' : 'Цена и активность страницы не обновлены';
  const manualChecks = [...new Set(['Исправность и скрытые дефекты: проверить при осмотре или диагностике.', ...(analysis.manualChecks || [])])];
  const references = (item.marketEvidence || []).map((reference) => {
    const href = safeListingUrl(reference.url);
    if (!href) return '';
    const listedPrice = Number(reference.listingPrice) > 0 && Number(reference.listingPrice) !== Number(reference.price) ? ` · в объявлении ${money(reference.listingPrice)}` : '';
    return `<li><a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(reference.seller || 'Сопоставимое объявление')} · ${money(reference.price)}</a>${listedPrice}${reference.observedAt ? ` <span>${escapeHtml(formatCollectedAt(reference.observedAt))}</span>` : ''}</li>`;
  }).filter(Boolean);
  const openAction = listingUrl
    ? `<a href="${escapeHtml(listingUrl)}" target="_blank" rel="noopener noreferrer">Открыть на Avito →</a>`
    : '<span class="link-unavailable">Ссылка недоступна</span>';
  const dealBadge = belowComparables
    ? `<span class="role deal-badge">${belowMarket ? 'Ниже рынка' : 'Дешевле найденных аналогов'}</span>` : '';
  const exactBadge = exactMatch && !belowComparables
    ? '<span class="role exact-badge">Точное совпадение</span>' : '';
  return `<article class="card ${discovered ? 'discovered' : ''} ${belowMarket ? 'below-market' : ''}">
    ${image}
    <div class="card-body">
      <div class="card-head"><div class="badges"><span class="role ${discovered ? 'discovery-badge' : `role-${escapeHtml(item.role)}`}">${escapeHtml(roleLabel(item, mode))}</span>${dealBadge}${exactBadge}<span class="rank">№ ${index + 1}</span></div><div class="price-wrap"><div class="price">${money(hasTotalPrice ? acquisitionPrice : listing.price, listing.currency)}</div><div class="price-basis">${escapeHtml(priceBasis)}</div></div></div>
      <h3>${escapeHtml(listing.title)}</h3>
      <div class="meta">${escapeHtml(listing.seller?.name || 'Продавец не указан')} · ${sellerKind}${escapeHtml(rating)}</div>
      <div class="meta listing-location">Город объявления: ${escapeHtml(listingCity(listing.location))}</div>
      <div class="availability ${fresh ? '' : 'unconfirmed'}"><span class="availability-dot"></span>${escapeHtml(checkedAt || pageStatus)} · ${escapeHtml(listing.imageCount || 0)} фото</div>
      ${market.length ? `<div class="market">${market.join('')}</div>` : ''}
      <div class="card-summary"><b>Почему подходит</b><p>${escapeHtml(reason)}</p></div>
      ${historyWarnings.length ? `<p class="history-warning">${historyWarnings.map(escapeHtml).join(' ')}</p>` : ''}
      ${risks.length ? `<div class="visible-risks"><b>На что обратить внимание</b><ul>${risks.slice(0, 3).map((risk) => `<li>${escapeHtml(risk)}</li>`).join('')}</ul></div>` : ''}
      <div class="card-actions">${openAction}<span class="meta">${facts.map(escapeHtml).join(' · ')}</span></div>
      <p class="verification-note">${discovered ? 'Это найденное объявление; рекомендация к покупке не подтверждена.' : analysis.complete === true ? 'Проверены описание и фото.' : 'Проверка описания и фото не завершена.'} Исправность требует осмотра.</p>
      <details class="card-details"><summary>Факты и вопросы перед покупкой</summary><div class="detail-columns">${listBlock(discovered ? 'Причины статуса' : 'Почему подходит', item.reasons)}${listBlock('Заявлено в объявлении', [...(analysis.descriptionFindings || []), ...conditionClaims])}${listBlock('Видно на фото', analysis.photoFindings)}${listBlock('Что входит в цену', priceFacts)}${listBlock('Риски и условия', risks)}${listBlock('Дефекты', analysis.defects)}${listBlock('Не подтверждено — проверить', manualChecks)}</div>${!discovered && references.length ? `<div class="market-evidence"><h4>С чем сравнили цену</h4><p>Полные цены предложений с обязательными доплатами, а не завершённых сделок.</p><ul>${references.join('')}</ul></div>` : ''}</details>
    </div>
  </article>`;
}

function clientWarning(value) {
  return String(value || '')
    .replace('Zen не вернул объявления.', 'По запросу не удалось получить объявления.')
    .replace('Объявления с незавершённым AI-анализом исключены из клиентской выдачи.', 'Проверка части объявлений не завершена.');
}

function reportHasCurrentPolicy(report) {
  const mode = report.mode || activeSearchPayload?.mode || 'bargain';
  const expected = {bargain: 'verified_exact_matches_with_optional_savings', find: 'verified_matches_only'}[mode];
  if (activeSearchPayload?.mode && activeSearchPayload.mode !== mode) return false;
  return Boolean(expected) && report.resultPolicy === expected;
}

function renderReport(report) {
  const preview = report.preview === true;
  ownerPreview.innerHTML = '';
  ownerPreview.hidden = true;
  warnings.innerHTML = '';
  if (!preview && !reportHasCurrentPolicy(report)) {
    summary.textContent = '';
    costs.hidden = true;
    audit.hidden = true;
    adminWarnings.innerHTML = '';
    results.innerHTML = '<div class="empty-state"><b>Поиск нужно повторить после обновления</b><p>Этот отчёт создан по прежним правилам. Запустите новый поиск кнопкой в форме.</p></div>';
    clearStatus();
    clearActiveJob();
    return false;
  }
  const requested = Number(report.requestedResults || activeSearchPayload?.desiredResults) === 3 ? 3 : 5;
  const realCards = (values) => Array.isArray(values) ? values.filter((item) => item?.listing && String(item.listing.title || '').trim() && (item.listing.id || item.listing.url)) : [];
  const mode = preview ? 'preview' : report.mode || activeSearchPayload?.mode || 'bargain';
  const seenIds = new Set();
  const seenUrls = new Set();
  const keepUnique = (item) => {
    const id = String(item.listing.id || '').trim();
    const url = canonicalListingUrl(item.listing.url);
    const duplicate = (id && seenIds.has(id)) || (url && seenUrls.has(url));
    if (id) seenIds.add(id);
    if (url) seenUrls.add(url);
    return !duplicate;
  };
  // Discovery and owner diagnostics never fill the public recommendation list.
  const items = preview ? [] : realCards(report.recommendations)
    .filter((item) => isVerifiedRecommendation(
      item, mode, report.resultPolicy === 'verified_exact_matches_with_optional_savings',
    )).filter(keepUnique).slice(0, requested);
  summary.textContent = preview ? '' : `Подтверждено: ${items.length} · запрошено до ${requested}`;

  if (authenticatedOwner) {
    const ownerAudit = report.adminAudit;
    if (ownerAudit) {
      audit.hidden = false;
      audit.innerHTML = `<div><span>Прошли правила</span><b>${ownerAudit.deterministicEligible || 0}</b></div><div><span>AI текст</span><b>${ownerAudit.textCompleted || 0} / ${ownerAudit.textAttempted || 0}</b></div><div><span>Совпали</span><b>${ownerAudit.textMatched || 0}</b></div><div><span>AI фото</span><b>${ownerAudit.photoCompleted || 0} / ${ownerAudit.photoAttempted || 0}</b></div><div><span>В выдаче</span><b>${ownerAudit.finalVisible || 0}</b></div><div><span>Время</span><b>${Number(ownerAudit.elapsedSeconds || 0).toFixed(1)} сек</b></div>`;
    } else audit.hidden = true;

    const admin = report.adminCosts;
    if (admin) {
      costs.hidden = false;
      const state = admin.withinBudget ? 'в лимите' : 'лимит превышен';
      const apifyLabel = admin.apifyCostEstimated ? 'оценка' : 'факт';
      costs.innerHTML = `<div><span>Себестоимость</span><b>${money(admin.estimatedTotalRub)}</b></div><div><span>Лимит</span><b>${money(admin.budgetRub)} · ${escapeHtml(state)}</b></div><div><span>Apify (${apifyLabel})</span><b>$${Number(admin.apifyCostUsd || 0).toFixed(4)}</b></div><div><span>AI Tunnel (${admin.aiCostEstimated ? 'оценка' : 'факт'})</span><b>${money(admin.aiCostRub)}</b></div><div><span>Текст / фото / кэш</span><b>${admin.aiTextReviewedCount ?? 0} / ${admin.aiPhotoReviewedCount ?? 0} / ${admin.aiCachedCount || 0}</b></div><div><span>Минимальная цена</span><b>${money(admin.suggestedMinPriceRub)}</b></div>`;
    } else costs.hidden = true;
    adminWarnings.innerHTML = [...new Set([...(report.warnings || []), ...(report.adminWarnings || [])])].map((item) => `<div class="admin-warning">${escapeHtml(item)}</div>`).join('');
    const diagnostics = [...realCards(report.adminRecommendations), ...realCards(report.adminDiscoveredListings)]
      .filter(keepUnique).slice(0, 10)
      .map((item) => ({...item, belowMarket: false, discoveryStatus: item.discoveryStatus || 'needs_review'}));
    if (diagnostics.length || preview) {
      ownerPreview.hidden = false;
      ownerPreview.innerHTML = '<p>Диагностика владельца. Эти объявления не допущены к выдаче покупателю.</p>'
        + diagnostics.map((item, index) => renderCard(item, index, 'preview')).join('');
      if (preview) operatorPanel.open = true;
    }
  }

  const emptyMessage = mode === 'find'
    ? 'Подтверждённых предложений, соответствующих запросу, пока нет'
    : 'Нет точных совпадений с завершёнными проверками и подтверждённой актуальной ценой';
  const emptyReason = preview ? '' : clientWarning(report.emptyReason).trim();
  const normalizeMessage = (value) => value.toLocaleLowerCase('ru-RU').replace(/[\s.!?]+$/g, '');
  const reasonNote = emptyReason && normalizeMessage(emptyReason) !== normalizeMessage(emptyMessage)
    ? `<p>${escapeHtml(emptyReason)}</p>` : '';
  results.innerHTML = items.length
    ? items.map((item, index) => renderCard(item, index, mode)).join('')
    : `<div class="empty-state"><span aria-hidden="true">⌕</span><b>${escapeHtml(preview ? 'Это технический предпросмотр, подбор для покупателя не сформирован' : emptyMessage)}</b>${reasonNote}${preview ? '' : '<p>Мы не стали показывать объявления, которые не прошли полную проверку. Можно увеличить бюджет, ослабить состояние или убрать необязательную характеристику.</p><div class="empty-actions"><button type="button" class="mini-action" data-adjust-filters>Изменить фильтры</button><button type="button" class="mini-action" data-repeat-last>Повторить позже</button></div>'}</div>`;
  if (!preview) rememberVerifiedExample(activeSearchPayload, items.length);
  if (items.length) setStatus('Модель и параметры совпадают с запросом. Цена обновлена, страницы активны.');
  else clearStatus();
  return !preview;
}

function updateProgress(job) {
  liveProgress.hidden = false;
  const elapsed = Math.max(0, Math.round(Number(job.elapsedSeconds || ((Date.now() - progressStartedAt) / 1000))));
  progressBar.value = Math.max(2, Math.min(100, Number(job.percent || 0)));
  progressBar.textContent = `${progressBar.value}%`;
  progressMessage.textContent = job.message || 'Поиск продолжается…';
  progressTime.textContent = formatElapsed(elapsed);
  progressJob.textContent = job.jobId ? `Задача #${compactJobId(job.jobId)}` : 'Задача создаётся…';
  cancelSearchButton.hidden = job.state !== 'running' || !job.jobId;
  cancelSearchButton.disabled = cancelInFlight;
  const connectionStale = job.alive === false && job.state === 'running';
  progressTitle.textContent = job.state === 'cancelled' ? 'Поиск остановлен'
    : job.state === 'complete' ? 'Проверка завершена'
      : connectionStale ? 'Ответ задерживается · поиск продолжается' : 'Сайт работает · поиск идёт';
  const stageOrder = {queued: 0, collect: 1, market: 1, prepare: 2, text: 2, photo: 3, refresh: 4, ranking: 5, complete: 5};
  const active = stageOrder[job.stage] ?? 0;
  $$('.stage-list li').forEach((item, index) => {
    item.classList.toggle('done', index < active || job.state === 'complete');
    item.classList.toggle('active', index === active && job.state === 'running');
  });
}

function compactJobId(jobId) {
  return String(jobId || '').replace(/[^a-z0-9_-]/gi, '').slice(0, 8).toUpperCase();
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total} сек`;
  return `${Math.floor(total / 60)} мин ${total % 60} сек`;
}

function startProgressClock(elapsedSeconds = 0) {
  stopProgressClock();
  progressStartedAt = Date.now() - Math.max(0, Number(elapsedSeconds || 0)) * 1000;
  progressTicker = window.setInterval(() => {
    const elapsed = Math.round((Date.now() - progressStartedAt) / 1000);
    progressTime.textContent = formatElapsed(elapsed);
    if (elapsed >= SOFT_TARGET_SECONDS) {
      progressTitle.textContent = 'Проверка занимает больше времени';
    }
  }, 1000);
}

function stopProgressClock() {
  if (progressTicker) window.clearInterval(progressTicker);
  progressTicker = null;
}

function setBusy(busy) {
  submitButton.disabled = busy;
  submitButton.classList.toggle('loading', busy);
  if (busy) buttonLabel.textContent = 'Проверяем объявления'; else updateButtonLabel();
}

const wait = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

async function requestJSON(url, options = {}, {timeout = 10000, retries = 0} = {}) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeout);
    try {
      const headers = new Headers(options.headers || {});
      if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`);
      const response = await fetch(url, {...options, headers, signal: controller.signal});
      const contentType = response.headers.get('content-type') || '';
      const body = contentType.includes('application/json')
        ? await response.json()
        : {error: (await response.text()).slice(0, 400)};
      if (!response.ok) {
        throw new ClientError(body.error || 'Сервис не смог обработать запрос.', {
          code: body.errorCode || body.code || `HTTP_${response.status}`,
          retryable: body.retryable ?? (response.status === 429 || response.status >= 500),
          status: response.status,
        });
      }
      return body;
    } catch (error) {
      lastError = error?.name === 'AbortError'
        ? new ClientError('Сервис отвечает дольше обычного.', {code: 'NETWORK_TIMEOUT', retryable: true})
        : error instanceof ClientError
          ? error
          : new ClientError('Не удалось связаться с сервисом. Проверьте интернет и повторите.', {code: 'NETWORK_ERROR', retryable: true});
      if (!lastError.retryable || attempt >= retries) throw lastError;
      await wait(400 * (attempt + 1));
    } finally {
      window.clearTimeout(timer);
    }
  }
  throw lastError;
}

const FRIENDLY_ERRORS = {
  APIFY_PLAN_REQUIRED: 'Лимит сбора объявлений исчерпан. Поиск станет доступен после пополнения Apify.',
  APIFY_AUTH: 'Сбор объявлений временно не настроен.',
  APIFY_RATE_LIMIT: 'Авито-поиск перегружен. Подождите немного и повторите.',
  AI_AUTH: 'AI-проверка временно не настроена.',
  AI_QUOTA: 'Лимит AI-проверки исчерпан. Попробуйте позже.',
  AI_RATE_LIMIT: 'AI-проверка перегружена. Попробуйте ещё раз через минуту.',
  AI_INVALID_RESPONSE: 'Проверка не завершена: AI вернул неполный ответ. Непроверенные объявления исключены из выдачи.',
  SEARCH_TIMEOUT: 'Проверка превысила время ожидания. Сохранённый поиск можно открыть снова.',
  SEARCH_RATE_LIMIT: 'Часовой лимит поисков исчерпан. Попробуйте позже.',
  ACCESS_REQUIRED: 'Для поиска нужен ключ доступа. Нажмите «Войти».',
  OWNER_AUTH_REQUIRED: 'Для этой операции нужен вход владельца.',
  SEARCH_BUSY: 'Сейчас уже идут другие проверки. Повторите через несколько секунд.',
  TOO_MANY_SEARCHES: 'Сейчас уже идут другие проверки. Повторите через несколько секунд.',
  SEARCH_CANCELLED: 'Поиск остановлен.',
  JOB_NOT_FOUND: 'Эта задача больше недоступна. Запустите поиск снова.',
  NETWORK_ERROR: 'Не удалось связаться с сервисом поиска. Проверьте подключение и повторите.',
  NETWORK_TIMEOUT: 'Сервис отвечает дольше обычного. Выполняющийся поиск можно открыть по номеру задачи.',
  COLLECTION_BUDGET: 'Поиск временно недоступен из-за лимита сервиса.',
  APIFY_SPEND_LIMIT: 'Поиск временно недоступен из-за лимита сервиса.',
  INVALID_REQUEST: 'Проверьте запрос и выбранные фильтры.',
};

function friendlyError(error) {
  return FRIENDLY_ERRORS[error.code] || error.message || 'Поиск не завершён.';
}

function renderSearchError(error) {
  if (error.code === 'SEARCH_CANCELLED') {
    clearActiveJob();
    setStatus('Поиск остановлен.', 'neutral');
    results.innerHTML = '<div class="empty-state"><span aria-hidden="true">■</span><b>Поиск остановлен</b><p>Можно изменить параметры и запустить новый поиск.</p></div>';
    return;
  }
  const retry = activeJobId
    ? '<button type="button" class="mini-action" data-resume-search>Открыть сохранённый поиск</button>'
    : error.retryable !== false && lastSearchPayload
    ? '<button type="button" class="mini-action" data-retry-search>Повторить поиск</button>'
    : '';
  const login = ['ACCESS_REQUIRED', 'OWNER_AUTH_REQUIRED'].includes(error.code) ? '<button type="button" class="mini-action" data-open-access>Войти</button>' : '';
  setStatus(friendlyError(error), 'error', `<div class="status-actions">${retry}${login}<button type="button" class="mini-action" data-open-support>Написать в поддержку</button></div>`);
  results.innerHTML = '<div class="empty-state error-state"><span aria-hidden="true">!</span><b>Поиск не завершён</b><p>Ваши поля сохранены. Можно повторить запрос или сообщить нам об ошибке.</p></div>';
}

function saveActiveJob(jobId, payload) {
  activeJobId = jobId;
  storage.set(ACTIVE_JOB_KEY, JSON.stringify({jobId, payload, isDataset: activeJobIsDataset, savedAt: Date.now()}));
  const url = new URL(window.location.href);
  url.searchParams.set('job', jobId);
  window.history.replaceState(null, '', url);
  progressJob.textContent = `Задача #${compactJobId(jobId)}`;
}

function clearActiveJob() {
  const previousJobId = activeJobId;
  activeJobId = null;
  storage.remove(ACTIVE_JOB_KEY);
  const url = new URL(window.location.href);
  if (!previousJobId || url.searchParams.get('job') === previousJobId) {
    url.searchParams.delete('job');
    window.history.replaceState(null, '', url);
  }
}

async function pollJob(job, generation) {
  let current = job;
  let consecutiveFailures = 0;
  while (current.state === 'running') {
    if (generation !== searchGeneration) throw new ClientError('Устаревший поиск остановлен.', {code: 'STALE_SEARCH', retryable: false});
    updateProgress(current);
    let remaining = SEARCH_LIMIT_MS - (Date.now() - progressStartedAt);
    if (remaining <= 0) {
      throw new ClientError('Сохранённый поиск можно открыть снова.', {code: 'SEARCH_TIMEOUT', retryable: true});
    }
    const elapsed = (Date.now() - progressStartedAt) / 1000;
    const interval = elapsed < 20 ? 750 : elapsed < 90 ? 1250 : 2000;
    await wait(Math.min(interval, remaining));
    if (generation !== searchGeneration) throw new ClientError('Устаревший поиск остановлен.', {code: 'STALE_SEARCH', retryable: false});
    remaining = SEARCH_LIMIT_MS - (Date.now() - progressStartedAt);
    if (remaining <= 0) {
      throw new ClientError('Сохранённый поиск можно открыть снова.', {code: 'SEARCH_TIMEOUT', retryable: true});
    }
    try {
      current = await requestJSON(
        `/api/avito/jobs/${encodeURIComponent(current.jobId)}`,
        {cache: 'no-store'},
        {timeout: Math.max(250, Math.min(5000, remaining)), retries: 0},
      );
      consecutiveFailures = 0;
    } catch (error) {
      if (error.retryable === false || error.status === 404) throw error;
      consecutiveFailures += 1;
      progressTitle.textContent = 'Восстанавливаем связь';
      progressMessage.textContent = 'Поиск на сервере продолжается. Получаем его состояние…';
      if (consecutiveFailures >= 3) throw error;
    }
  }
  return current;
}

async function startSearch(payload, resumeJob = null, {isDataset = false} = {}) {
  if (searchInFlight) return;
  searchInFlight = true;
  const generation = ++searchGeneration;
  if (!resumeJob) activeJobId = null;
  activeJobIsDataset = isDataset;
  activeSearchPayload = payload;
  lastSearchPayload = isDataset ? null : payload;
  setBusy(true);
  clearStatus();
  warnings.innerHTML = '';
  results.innerHTML = '';
  summary.textContent = '';
  ownerPreview.innerHTML = '';
  ownerPreview.hidden = true;
  adminWarnings.innerHTML = '';
  costs.hidden = true;
  audit.hidden = true;
  liveProgress.hidden = false;
  updateProgress({stage: 'queued', state: 'running', message: resumeJob ? 'Возвращаемся к незавершённому поиску…' : 'Запрос принят. Запускаем поиск…', percent: 2, elapsedSeconds: 0, alive: true});
  startProgressClock(resumeJob?.elapsedSeconds || 0);
  $('#result-section').scrollIntoView({behavior: 'smooth', block: 'start'});
  try {
    const createTimeout = Math.max(500, Math.min(8000, SEARCH_LIMIT_MS - (Date.now() - progressStartedAt)));
    let job = resumeJob || await requestJSON('/api/avito/jobs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }, {timeout: createTimeout, retries: 0});
    if (!job.jobId) throw new ClientError('Сервис не вернул номер поиска.', {code: 'INVALID_JOB', retryable: true});
    saveActiveJob(job.jobId, payload);
    job = await pollJob(job, generation);
    updateProgress(job);
    if (job.state !== 'complete' || !job.result) {
      if (!job.workerAlive || job.state === 'cancelled') clearActiveJob();
      throw new ClientError(job.error || 'Поиск не завершён.', {code: job.errorCode || 'SEARCH_ERROR', retryable: job.retryable !== false});
    }
    saveActiveJob(job.jobId, payload);
    activeJobId = null;
    if (!isDataset) saveLastSearch(payload);
    renderReport(job.result);
  } catch (error) {
    if (error?.code === 'STALE_SEARCH') return;
    // Network interruption does not cancel work or lose the reconnect handle.
    if (error.status === 404) clearActiveJob();
    renderSearchError(error instanceof ClientError ? error : new ClientError(error.message));
  } finally {
    if (generation === searchGeneration) {
      searchInFlight = false;
      setBusy(false);
      stopProgressClock();
      window.setTimeout(() => { liveProgress.hidden = true; }, 900);
    }
  }
}

async function requestDataset(payload) {
  activeSearchPayload = payload.search || null;
  setBusy(true);
  clearStatus();
  try {
    const data = await requestJSON('/api/avito/analyze-dataset', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    }, {timeout: 10000});
    await startSearch(payload.search, data, {isDataset: true});
  } catch (error) {
    setStatus(friendlyError(error), 'error');
  } finally {
    setBusy(false);
  }
}

function updateButtonLabel() {
  const mode = $('input[name="mode"]:checked', form)?.value;
  buttonLabel.textContent = mode === 'find' ? 'Найти подходящие варианты' : 'Найти выгодные варианты';
}

function applyExample(example, {scroll = false} = {}) {
  if (!example) return;
  form.elements.query.value = example.query;
  form.elements.category.value = example.category;
  form.elements.requiredCondition.value = example.condition || '';
  form.elements.mode.value = example.mode;
  form.elements.priority.value = example.priority;
  renderDynamicFields(example.category, example.attributes);
  updateButtonLabel();
  updateAdvancedHint();
  globalThis.scheduleQueryRecognition?.();
  if (scroll) $('#search').scrollIntoView({behavior: 'smooth', block: 'start'});
  window.setTimeout(() => form.elements.query.focus(), scroll ? 450 : 0);
}

function applySearchPayload(payload) {
  if (!payload || typeof payload !== 'object') return;
  form.elements.query.value = String(payload.query || '');
  form.elements.location.value = String(payload.location || 'Ярославль');
  form.elements.mode.value = payload.mode === 'find' ? 'find' : 'bargain';
  form.elements.priority.value = ['quality', 'budget'].includes(payload.priority) ? payload.priority : 'balanced';
  form.elements.desiredResults.value = Number(payload.desiredResults) === 3 ? '3' : '5';
  form.elements.priceMin.value = payload.priceMin ?? '';
  form.elements.priceMax.value = payload.priceMax ?? '';
  form.elements.requiredCondition.value = payload.requiredCondition || '';
  form.elements['pickup-only'].checked = payload.pickupOnly === true;
  const category = CATEGORY_FIELDS[payload.category] ? payload.category : 'all';
  form.elements.category.value = category;
  renderDynamicFields(category, payload.attributes || {});
  updateButtonLabel();
  updateAdvancedHint();
  applyQueryRecognition();
}

function loadLastSearch() {
  try {
    const value = JSON.parse(storage.get(LAST_SEARCH_KEY, 'null'));
    return value?.payload && Date.now() - Number(value.savedAt || 0) < 30 * 24 * 60 * 60 * 1000 ? value.payload : null;
  } catch {
    return null;
  }
}

function renderLastSearch() {
  const payload = loadLastSearch();
  lastSearchBox.hidden = !payload;
  lastSearchLabel.textContent = payload?.query || '';
}

function saveLastSearch(payload) {
  storage.set(LAST_SEARCH_KEY, JSON.stringify({payload, savedAt: Date.now()}));
  lastSearchPayload = payload;
  renderLastSearch();
}

async function cancelActiveSearch() {
  if (!activeJobId || cancelInFlight) return;
  cancelInFlight = true;
  cancelSearchButton.disabled = true;
  cancelSearchButton.textContent = 'Останавливаем…';
  try {
    const job = await requestJSON(`/api/avito/jobs/${encodeURIComponent(activeJobId)}/cancel`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: '{}',
    }, {timeout: 8000, retries: 0});
    updateProgress(job);
    searchGeneration += 1;
    searchInFlight = false;
    setBusy(false);
    stopProgressClock();
    renderSearchError(new ClientError(job.error || 'Поиск остановлен.', {code: 'SEARCH_CANCELLED', retryable: false}));
    window.setTimeout(() => { liveProgress.hidden = true; }, 900);
  } catch (error) {
    renderSearchError(error instanceof ClientError ? error : new ClientError(error.message));
  } finally {
    cancelInFlight = false;
    cancelSearchButton.disabled = false;
    cancelSearchButton.textContent = 'Отменить поиск';
  }
}

function updateTheme(theme) {
  const dark = theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  themeToggle.setAttribute('aria-pressed', String(dark));
  themeToggle.setAttribute('aria-label', dark ? 'Включить светлую тему' : 'Включить тёмную тему');
  themeToggle.title = dark ? 'Светлая тема' : 'Тёмная тема';
  $('meta[name="theme-color"]')?.setAttribute('content', dark ? '#09070c' : '#f4f1f5');
  storage.set('naydi-theme', dark ? 'dark' : 'light');
}

function closeMenu() {
  siteNav.classList.remove('open');
  menuToggle.classList.remove('open');
  menuToggle.setAttribute('aria-expanded', 'false');
  menuToggle.setAttribute('aria-label', 'Открыть меню');
}

function openSupport() {
  supportStatus.className = 'support-status';
  supportStatus.textContent = '';
  if (!supportDialog.open) supportDialog.showModal();
}

form.addEventListener('change', (event) => {
  if (event.target.matches('input[name="mode"]')) updateButtonLabel();
  if (event.target === categorySelect) renderDynamicFields(categorySelect.value);
  updateAdvancedHint();
});

form.addEventListener('input', (event) => {
  if (event.target === form.elements.query) globalThis.scheduleQueryRecognition?.();
  if (event.target === form.elements.priceMin || event.target === form.elements.priceMax) {
    form.elements.priceMin.setCustomValidity('');
    form.elements.priceMax.setCustomValidity('');
  }
  updateAdvancedHint();
});

form.addEventListener('submit', (event) => {
  event.preventDefault();
  if (searchInFlight || submitButton.disabled) return;
  applyQueryRecognition();
  if (!validateSearch()) return;
  startSearch(searchPayload());
});

$('#reset-extra-filters').addEventListener('click', resetExtraFilters);

examplesList.addEventListener('click', (event) => {
  const button = event.target.closest('[data-example-id]');
  if (!button) return;
  applyExample(examplePool().find((item) => item.id === button.dataset.exampleId));
});

examplesPrev.addEventListener('click', () => { changeExamplePage(-1); startExampleRotation(); });
examplesNext.addEventListener('click', () => { changeExamplePage(1); startExampleRotation(); });
examplesPause.addEventListener('click', () => setExamplesPaused(!examplesPaused));
examplesPanel.addEventListener('mouseenter', stopExampleRotation);
examplesPanel.addEventListener('mouseleave', startExampleRotation);
examplesPanel.addEventListener('focusin', stopExampleRotation);
examplesPanel.addEventListener('focusout', (event) => {
  if (!examplesPanel.contains(event.relatedTarget)) startExampleRotation();
});
cancelSearchButton.addEventListener('click', cancelActiveSearch);
for (const select of [ps5Version, ps5Drive, ps5Condition]) select.addEventListener('change', applyPs5QuickFilters);
recognizedParameters.addEventListener('click', (event) => {
  const chip = event.target.closest('[data-recognized-key]');
  if (!chip) return;
  const key = chip.dataset.recognizedKey;
  if (key === 'model') {
    form.elements.query.focus();
    return;
  }
  $('.advanced', form).open = true;
  const target = key === 'storage' || key === 'edition'
    ? $(`[data-attribute="${key}"]`, dynamicFields)
    : form.elements[key];
  target?.focus();
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopExampleRotation(); else startExampleRotation();
});
window.matchMedia('(max-width: 780px)').addEventListener?.('change', startExampleRotation);

$$('[data-preset-id]').forEach((button) => button.addEventListener('click', () => {
  applyExample(examplePool().find((item) => item.id === button.dataset.presetId), {scroll: true});
}));

if (fileInput) fileInput.addEventListener('change', async () => {
  const file = fileInput.files?.[0];
  if (!file) return;
  try {
    const listings = JSON.parse(await file.text());
    if (!Array.isArray(listings)) throw new Error('not-array');
    await requestDataset({search: searchPayload(), listings});
  } catch {
    setStatus('Файл должен содержать корректный JSON-массив Dataset.', 'error');
  } finally {
    fileInput.value = '';
  }
});

const preferredTheme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
updateTheme(storage.get('naydi-theme', preferredTheme));
themeToggle.addEventListener('click', () => updateTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));

menuToggle.addEventListener('click', () => {
  const open = !siteNav.classList.contains('open');
  siteNav.classList.toggle('open', open);
  menuToggle.classList.toggle('open', open);
  menuToggle.setAttribute('aria-expanded', String(open));
  menuToggle.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
});
$$('a', siteNav).forEach((link) => link.addEventListener('click', closeMenu));
document.addEventListener('click', (event) => {
  if (siteNav.classList.contains('open') && !siteNav.contains(event.target) && !menuToggle.contains(event.target)) closeMenu();
  const retryButton = event.target.closest('[data-retry-search]');
  if (retryButton && lastSearchPayload) startSearch(lastSearchPayload);
  if (event.target.closest('[data-resume-search]')) resumeSavedJob();
  if (event.target.closest('[data-open-access]') && !accessDialog.open) accessDialog.showModal();
  if (event.target.closest('[data-open-support]')) openSupport();
  if (event.target.closest('[data-adjust-filters]')) {
    $('.advanced', form).open = true;
    $('#search').scrollIntoView({behavior: 'smooth', block: 'start'});
  }
  if (event.target.closest('[data-repeat-last]')) {
    const payload = loadLastSearch();
    if (payload) {
      applySearchPayload(payload);
      $('#search').scrollIntoView({behavior: 'smooth', block: 'start'});
      window.setTimeout(() => form.elements.query.focus(), 350);
    }
  }
});

$('[data-close-support]').addEventListener('click', () => supportDialog.close());
supportDialog.addEventListener('click', (event) => {
  if (event.target === supportDialog) supportDialog.close();
});

supportForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!supportForm.reportValidity()) return;
  const button = $('.primary', supportForm);
  const data = new FormData(supportForm);
  button.disabled = true;
  supportStatus.className = 'support-status visible';
  supportStatus.textContent = 'Отправляем заявку…';
  try {
    const result = await requestJSON('/api/support', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: data.get('name'), contact: data.get('contact'), topic: data.get('topic'), message: data.get('message')}),
    }, {timeout: 10000, retries: 1});
    supportStatus.className = 'support-status visible';
    supportStatus.textContent = `${result.message} Номер: ${result.ticket}`;
    supportForm.reset();
  } catch (error) {
    supportStatus.className = 'support-status visible error';
    supportStatus.textContent = friendlyError(error);
  } finally {
    button.disabled = false;
  }
});

async function enableOwnerMode() {
  authenticatedOwner = false;
  try {
    const session = await requestJSON('/api/session', {cache: 'no-store'}, {timeout: 5000});
    authenticatedOwner = session.owner === true;
  } catch { /* Login stays available when protected. */ }
  operatorPanel.hidden = !authenticatedOwner;
  if (!authenticatedOwner) {
    costs.hidden = true;
    audit.hidden = true;
    costs.innerHTML = '';
    audit.innerHTML = '';
    adminWarnings.innerHTML = '';
    ownerPreview.innerHTML = '';
    ownerPreview.hidden = true;
    if (activeJobIsDataset) {
      results.innerHTML = '<div class="empty-state"><b>Предпросмотр доступен владельцу после входа</b></div>';
      summary.textContent = '';
      warnings.innerHTML = '';
      clearActiveJob();
    }
  }
}

accessForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const previousToken = accessToken;
  accessToken = String(new FormData(accessForm).get('token') || '').trim();
  accessStatus.textContent = 'Проверяем доступ…';
  try {
    const session = await requestJSON('/api/session', {cache: 'no-store'}, {timeout: 5000});
    try { sessionStorage.setItem('naydi-access-token', accessToken); } catch { /* Optional. */ }
    authenticatedOwner = session.owner === true;
    operatorPanel.hidden = !authenticatedOwner;
    accessStatus.textContent = '';
    accessForm.reset();
    accessDialog.close();
    setStatus(authenticatedOwner ? 'Вход владельца выполнен.' : 'Доступ к поиску открыт.');
    await resumeSavedJob();
  } catch (error) {
    accessToken = previousToken;
    accessStatus.textContent = 'Ключ не принят. Проверьте его и повторите вход.';
  }
});
$('[data-close-access]').addEventListener('click', () => accessDialog.close());
$('[data-logout]').addEventListener('click', async () => {
  accessToken = '';
  try { sessionStorage.removeItem('naydi-access-token'); } catch { /* Optional. */ }
  await enableOwnerMode();
  accessDialog.close();
  setStatus('Вы вышли.');
});

async function refreshReadiness() {
  try {
    const data = await requestJSON('/health', {cache: 'no-store'}, {timeout: 5000});
    const ready = Boolean(data.ready?.apify && data.ready?.ai);
    readiness.className = `readiness ${ready ? 'ready' : 'partial'}`;
    readiness.innerHTML = `<span class="dot"></span><span>${ready ? (data.accessRequired && !accessToken ? 'Нужен вход' : 'Готов к поиску') : 'Нужна настройка'}</span>`;
  } catch {
    readiness.className = 'readiness partial';
    readiness.innerHTML = '<span class="dot"></span><span>Сервис недоступен</span>';
  }
}

async function resumeSavedJob() {
  let saved;
  try { saved = JSON.parse(storage.get(ACTIVE_JOB_KEY, 'null')); } catch { saved = null; }
  let urlJobId = '';
  try { urlJobId = new URL(window.location.href).searchParams.get('job') || ''; } catch { /* URL recovery is optional. */ }
  if (urlJobId && (!saved || saved.jobId !== urlJobId)) {
    saved = {jobId: urlJobId, payload: null, isDataset: false, savedAt: Date.now(), fromUrl: true};
  }
  if (!saved?.jobId || (!saved.fromUrl && Date.now() - Number(saved.savedAt || 0) > JOB_RETENTION_MS)) {
    clearActiveJob();
    return;
  }
  activeJobId = saved.jobId;
  activeJobIsDataset = Boolean(saved.isDataset);
  const savedPayload = saved.payload || loadLastSearch() || {};
  try {
    const job = await requestJSON(`/api/avito/jobs/${encodeURIComponent(saved.jobId)}`, {cache: 'no-store'}, {timeout: 5000});
    if (job.state === 'running') await startSearch(savedPayload, job, {isDataset: activeJobIsDataset});
    else if (job.state === 'complete' && job.result) {
      activeSearchPayload = savedPayload;
      if (typeof progressJob !== 'undefined') progressJob.textContent = `Задача #${compactJobId(job.jobId)}`;
      renderReport(job.result);
      activeJobId = null;
    } else {
      if (!job.workerAlive) clearActiveJob();
      renderSearchError(new ClientError(job.error || 'Поиск ещё не завершён.', {code: job.errorCode || 'SEARCH_ERROR'}));
    }
  } catch (error) {
    if (error.status === 404) clearActiveJob();
    renderSearchError(error);
  }
}

renderDynamicFields('all');
renderExamples();
renderLastSearch();
startExampleRotation();
updateButtonLabel();
refreshReadiness();
enableOwnerMode().then(resumeSavedJob);
