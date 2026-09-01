const STORAGE_KEY = "nova:local-requests:v1";

// initData remains private to this bridge. It is never copied to state,
// localStorage, a URL, analytics, or the general search request.
const telegramBridge = (() => {
  let sessionRequest = null;
  let backHandler = null;
  let backHandlerBound = false;
  let preparedWebApp = null;

  function getWebApp() {
    return window.Telegram?.WebApp || null;
  }

  function getInitData(webApp = getWebApp()) {
    return typeof webApp?.initData === "string" ? webApp.initData : "";
  }

  function isMiniApp() {
    const webApp = getWebApp();
    return Boolean(webApp && getInitData(webApp));
  }

  function safeInset(value) {
    return Number.isFinite(value) && value > 0 ? `${Math.round(value)}px` : "0px";
  }

  function syncInsets(webApp = getWebApp()) {
    const inset = webApp?.contentSafeAreaInset || webApp?.safeAreaInset || {};
    const root = document.documentElement;
    root.style.setProperty("--telegram-safe-top", safeInset(inset.top));
    root.style.setProperty("--telegram-safe-right", safeInset(inset.right));
    root.style.setProperty("--telegram-safe-bottom", safeInset(inset.bottom));
    root.style.setProperty("--telegram-safe-left", safeInset(inset.left));
  }

  function prepare() {
    const webApp = getWebApp();
    if (!webApp) return;
    try {
      webApp.ready();
      webApp.expand();
      webApp.setHeaderColor?.("#101010");
      webApp.setBackgroundColor?.("#0d0d0d");
      if (isMiniApp()) document.documentElement.dataset.telegram = "true";
      if (preparedWebApp !== webApp) {
        syncInsets(webApp);
        webApp.onEvent?.("safeAreaChanged", () => syncInsets(webApp));
        webApp.onEvent?.("contentSafeAreaChanged", () => syncInsets(webApp));
        preparedWebApp = webApp;
      }
    } catch {
      // A partial WebApp implementation must not prevent browser search.
    }
  }

  async function openSession() {
    const webApp = getWebApp();
    const initData = getInitData(webApp);
    if (!webApp || !initData) return { status: "browser", displayName: "" };
    if (sessionRequest) return sessionRequest;
    sessionRequest = (async () => {
      try {
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 8000);
        const response = await fetch("/api/telegram/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ initData }),
          signal: controller.signal,
        });
        window.clearTimeout(timeout);
        const payload = await response.json().catch(() => null);
        if (!response.ok || payload?.telegram !== true) return { status: "unavailable", displayName: "" };
        const displayName = typeof payload.displayName === "string"
          ? payload.displayName.replace(/\s+/g, " ").trim().slice(0, 64)
          : "";
        return { status: "connected", displayName };
      } catch {
        return { status: "unavailable", displayName: "" };
      } finally {
        sessionRequest = null;
      }
    })();
    return sessionRequest;
  }

  function syncBackButton(handler, visible) {
    const webApp = getWebApp();
    if (!webApp?.BackButton) return;
    try {
      if (!backHandlerBound) {
        webApp.BackButton.onClick(() => backHandler?.());
        backHandlerBound = true;
      }
      backHandler = visible ? handler : null;
      if (visible) webApp.BackButton.show();
      else webApp.BackButton.hide();
    } catch {
      // The ordinary browser controls stay available.
    }
  }

  function haptic(kind) {
    const webApp = getWebApp();
    try {
      if (kind === "success" || kind === "warning") webApp?.HapticFeedback?.notificationOccurred(kind);
      else webApp?.HapticFeedback?.impactOccurred(kind || "light");
    } catch {
      // Haptics are optional and never affect the search.
    }
  }

  return {
    get isMiniApp() { return isMiniApp(); },
    prepare,
    openSession,
    syncBackButton,
    haptic,
  };
})();

const priorities = [
  { id: "balance", title: "Лучший баланс", text: "Цена, надёжность и характеристики вместе" },
  { id: "price", title: "Минимальная цена", text: "Ищем выгодный вариант без явных компромиссов" },
  { id: "reliability", title: "Надёжность", text: "Проверяем продавца и условия особенно внимательно" },
  { id: "delivery", title: "Быстрая доставка", text: "Приоритет предложениям, которые можно получить быстрее" },
];

const EXAMPLE_QUERY_SETS = [
  {
    title: "Популярная техника",
    examples: [
      { label: "iPhone 17 Pro", query: "iPhone 17 Pro" },
      { label: "MacBook Air M4", query: "MacBook Air M4" },
      { label: "Телевизор Samsung 55", query: "Телевизор Samsung 55 4K" },
    ],
  },
  {
    title: "Техника для дома и работы",
    examples: [
      { label: "Наушники Sony WH-1000XM5", query: "Наушники Sony WH-1000XM5" },
      { label: "Монитор LG 27 4K", query: "Монитор LG 27 4K" },
      { label: "Кресло Cougar Armor", query: "Офисное кресло Cougar Armor" },
    ],
  },
  {
    title: "Другие популярные модели",
    examples: [
      { label: "Samsung Galaxy S25", query: "Samsung Galaxy S25" },
      { label: "Ноутбук ASUS Zenbook 14", query: "Ноутбук ASUS Zenbook 14" },
      { label: "Телевизор Hisense 55", query: "Телевизор Hisense 55 4K" },
    ],
  },
];

const initialDraft = () => ({
  category: "manual",
  product: "",
  budget: "",
  city: "Москва",
  priority: "balance",
});

const state = {
  view: "home",
  returnView: "home",
  step: 1,
  draft: initialDraft(),
  saved: null,
  search: { loading: false, result: null, error: null },
  telegram: { status: "browser", displayName: "" },
  exampleSetIndex: 0,
};

const app = document.querySelector("#app");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  })[char]);
}

function categoryName() {
  return "Товар";
}

function priorityName(id) {
  return priorities.find((item) => item.id === id)?.title || "Лучший баланс";
}

function stepCount() {
  return 3;
}

function budgetStep() {
  return 2;
}

function priorityStep() {
  return 3;
}

function money(value) {
  const digits = String(value || "").replace(/\D/g, "");
  return digits ? `${Number(digits).toLocaleString("ru-RU")} ₽` : "Не указан";
}

function getLocalRequests() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function setLocalRequests(requests) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(requests));
}

function telegramContext() {
  const { status, displayName } = state.telegram;
  if (status === "browser") {
    return `<span class="telegram-context telegram-context--browser" role="status"><i aria-hidden="true">↗</i><span>Браузерный режим</span></span>`;
  }
  if (status === "connecting") {
    return `<span class="telegram-context telegram-context--loading" role="status"><i aria-hidden="true"></i><span>Подключаем Telegram</span></span>`;
  }
  if (status === "connected") {
    const label = displayName ? `Telegram · ${displayName}` : "В Telegram";
    return `<span class="telegram-context telegram-context--connected" role="status"><i aria-hidden="true">✓</i><span>${escapeHtml(label)}</span></span>`;
  }
  return `<button class="telegram-context telegram-context--unavailable" type="button" data-action="retry-telegram" aria-label="Повторить подключение Telegram"><i aria-hidden="true">!</i><span>Telegram: повторить</span></button>`;
}

function activeExampleQuerySet() {
  return EXAMPLE_QUERY_SETS[state.exampleSetIndex] || EXAMPLE_QUERY_SETS[0];
}

function renderExampleQueries() {
  const set = activeExampleQuerySet();
  const currentNumber = state.exampleSetIndex + 1;
  const labels = set.examples.map((item) => item.label).join(", ");
  return `
    <div class="quick-queries" aria-label="Примеры запросов">
      <span class="quick-queries__label">Попробуйте:</span>
      <div class="quick-queries__items" id="quick-query-examples" role="group" aria-label="${escapeHtml(set.title)}">
        ${set.examples.map((item) => `<button type="button" data-example-query="${escapeHtml(item.query)}">${escapeHtml(item.label)}</button>`).join("")}
      </div>
      <button class="quick-queries__rotate" type="button" data-action="rotate-examples" aria-controls="quick-query-examples" aria-label="Показать другие варианты товаров">Другие варианты <span aria-hidden="true">↻</span></button>
      <span class="quick-queries__status" role="status">Набор ${currentNumber} из ${EXAMPLE_QUERY_SETS.length}: ${escapeHtml(labels)}</span>
    </div>
  `;
}

function rotateExampleQueries() {
  state.exampleSetIndex = (state.exampleSetIndex + 1) % EXAMPLE_QUERY_SETS.length;
  render();
  app.querySelector('[data-action="rotate-examples"]')?.focus();
}

function screenFrame(content, active = state.view) {
  return `
    <header class="topbar">
      <button class="brand" type="button" data-view="home" aria-label="На главную">
        <span class="brand__mark" aria-hidden="true">N</span>
        <span class="brand__wordmark">NOVA<small>smart market</small></span>
      </button>
      <div class="topbar__right">
        ${telegramContext()}
        <button class="nav-button" type="button" data-scroll="how">Как ищем</button>
        <button class="nav-button" type="button" data-view="requests">Мои поиски</button>
        <button class="header-search" type="button" data-action="start">Начать поиск <span aria-hidden="true">↗</span></button>
      </div>
    </header>
    ${content}
    <nav class="bottom-nav" aria-label="Основная навигация">
      <button type="button" data-view="home" ${active === "home" ? 'aria-current="page"' : ""}>NOVA</button>
      <button type="button" data-view="wizard" ${active === "wizard" ? 'aria-current="page"' : ""}>Найти</button>
      <button type="button" data-scroll="how">Как ищем</button>
      <button type="button" data-view="requests" ${active === "requests" ? 'aria-current="page"' : ""}>Мои</button>
    </nav>
  `;
}

function renderHome() {
  return screenFrame(`
    <main class="page nova-home">
      <section class="hero hero--nova" aria-labelledby="home-title">
        <div class="hero__copy reveal">
          <p class="eyebrow">NOVA / точный поиск товаров</p>
          <h1 id="home-title">Покупайте по рынку.<br><em>Не наугад.</em></h1>
          <p class="lead">NOVA находит один и тот же товар и важные параметры — и показывает, где цена действительно сильнее. Без бюджета тоже можно.</p>
          <form class="hero-search" data-home-search>
            <label class="hero-search__label" for="hero-query">Какой товар ищем?</label>
            <div class="hero-search__row">
              <span class="hero-search__icon" aria-hidden="true">⌕</span>
              <input id="hero-query" name="product" maxlength="300" autocomplete="off" placeholder="Например, MacBook Air M4" />
              <button class="hero-search__submit" type="submit">Найти выгоднее <span aria-hidden="true">↗</span></button>
            </div>
          </form>
          ${renderExampleQueries()}
          <div class="hero__meta" aria-label="Преимущества">
            <span>Точное совпадение</span>
            <span>Прямые предложения</span>
            <span>Экономия только с доказательством</span>
          </div>
        </div>
        <aside class="market-simulator reveal" aria-label="Как NOVA проверяет рынок">
          <div class="market-simulator__top"><span class="simulator-live"><i></i> рынок в движении</span><span>01 / 03</span></div>
          <div class="market-simulator__query"><span aria-hidden="true">⌕</span><strong>Сопоставляем товар</strong><small>модель · версия · комплектация</small></div>
          <div class="market-simulator__track" aria-hidden="true"><span></span></div>
          <div class="market-simulator__cards">
            <article><span class="simulator-card__dot"></span><div><strong>Конфигурация</strong><small>отделяем похожие версии</small></div><b>01</b></article>
            <article><span class="simulator-card__dot simulator-card__dot--soft"></span><div><strong>Предложения</strong><small>собираем только прямые карточки</small></div><b>02</b></article>
            <article><span class="simulator-card__dot simulator-card__dot--bright"></span><div><strong>Цена рынка</strong><small>объясняем только подтверждённую выгоду</small></div><b>03</b></article>
          </div>
          <div class="market-simulator__foot"><span>Без карты и подписки</span><strong>Сейчас бесплатно</strong></div>
        </aside>
      </section>

      <section class="proof-strip reveal" aria-label="Принципы NOVA">
        <article><span>01</span><strong>Параметры важнее названия</strong><p>Размер, комплектация и состояние — не мелочи. Это условия точного сравнения.</p></article>
        <article><span>02</span><strong>Рынок, а не одна витрина</strong><p>Смотрим предложения целиком, если вы не задали свой потолок цены.</p></article>
        <article><span>03</span><strong>Выгода в рублях</strong><p>Покажем экономию, только когда её подтверждают актуальные аналоги.</p></article>
      </section>

      <section class="story-section story-section--versions reveal" id="versions" aria-labelledby="versions-title">
        <div class="story-section__head"><p class="section-label">NOVA / точность</p><h2 id="versions-title">Хорошая цена без точного совпадения —<br><em>не находка.</em></h2></div>
        <div class="version-lab">
          <div class="version-lab__copy"><p>Одна строчка в карточке меняет цену на десятки тысяч. NOVA не сравнивает то, что лишь выглядит одинаково.</p><div class="version-lab__chips" role="group" aria-label="Пример важных параметров"><button class="version-chip version-chip--selected" type="button" data-demo-chip aria-pressed="true">Модель</button><button class="version-chip" type="button" data-demo-chip aria-pressed="false">Размер</button><button class="version-chip" type="button" data-demo-chip aria-pressed="false">Комплектация</button></div></div>
          <div class="version-lab__diagram" aria-label="Сравнение товара"><div class="version-lab__model"><span class="version-lab__mark">N</span><div><small>Ищем ровно</small><strong>Товар · версия · параметры</strong></div></div><div class="version-lab__rule"><span>Совпадает</span><span>Не смешиваем</span></div><div class="version-lab__rows"><p><i class="match-dot"></i> Нужная модель и комплект</p><p><i class="miss-dot"></i> Другая версия</p><p><i class="miss-dot"></i> Другая комплектация</p></div></div>
        </div>
      </section>

      <section class="story-section story-section--how reveal" id="how" aria-labelledby="how-title">
        <div class="story-section__head story-section__head--wide"><p class="section-label">NOVA / процесс</p><h2 id="how-title">Как NOVA ищет<br><em>действительно выгодное.</em></h2><p>Не обещаем магию. Показываем понятный путь от запроса до прямого предложения.</p></div>
        <div class="process-grid">
          <article data-number="01"><span>01</span><h3>Слушаем запрос</h3><p>Фиксируем товар и то, что для вас принципиально.</p></article>
          <article data-number="02"><span>02</span><h3>Сверяем параметры</h3><p>Версия, размер, комплектация и состояние становятся фильтрами, а не примечаниями.</p></article>
          <article data-number="03"><span>03</span><h3>Сверяем рынок</h3><p>Отсекаем похожее, аксессуары и карточки без понятного соответствия.</p></article>
          <article data-number="04"><span>04</span><h3>Объясняем выбор</h3><p>Оставляем до трёх вариантов, чтобы решение было спокойным.</p></article>
        </div>
      </section>

      <section class="story-section story-section--result reveal" aria-labelledby="result-title">
        <div class="result-preview"><div class="result-preview__card"><p class="section-label">Что увидите в результате</p><h3>Один сильный вариант<br>вместо десятков вкладок.</h3><div class="result-preview__rows"><span><i></i> Точное совпадение</span><span><i></i> Цена и магазин</span><span><i></i> Объяснение выбора</span></div><p class="result-preview__note">Сумму экономии NOVA показывает только после честного сравнения текущих цен.</p></div><div class="result-preview__copy"><p class="section-label">NOVA / результат</p><h2 id="result-title">Цена становится<br><em>понятной.</em></h2><p>Вы увидите, что именно совпало с запросом, у кого купить и почему этот вариант оказался в подборе.</p><button class="button button--primary" type="button" data-action="start">Проверить свой товар <span aria-hidden="true">↗</span></button></div></div>
      </section>

      <section class="final-cta reveal" aria-labelledby="final-title"><p class="section-label">NOVA / начнём</p><h2 id="final-title">Хотите понять,<br><em>сколько он стоит на самом деле?</em></h2><button class="final-cta__button" type="button" data-action="start"><span>Какой товар ищем?</span><b aria-hidden="true">↗</b></button><p>Сейчас бесплатно · можно без бюджета</p></section>
      <footer class="site-footer"><span>NOVA © 2026</span><button type="button" data-scroll="how">Как ищем</button><button type="button" data-action="start">Новый поиск</button><button type="button" data-view="requests">Мои поиски</button></footer>
    </main>
  `, "home");
}

function stepper() {
  const total = stepCount();
  return `<div class="stepper" aria-label="Шаг ${state.step} из ${total}">
    ${Array.from({ length: total }, (_, index) => index + 1).map((number) => `<span class="step ${number < state.step ? "step--done" : ""} ${number === state.step ? "step--active" : ""}"></span>`).join("")}
  </div>`;
}

function renderWizardStep() {
  const draft = state.draft;
  const total = stepCount();
  if (state.step === 1) {
    return `
      <div class="panel__head"><p class="section-label">Шаг 1 из ${total}</p><h2>Какой товар ищем?</h2><p class="panel__intro">Укажите название, модель и важные параметры. Например: MacBook Air M4, телевизор Samsung 55 или кофемашина DeLonghi.</p></div>
      <div class="form-grid">
        <div class="form-field form-field--wide"><label for="product">Товар или модель</label><textarea id="product" rows="3" maxlength="300" placeholder="Например: MacBook Air M4 16 GB">${escapeHtml(draft.product)}</textarea></div>
      </div>`;
  }
  if (state.step === budgetStep()) {
    return `
      <div class="panel__head"><p class="section-label">Шаг ${state.step} из ${total}</p><h2>Бюджет или весь рынок</h2><p class="panel__intro">Бюджет необязателен. Оставьте поле пустым, и NOVA найдёт выгодные варианты по рынку.</p></div>
      <div class="form-grid">
        <div class="form-field"><label for="budget">Ваш бюджет <span class="field-optional">необязательно</span></label><input id="budget" inputmode="numeric" maxlength="14" placeholder="Например: 30 000 ₽" value="${escapeHtml(draft.budget)}"><small class="helper">Пусто = сравним рынок целиком</small></div>
        <div class="form-field"><label for="city">Город</label><input id="city" maxlength="80" placeholder="Например: Москва" value="${escapeHtml(draft.city)}"></div>
      </div>`;
  }
  return `
    <div class="panel__head"><p class="section-label">Шаг ${priorityStep()} из ${total}</p><h2>Что важнее всего?</h2><p class="panel__intro">Выберите один главный ориентир для подбора.</p></div>
    <div class="priority-list">
      ${priorities.map((item) => `
        <button type="button" class="priority ${draft.priority === item.id ? "priority--selected" : ""}" data-priority="${item.id}" aria-pressed="${draft.priority === item.id}">
          <span><span class="priority__title">${item.title}</span><span class="priority__text">${item.text}</span></span>
          <span class="priority__check" aria-hidden="true">${draft.priority === item.id ? "✓" : ""}</span>
        </button>`).join("")}
    </div>`;
}

function renderWizard() {
  const nextLabel = state.step === priorityStep() ? "Посмотреть заявку" : "Далее";
  return screenFrame(`
    <main class="page">
      <section class="panel" aria-labelledby="wizard-title">
        ${stepper()}
        <div id="wizard-title">${renderWizardStep()}</div>
        <div class="wizard-actions">
          <button class="button button--secondary" type="button" data-action="back">Назад</button>
          <button class="button button--primary" type="button" data-action="next">${nextLabel}</button>
        </div>
      </section>
    </main>
  `, "wizard");
}

function renderSummary() {
  const draft = state.draft;
  return screenFrame(`
    <main class="page">
      <section class="panel" aria-labelledby="summary-title">
        <div class="panel__head"><p class="section-label">NOVA / ready</p><h2 id="summary-title">Проверим рынок</h2><p class="panel__intro">NOVA передаст точный запрос поисковому движку и покажет только подходящие варианты.</p></div>
        <dl class="summary-list">
          <div class="summary-row"><dt>Товар</dt><dd>${escapeHtml(draft.product || "Не указан")}</dd></div>
          <div class="summary-row"><dt>Бюджет</dt><dd>${draft.budget ? money(draft.budget) : "Рынок целиком"}</dd></div>
          <div class="summary-row"><dt>Город</dt><dd>${escapeHtml(draft.city || "Не указан")}</dd></div>
          <div class="summary-row"><dt>Приоритет</dt><dd>${priorityName(draft.priority)}</dd></div>
          <div class="summary-row summary-row--price"><dt>Подбор</dt><dd>Бесплатно</dd></div>
        </dl>
        <aside class="local-note"><strong>Как NOVA считает выгоду</strong><p>Экономию показываем только для той же модели и версии, когда её подтверждают независимые актуальные предложения.</p></aside>
        <div class="wizard-actions">
          <button class="button button--secondary" type="button" data-action="save">Сохранить</button>
          <button class="button button--primary" type="button" data-action="search">Искать рынок</button>
        </div>
      </section>
    </main>
  `, "wizard");
}

function renderSaved() {
  const item = state.saved;
  return screenFrame(`
    <main class="page">
      <section class="panel" aria-labelledby="saved-title">
        <div class="saved-icon" aria-hidden="true">✓</div>
        <p class="section-label">NOVA / saved</p>
        <h2 id="saved-title">Поиск ${escapeHtml(item?.id || "")}</h2>
        <p class="panel__intro">Настройки хранятся на этом устройстве — чтобы можно было быстро вернуться и изменить поиск.</p>
        <aside class="local-note"><strong>Сервис бесплатный</strong><p>Никакой оплаты и подписки нет. Сейчас важнее всего точность поиска.</p></aside>
        <div class="button-row">
          <button class="button button--primary" type="button" data-view="requests">Сохранённое</button>
          <button class="button button--secondary" type="button" data-action="search">Искать рынок</button>
        </div>
      </section>
    </main>
  `, "requests");
}

function renderRequests() {
  const requests = getLocalRequests();
  const content = requests.length ? requests.map((item) => `
    <article class="request-card">
      <span class="tag">Только на этом устройстве</span>
      <h3 class="request-card__title">${escapeHtml(item.product || categoryName(item.category))}</h3>
      <p>${categoryName(item.category)} · ${escapeHtml(item.city || "Город не указан")}</p>
      <div class="request-meta"><strong>${item.budget ? money(item.budget) : "Рынок целиком"}</strong><span>NOVA search</span><span>${escapeHtml(item.createdAt)}</span></div>
    </article>`).join("") : `
    <section class="empty-state"><div class="empty-state__icon" aria-hidden="true">⌕</div><h3>Сохранённых поисков нет</h3><p class="muted">Начните поиск — NOVA запомнит настройки на этом устройстве.</p><div class="button-row" style="justify-content:center; margin-top:18px"><button class="button button--primary" type="button" data-action="start">Искать товар</button></div></section>`;
  return screenFrame(`
    <main class="page">
      <div class="screen-head screen-head--split"><div><p class="section-label">NOVA / saved</p><h2>Сохранённые поиски</h2></div><button class="button button--secondary" type="button" data-action="start">Новый поиск</button></div>
      <p class="demo-notice">Они хранятся только в этом браузере. Telegram-бот и сохранённые поиски пока не смешиваются.</p>
      <section class="request-list" aria-label="Локально сохранённые заявки">${content}</section>
    </main>
  `, "requests");
}

function renderMarket() {
  return screenFrame(`
    <main class="page">
      <div class="screen-head"><p class="section-label">NOVA / market</p><h2>Как ищет NOVA</h2><p class="muted">Без фиксированного бюджета тоже можно: NOVA сравнит рынок и покажет предложения, которые соответствуют вашему товару и важным параметрам.</p></div>
      <section class="market-steps" aria-label="Этапы поиска NOVA">
        <article><span>01</span><h3>Фиксируем товар</h3><p>Модель, версия, комплектация и состояние не смешиваются с похожими товарами.</p></article>
        <article><span>02</span><h3>Сверяем рынок</h3><p>Собираем прямые предложения и отсекаем неверную модель, аксессуары и отсутствие.</p></article>
        <article><span>03</span><h3>Объясняем выгоду</h3><p>«Экономия» появляется только при независимом сравнении текущих цен.</p></article>
      </section>
      <div class="button-row" style="margin-top:26px"><button class="button button--primary" type="button" data-action="start">Начать поиск</button><button class="button button--secondary" type="button" data-view="requests">Мои поиски</button></div>
    </main>
  `, "market");
}

function safeExternalUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
  } catch {
    return "";
  }
}

function renderMarketTrend(item) {
  const trend = item?.marketTrend;
  const direction = trend?.direction === "down" || trend?.direction === "up" ? trend.direction : "";
  const label = typeof trend?.label === "string" ? trend.label.replace(/\s+/g, " ").trim().slice(0, 140) : "";
  if (!direction || !label) return "";
  const icon = direction === "down" ? "↓" : "↑";
  return `<p class="result-card__trend result-card__trend--${direction}"><span aria-hidden="true">${icon}</span><span>${escapeHtml(label)}</span></p>`;
}

function renderResultCard(item) {
  const roleClass = item.role === "CHEAP_WITH_RISK" ? "role-label--warn" : item.role === "RELIABLE" ? "role-label--good" : "";
  const cardClass = item.role === "BEST_OVERALL" ? "result-card--best" : item.role === "CHEAP_WITH_RISK" ? "result-card--caution" : "";
  const seller = [item.store, item.seller].filter(Boolean).join(" · ");
  const directUrl = safeExternalUrl(item.url);
  return `<article class="result-card ${cardClass}">
    <div class="result-card__top"><p class="role-label ${roleClass}">${escapeHtml(item.roleLabel || "Вариант NOVA")}</p><p class="result-card__price">${escapeHtml(item.price || "Цена уточняется")}</p></div>
    <h3>${escapeHtml(item.title || "Товар")}</h3>
    ${item.facts?.length ? `<div class="result-card__facts">${item.facts.map((fact) => `<span class="fact">${escapeHtml(fact)}</span>`).join("")}</div>` : ""}
    ${item.saving ? `<p class="result-card__saving">${escapeHtml(item.saving)}</p>` : ""}
    <p class="result-card__reason">${escapeHtml(item.reason || "Цена и конфигурация проверены.")}</p>
    ${renderMarketTrend(item)}
    ${seller ? `<p class="result-card__store">${escapeHtml(seller)}</p>` : ""}
    ${item.checks?.length ? `<p class="result-card__risk">Проверьте: ${escapeHtml(item.checks.join(" · "))}</p>` : ""}
    ${directUrl ? `<a class="result-link" href="${escapeHtml(directUrl)}" target="_blank" rel="noopener noreferrer">Открыть предложение <span aria-hidden="true">↗</span></a>` : ""}
  </article>`;
}

function resultQuery(result) {
  return String(result?.query?.model || state.draft.product || "").trim();
}

function sourcesUnavailable(result) {
  return result?.searchState === "sources_unavailable"
    || String(result?.status || "").toUpperCase() === "TIMEOUT";
}

function manualYandexSearchUrl(result) {
  const query = resultQuery(result).replace(/\s+/g, " ").trim().slice(0, 240);
  if (query.length < 2) return "";
  return `https://yandex.ru/search/?${new URLSearchParams({ text: `${query} купить` }).toString()}`;
}

function needsModelClarification(result) {
  const status = String(result?.status || "").toUpperCase();
  if (status !== "NO_EXACT_MATCH" || result?.recommendations?.length) return false;
  const query = resultQuery(result);
  if (query.length < 2) return false;
  // A model/article usually carries a number or an alphanumeric code. Without
  // one, a family such as "DeLonghi Magnifica" must not be priced as one item.
  return !/(?:[a-zа-яё]+\d+|\d+[a-zа-яё]+|\b\d{2,5}\b)/iu.test(query);
}

function renderEmptyResults(result) {
  if (sourcesUnavailable(result)) {
    const manualUrl = manualYandexSearchUrl(result);
    return `<section class="empty-state empty-state--clarify"><p class="section-label">NOVA / поиск не завершён</p><div class="empty-state__icon" aria-hidden="true">⌕</div><h3>Часть источников не ответила</h3><p class="muted">Карточек пока нет, но это не значит, что товара нет на рынке. Повторите поиск — NOVA не будет сравнивать неполные данные.</p><div class="button-row empty-state__actions"><button class="button button--primary" type="button" data-action="search">Повторить поиск</button><button class="button button--secondary" type="button" data-action="edit">Изменить поиск</button>${manualUrl ? `<a class="button button--secondary" href="${escapeHtml(manualUrl)}" target="_blank" rel="noopener noreferrer">Проверить вручную в Яндексе <span aria-hidden="true">↗</span></a>` : ""}</div><p class="muted" style="margin-top:14px">Ручная проверка не становится рекомендацией NOVA.</p></section>`;
  }
  if (needsModelClarification(result)) {
    const query = resultQuery(result);
    return `<section class="empty-state empty-state--clarify"><p class="section-label">NOVA / нужна точная версия</p><div class="empty-state__icon" aria-hidden="true">⌕</div><h3>Это линейка, а не один товар</h3><p class="muted">${escapeHtml(`«${query}»`)} включает разные версии. Добавьте артикул или номер модели из карточки магазина — тогда NOVA сравнит цены только на один и тот же товар.</p><div class="button-row empty-state__actions"><button class="button button--primary" type="button" data-action="edit">Уточнить товар</button><button class="button button--secondary" type="button" data-action="search">Повторить поиск</button></div></section>`;
  }
  return `<section class="empty-state"><div class="empty-state__icon" aria-hidden="true">⌕</div><h3>Точных вариантов пока нет</h3><p class="muted">Попробуйте уточнить модель, размер, комплектацию или другие важные параметры.</p><div class="button-row empty-state__actions"><button class="button button--primary" type="button" data-action="edit">Изменить поиск</button><button class="button button--secondary" type="button" data-action="search">Повторить поиск</button></div></section>`;
}

function normaliseSearchError(error) {
  if (error && typeof error === "object") {
    return {
      kind: String(error.kind || "unknown"),
      status: Number(error.status) || 0,
      code: String(error.code || "").trim(),
      message: String(error.message || "").trim(),
    };
  }
  return {
    kind: "network",
    status: 0,
    code: "",
    message: String(error || "Подключитесь к локальному NOVA-серверу, чтобы запустить реальный поиск."),
  };
}

function renderSearchError(error) {
  const safeMessage = escapeHtml(error.message || "Попробуйте ещё раз.");
  const actions = `<div class="button-row"><button class="button button--primary" type="button" data-action="search">Повторить</button><button class="button button--secondary" type="button" data-action="edit">Изменить запрос</button></div>`;

  if (error.kind === "network") {
    return screenFrame(`
      <main class="page"><section class="panel search-state"><p class="section-label">NOVA / offline</p><h2>Поиск пока не запущен</h2><p class="panel__intro">${safeMessage}</p><aside class="local-note"><strong>Локальный запуск</strong><p>Откройте сайт через <code>python tools/run_nova_web.py</code>, затем повторите поиск.</p></aside>${actions}</section></main>
    `, "results");
  }

  if (error.status === 503) {
    const backendUnavailable = error.code === "search_backend_unavailable";
    const note = backendUnavailable
      ? `<aside class="local-note"><strong>Нужно обновить зависимости</strong><p>В терминале выполните <code>python -m pip install -r requirements.txt</code>, затем перезапустите NOVA.</p></aside>`
      : `<aside class="local-note"><strong>Сервер запущен</strong><p>Поисковый модуль или источники сейчас недоступны. Повторите попытку через минуту.</p></aside>`;
    return screenFrame(`
      <main class="page"><section class="panel search-state"><p class="section-label">NOVA / временно недоступен</p><h2>${backendUnavailable ? "Сервер запущен, поиск не загрузился" : "Поиск временно недоступен"}</h2><p class="panel__intro">${safeMessage}</p>${note}${actions}</section></main>
    `, "results");
  }

  if (error.status === 400) {
    return screenFrame(`
      <main class="page"><section class="panel search-state"><p class="section-label">NOVA / нужно уточнение</p><h2>Некорректный запрос</h2><p class="panel__intro">${safeMessage}</p><aside class="local-note"><strong>Проверьте товар</strong><p>Укажите модель, артикул или важные параметры и попробуйте снова.</p></aside><div class="button-row"><button class="button button--primary" type="button" data-action="edit">Изменить запрос</button><button class="button button--secondary" type="button" data-action="search">Повторить</button></div></section></main>
    `, "results");
  }

  if (error.status === 401) {
    return screenFrame(`
      <main class="page"><section class="panel search-state"><p class="section-label">NOVA / Telegram</p><h2>Откройте поиск через Telegram</h2><p class="panel__intro">${safeMessage}</p><aside class="local-note"><strong>Нужна авторизация</strong><p>Откройте NOVA из меню Telegram-бота, чтобы продолжить поиск.</p></aside><div class="button-row"><button class="button button--primary" type="button" data-action="edit">Изменить запрос</button></div></section></main>
    `, "results");
  }

  return screenFrame(`
    <main class="page"><section class="panel search-state"><p class="section-label">NOVA / ошибка поиска</p><h2>Не удалось выполнить поиск</h2><p class="panel__intro">${safeMessage}</p>${actions}</section></main>
  `, "results");
}

function renderResults() {
  const search = state.search;
  if (search.loading) {
    return screenFrame(`
      <main class="page"><section class="panel search-state" aria-live="polite"><div class="search-orbit" aria-hidden="true"></div><p class="section-label">NOVA / scanning</p><h2>Сверяем рынок</h2><p class="panel__intro">Ищем точную модель и проверяем предложения. Обычно это занимает до полуминуты.</p></section></main>
    `, "results");
  }
  if (search.error) {
    return renderSearchError(normaliseSearchError(search.error));
  }
  const result = search.result || { recommendations: [], message: "Сначала настройте поиск." };
  const clarification = needsModelClarification(result);
  const resultMessage = clarification
    ? "Не сравниваем разные версии товара как один и тот же вариант."
    : (result.message || "");
  return screenFrame(`
    <main class="page"><div class="screen-head screen-head--split"><div><p class="section-label">NOVA / live result</p><h2>Рынок для вас</h2><p class="muted">${escapeHtml(resultMessage)}</p></div><button class="button button--secondary" type="button" data-action="edit">Изменить поиск</button></div>
      <section class="result-list" aria-label="Результаты поиска NOVA">${result.recommendations?.length ? result.recommendations.map(renderResultCard).join("") : renderEmptyResults(result)}</section>
      ${result.recommendations?.length ? `<div class="button-row" style="margin-top:22px"><button class="button button--primary" type="button" data-action="search">Обновить рынок</button><button class="button button--secondary" type="button" data-action="save">Сохранить поиск</button></div>` : ""}
    </main>
  `, "results");
}

let revealObserver = null;

function bindVisualMotion() {
  const revealItems = app.querySelectorAll(".reveal");
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
  revealObserver?.disconnect();
  if (!revealItems.length || reducedMotion || !("IntersectionObserver" in window)) {
    revealItems.forEach((item) => item.classList.add("is-visible"));
    return;
  }
  revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("is-visible");
      revealObserver?.unobserve(entry.target);
    });
  }, { threshold: 0.12 });
  revealItems.forEach((item) => revealObserver.observe(item));

  const simulator = app.querySelector(".market-simulator");
  if (simulator) {
    simulator.addEventListener("pointermove", (event) => {
      const bounds = simulator.getBoundingClientRect();
      simulator.style.setProperty("--pointer-x", `${((event.clientX - bounds.left) / bounds.width) * 100}%`);
      simulator.style.setProperty("--pointer-y", `${((event.clientY - bounds.top) / bounds.height) * 100}%`);
    });
    simulator.addEventListener("pointerleave", () => {
      simulator.style.removeProperty("--pointer-x");
      simulator.style.removeProperty("--pointer-y");
    });
  }
}

function render() {
  if (state.view === "wizard") app.innerHTML = renderWizard();
  else if (state.view === "summary") app.innerHTML = renderSummary();
  else if (state.view === "saved") app.innerHTML = renderSaved();
  else if (state.view === "requests") app.innerHTML = renderRequests();
  else if (state.view === "market") app.innerHTML = renderMarket();
  else if (state.view === "results") app.innerHTML = renderResults();
  else app.innerHTML = renderHome();
  bindVisualMotion();
  telegramBridge.syncBackButton(goBack, state.view !== "home");
}

function readInputs() {
  if (state.step === 1) state.draft.product = document.querySelector("#product")?.value.trim() || "";
  if (state.step === budgetStep()) {
    state.draft.budget = document.querySelector("#budget")?.value.trim() || "";
    state.draft.city = document.querySelector("#city")?.value.trim() || "";
  }
}

function nextStep() {
  readInputs();
  if (state.step === 1 && state.draft.product.length < 2) {
    document.querySelector("#product")?.focus();
    return;
  }
  if (state.step === budgetStep() && !state.draft.city) {
    document.querySelector("#city")?.focus();
    return;
  }
  if (state.step === priorityStep()) state.view = "summary";
  else state.step += 1;
  render();
}

function saveDraft() {
  const now = new Date();
  const item = {
    ...state.draft,
    id: `W-${String(now.getFullYear()).slice(-2)}${String(now.getMonth() + 1).padStart(2, "0")}${String(now.getDate()).padStart(2, "0")}-${String(now.getTime()).slice(-4)}`,
    createdAt: now.toLocaleDateString("ru-RU", { day: "numeric", month: "long" }),
  };
  setLocalRequests([item, ...getLocalRequests()].slice(0, 20));
  state.saved = item;
  state.view = "saved";
  render();
}

function searchPayload() {
  return {
    category: state.draft.category,
    product: state.draft.product,
    budget: state.draft.budget,
    city: state.draft.city,
    priority: state.draft.priority,
  };
}

async function runSearch() {
  telegramBridge.haptic("medium");
  state.search = { loading: true, result: null, error: null };
  state.view = "results";
  render();
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(searchPayload()),
    });
    let data = {};
    try {
      data = await response.json();
    } catch {
      // The static preview server has no NOVA API; show a useful next action.
    }
    if (!response.ok) {
      const fallbackMessage = response.status === 503
        ? "Поиск временно недоступен. Попробуйте ещё раз."
        : "Не удалось выполнить поиск. Проверьте запрос и попробуйте снова.";
      state.search = {
        loading: false,
        result: null,
        error: {
          kind: "http",
          status: response.status,
          code: typeof data?.code === "string" ? data.code.trim() : "",
          message: typeof data?.message === "string" && data.message.trim() ? data.message.trim() : fallbackMessage,
        },
      };
      telegramBridge.haptic("warning");
    } else {
      state.search = { loading: false, result: data, error: null };
      telegramBridge.haptic("success");
    }
  } catch {
    state.search = {
      loading: false,
      result: null,
      error: {
        kind: "network",
        status: 0,
        code: "",
        message: "Подключитесь к локальному NOVA-серверу, чтобы запустить реальный поиск.",
      },
    };
    telegramBridge.haptic("warning");
  }
  render();
}

function startNewDraft(origin = state.view, product = "") {
  state.returnView = origin && origin !== "wizard" ? origin : "home";
  state.draft = initialDraft();
  state.draft.product = product.trim();
  state.step = 1;
  state.view = "wizard";
  render();
}

function scrollToHomeSection(sectionId) {
  const scroll = () => document.querySelector(`#${sectionId}`)?.scrollIntoView({
    behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth",
    block: "start",
  });
  if (state.view !== "home") {
    state.view = "home";
    render();
    requestAnimationFrame(scroll);
    return;
  }
  scroll();
}

function goBack() {
  if (state.view === "wizard") {
    readInputs();
    if (state.step === 1) state.view = state.returnView || "home";
    else state.step -= 1;
  } else if (state.view === "summary") {
    state.step = priorityStep();
    state.view = "wizard";
  } else if (state.view !== "home") {
    state.view = "home";
  } else {
    return;
  }
  render();
}

async function connectTelegram() {
  if (!telegramBridge.isMiniApp) return;
  state.telegram = { status: "connecting", displayName: "" };
  render();
  const session = await telegramBridge.openSession();
  state.telegram = session;
  render();
}

app.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("[data-home-search]")) return;
  event.preventDefault();
  const product = new FormData(form).get("product");
  startNewDraft("home", String(product || ""));
});

app.addEventListener("click", (event) => {
  const target = event.target.closest("button");
  if (!target || target.disabled) return;
  if (target.dataset.scroll) {
    scrollToHomeSection(target.dataset.scroll);
    return;
  }
  if (target.dataset.exampleQuery) {
    const input = document.querySelector("#hero-query");
    if (input instanceof HTMLInputElement) {
      input.value = target.dataset.exampleQuery;
      input.focus();
    }
    return;
  }
  if (target.hasAttribute("data-demo-chip")) {
    app.querySelectorAll("[data-demo-chip]").forEach((chip) => {
      const selected = chip === target;
      chip.classList.toggle("version-chip--selected", selected);
      chip.setAttribute("aria-pressed", String(selected));
    });
    return;
  }
  if (target.dataset.action === "rotate-examples") {
    rotateExampleQueries();
    return;
  }
  if (target.dataset.view) {
    if (target.dataset.view === "wizard") {
      startNewDraft(state.view);
      return;
    }
    state.view = target.dataset.view;
    render();
    return;
  }
  if (target.dataset.priority) {
    state.draft.priority = target.dataset.priority;
    render();
    return;
  }
  if (target.dataset.action === "start") startNewDraft(state.view);
  if (target.dataset.action === "next") nextStep();
  if (target.dataset.action === "search") void runSearch();
  if (target.dataset.action === "back") goBack();
  if (target.dataset.action === "edit") {
    state.step = 1;
    state.view = "wizard";
    render();
  }
  if (target.dataset.action === "save") saveDraft();
  if (target.dataset.action === "retry-telegram") void connectTelegram();
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("service-worker.js?v=13"));
}

render();
telegramBridge.prepare();
void connectTelegram();
window.addEventListener("telegram-web-app-ready", () => {
  telegramBridge.prepare();
  void connectTelegram();
});
