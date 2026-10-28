import { translations } from './i18n.js';

const root = document.documentElement;
const themes = ['auto', 'light', 'dark'];

function readPreference(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function savePreference(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage may be blocked or full; the current page still works.
  }
}
const themeLabels = { auto: 'themeAuto', light: 'themeLight', dark: 'themeDark' };


const mirrorTargets = {
  mirror1: { url: 'https://mirror.keiminem.com/', titleKey: 'mirrorOneTitle' },
  mirror2: { url: 'https://mirror2.keiminem.com/', titleKey: 'mirrorTwoTitle' },
};

const boundEvents = new WeakMap();

function bindOnce(element, type, handler) {
  if (!element) return;
  const types = boundEvents.get(element) || new Set();
  if (types.has(type)) return;
  types.add(type);
  boundEvents.set(element, types);
  element.addEventListener(type, handler);
}

function componentError(host) {
  const fallback = document.createElement('div');
  fallback.dataset.componentError = '';
  fallback.className = 'component-error';
  fallback.setAttribute('role', 'status');
  const message = document.createElement('p');
  message.dataset.i18n = 'componentFailure';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.className = 'button';
  retry.dataset.componentRetry = '';
  retry.dataset.i18n = 'componentRetry';
  const dictionary = translations[root.lang] || translations.en;
  message.textContent = dictionary.componentFailure;
  retry.textContent = dictionary.componentRetry;
  retry.addEventListener('click', async () => {
    retry.disabled = true;
    await loadComponent(host);
    bindControls();
    applyTheme(root.dataset.theme || 'auto');
    applyLanguage(root.lang);
  });
  fallback.append(message, retry);
  host.replaceChildren(fallback);
}

async function loadComponent(host) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch(host.dataset.component, { signal: controller.signal });
    if (!response.ok) throw new Error('Component unavailable');
    host.innerHTML = await response.text();
    clearTimeout(timeout);
    const nested = [...host.querySelectorAll('[data-component]')]
      .filter(child => child.parentElement.closest('[data-component]') === host);
    await Promise.allSettled(nested.map(loadComponent));
  } catch {
    componentError(host);
  } finally {
    clearTimeout(timeout);
  }
}

async function loadComponents() {
  const hosts = [...document.querySelectorAll('[data-component]')]
    .filter(host => !host.parentElement.closest('[data-component]'));
  await Promise.allSettled(hosts.map(loadComponent));
}

function renderThemeControl() {
  const theme = root.dataset.theme || 'auto';
  const button = document.getElementById('theme-toggle');
  const dictionary = translations[root.lang] || translations.en;
  if (button) {
    button.textContent = dictionary[themeLabels[theme]];
    button.setAttribute('aria-label', dictionary.themeControl.replace('{theme}', dictionary[themeLabels[theme]]));
  }
}

function applyTheme(theme) {
  if (!themes.includes(theme)) theme = 'auto';

  if (theme === 'auto') {
    root.removeAttribute('data-theme');
  } else {
    root.dataset.theme = theme;
  }

  renderThemeControl();
  savePreference('keimirror-theme', theme);
}

function applyLanguage(language) {
  if (!['en', 'ko'].includes(language)) language = 'en';
  const languageToggle = document.getElementById('language-toggle');
  const dictionary = translations[language] || translations.en;

  root.lang = language;
  document.querySelectorAll('[data-i18n]').forEach((element) => {
    const key = element.dataset.i18n;
    if (dictionary[key]) element.textContent = dictionary[key];
  });

  for (const [marker, attribute] of [['data-i18n-placeholder', 'placeholder'], ['data-i18n-aria-label', 'aria-label']]) {
    document.querySelectorAll(`[${marker}]`).forEach(element => {
      const text = dictionary[element.getAttribute(marker)];
      if (text) element.setAttribute(attribute, text);
    });
  }
  document.querySelectorAll('[data-project-directory]').forEach(filterProjects);
  renderCopyStatus();
  renderMirrorLabels();
  renderVisitorIp();
  renderThemeControl();
  if (languageToggle) {
    languageToggle.textContent = language === 'en' ? '한국어' : 'English';
    languageToggle.setAttribute('aria-label', dictionary.languageControl);
  }
  savePreference('keimirror-language', language);
}

let mirrorOpener;
let mirrorTimeout;
let mirrorFeedback = 'mirrorPreviewNote';

function hideMirrorList() {
  clearTimeout(mirrorTimeout);
  const placeholder = document.querySelector('[data-mirror-placeholder]');
  const mirrorList = document.querySelector('[data-mirror-list]');
  const mirrorFrame = document.querySelector('[data-mirror-frame]');
  const hero = mirrorList?.closest('.hero');

  if (placeholder) placeholder.hidden = false;
  if (mirrorList) {
    mirrorList.hidden = true;
    delete mirrorList.dataset.activeMirror;
  }
  if (mirrorFrame) mirrorFrame.removeAttribute('src');
  hero?.classList.remove('is-showing-mirror-list');
  if (mirrorOpener?.isConnected) mirrorOpener.focus();

  document.querySelectorAll('[data-mirror-trigger]').forEach((trigger) => {
    trigger.classList.remove('primary');
    trigger.setAttribute('aria-pressed', 'false');
  });
}

function renderMirrorLabels() {
  const mirrorId = document.querySelector('[data-mirror-list]')?.dataset.activeMirror;
  const mirror = mirrorTargets[mirrorId];
  if (!mirror) return;
  const dictionary = translations[root.lang] || translations.en;
  const title = document.querySelector('[data-mirror-title]');
  const frame = document.querySelector('[data-mirror-frame]');
  if (title) title.textContent = dictionary[mirror.titleKey];
  if (frame) frame.title = dictionary.mirrorFrameTitle.replace('{mirror}', dictionary[mirror.titleKey]);
  const status = document.querySelector('[data-mirror-status]');
  if (status) status.textContent = dictionary[mirrorFeedback];
}

function showMirrorList(mirrorId, opener) {
  const mirror = mirrorTargets[mirrorId];
  if (!mirror) return;

  const placeholder = document.querySelector('[data-mirror-placeholder]');
  const mirrorList = document.querySelector('[data-mirror-list]');
  const mirrorFrame = document.querySelector('[data-mirror-frame]');
  const mirrorOpen = document.querySelector('[data-mirror-open]');

  if (!mirrorList || !mirrorFrame || !mirrorOpen) return;

  const hero = mirrorList.closest('.hero');

  if (placeholder) placeholder.hidden = true;
  mirrorList.hidden = false;
  hero?.classList.add('is-showing-mirror-list');
  mirrorList.dataset.activeMirror = mirrorId;
  if (opener) mirrorOpener = opener;
  clearTimeout(mirrorTimeout);
  mirrorFeedback = 'mirrorLoading';
  mirrorTimeout = setTimeout(() => {
    mirrorFeedback = 'mirrorPreviewNote';
    renderMirrorLabels();
  }, 10000);
  mirrorFrame.loading = 'eager';
  // Keep the existing unsandboxed directory: restrictive flags can break downloads
  // and directory links. It is cross-origin, and the direct link remains available.
  mirrorFrame.src = mirror.url;
  mirrorOpen.href = mirror.url;

  renderMirrorLabels();

  document.querySelectorAll('[data-mirror-trigger]').forEach((trigger) => {
    const isActive = trigger.dataset.mirrorTrigger === mirrorId;
    trigger.classList.toggle('primary', isActive);
    trigger.setAttribute('aria-pressed', String(isActive));
  });
}

let sponsorOpener;

function setSponsorPopup(open) {
  const sponsorModal = document.querySelector('[data-sponsor-modal]');
  if (!sponsorModal || typeof sponsorModal.showModal !== 'function') return;

  if (open && !sponsorModal.open) {
    sponsorOpener = document.activeElement;
    sponsorModal.hidden = false;
    sponsorModal.showModal();
  } else if (!open) {
    if (sponsorModal.open) sponsorModal.close();
    sponsorModal.hidden = true;
    if (sponsorOpener?.isConnected) sponsorOpener.focus();
  }
  document.querySelectorAll('[data-sponsor-toggle]').forEach((trigger) => {
    // Keep opener colors stable: inverted foreground/background cross-fades lose contrast.
    trigger.setAttribute('aria-expanded', String(open));
  });
}

function toggleSponsorPopup() {
  const sponsorModal = document.querySelector('[data-sponsor-modal]');
  setSponsorPopup(Boolean(sponsorModal?.hidden));
}

function filterProjects(directory) {
  const search = directory.querySelector('#project-search');
  const terms = (search?.value || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  const category = directory.querySelector('[data-project-filter][aria-pressed="true"]')?.dataset.projectFilter || 'all';
  const entries = [...directory.querySelectorAll('[data-project]')];
  let count = 0;
  for (const entry of entries) {
    const text = (entry.dataset.search || entry.textContent).toLowerCase();
    const matches = (category === 'all' || entry.dataset.category === category)
      && terms.every(term => text.includes(term));
    entry.hidden = !matches;
    if (matches) count += 1;
  }
  const empty = directory.querySelector('[data-project-empty]');
  if (empty) empty.hidden = count !== 0;
  const status = directory.querySelector('[data-project-count]');
  if (status) {
    const dictionary = translations[root.lang] || translations.en;
    status.textContent = dictionary.projectCount.replace('{count}', count).replace('{total}', entries.length);
  }
}

function bindProjectDirectory() {
  document.querySelectorAll('[data-project-directory]').forEach(directory => {
    bindOnce(directory.querySelector('#project-search'), 'input', () => filterProjects(directory));
    directory.querySelectorAll('[data-project-filter]').forEach(button => {
      bindOnce(button, 'click', () => {
        directory.querySelectorAll('[data-project-filter]').forEach(filter => {
          filter.setAttribute('aria-pressed', String(filter === button));
        });
        filterProjects(directory);
      });
    });
    directory.querySelectorAll('[data-project-reset]').forEach(button => {
      bindOnce(button, 'click', () => {
        const search = directory.querySelector('#project-search');
        if (search) search.value = '';
        directory.querySelectorAll('[data-project-filter]').forEach(filter => {
          filter.setAttribute('aria-pressed', String(filter.dataset.projectFilter === 'all'));
        });
        filterProjects(directory);
        search?.focus();
      });
    });
    filterProjects(directory);
  });
}

let copyState = null;

function renderCopyStatus() {
  const status = document.querySelector('[data-copy-status]');
  if (!status || !copyState) return;
  const dictionary = translations[root.lang] || translations.en;
  status.textContent = `${dictionary[copyState.key]} ${copyState.value}`;
}

async function copyAddress(button) {
  const value = button.dataset.copy;
  button.disabled = true;
  try {
    await navigator.clipboard.writeText(value);
    copyState = { key: 'copySuccess', value };
  } catch {
    copyState = { key: 'copyFailure', value };
    const address = button.parentElement.querySelector('code');
    const selection = window.getSelection();
    if (address?.textContent === value && selection) {
      const range = document.createRange();
      range.selectNodeContents(address);
      selection.removeAllRanges();
      selection.addRange(range);
    }
  } finally {
    button.disabled = false;
    renderCopyStatus();
  }
}

function bindControls() {
  document.querySelectorAll('[data-copy]').forEach(button => {
    bindOnce(button, 'click', () => copyAddress(button));
  });
  bindProjectDirectory();
  bindOnce(document.getElementById('theme-toggle'), 'click', () => {
    const current = root.dataset.theme || 'auto';
    applyTheme(themes[(themes.indexOf(current) + 1) % themes.length]);
  });

  bindOnce(document.getElementById('language-toggle'), 'click', () => {
    const current = root.lang;
    const nextLanguage = current === 'en' ? 'ko' : 'en';
    applyLanguage(nextLanguage);
  });

  const mirrorFrame = document.querySelector('[data-mirror-frame]');
  const mirrorList = document.querySelector('[data-mirror-list]');
  if (mirrorFrame && mirrorList) {
    let status = mirrorList.querySelector('[data-mirror-status], .preview-note');
    if (!status) {
      status = document.createElement('p');
      status.className = 'preview-note';
      mirrorFrame.before(status);
    }
    status.dataset.mirrorStatus = '';
    status.setAttribute('role', 'status');
    const showHelp = () => {
      if (!mirrorList.dataset.activeMirror || mirrorList.hidden) return;
      clearTimeout(mirrorTimeout);
      // An iframe load event also fires on failure. Never label it healthy/online.
      mirrorFeedback = 'mirrorPreviewNote';
      renderMirrorLabels();
    };
    bindOnce(mirrorFrame, 'load', showHelp);
    bindOnce(mirrorFrame, 'error', showHelp);
  }

  document.querySelectorAll('[data-mirror-trigger]').forEach((trigger) => {
    const mirrorList = document.querySelector('[data-mirror-list]');
    if (!mirrorTargets[trigger.dataset.mirrorTrigger] || !mirrorList
      || !document.querySelector('[data-mirror-frame]') || !document.querySelector('[data-mirror-open]')) return;
    trigger.setAttribute('role', 'button');
    trigger.setAttribute('aria-pressed', String(mirrorList.dataset.activeMirror === trigger.dataset.mirrorTrigger && !mirrorList.hidden));
    bindOnce(trigger, 'keydown', event => {
      if (trigger.tagName === 'A' && event.key === ' ' && !event.repeat
        && !event.altKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        trigger.click();
      }
    });
    bindOnce(trigger, 'click', (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      event.preventDefault();
      const mirrorList = document.querySelector('[data-mirror-list]');
      const isActive = mirrorList?.dataset.activeMirror === trigger.dataset.mirrorTrigger && !mirrorList.hidden;

      if (isActive) {
        hideMirrorList();
        return;
      }

      showMirrorList(trigger.dataset.mirrorTrigger, trigger);
    });
  });

  document.querySelectorAll('[data-mirror-close]').forEach(button => bindOnce(button, 'click', hideMirrorList));

  document.querySelectorAll('[data-sponsor-toggle]').forEach((trigger) => {
    bindOnce(trigger, 'click', toggleSponsorPopup);
  });

  document.querySelectorAll('[data-sponsor-close]').forEach((trigger) => {
    bindOnce(trigger, 'click', () => setSponsorPopup(false));
  });

  const sponsorModal = document.querySelector('[data-sponsor-modal]');
  bindOnce(sponsorModal, 'cancel', (event) => {
    event.preventDefault();
    setSponsorPopup(false);
  });
  bindOnce(sponsorModal, 'close', () => {
    if (!sponsorModal.open) setSponsorPopup(false);
  });
  bindOnce(sponsorModal, 'click', (event) => {
    if (event.target === sponsorModal) setSponsorPopup(false);
  });

  bindOnce(document.getElementById('visitor-ip-check'), 'click', showVisitorIp);
}

function detectInitialLanguage() {
  const queryLanguage = new URLSearchParams(window.location.search).get('lang');
  return ['en', 'ko'].includes(queryLanguage) ? queryLanguage : readPreference('keimirror-language', 'en');
}

let ipState = 'idle';
let visitorAddress = '';

function renderVisitorIp() {
  const status = document.getElementById('visitor-ip');
  const button = document.getElementById('visitor-ip-check');
  const dictionary = translations[root.lang] || translations.en;
  if (status) {
    status.setAttribute('role', 'status');
    status.dataset.ipState = ipState;
    status.textContent = ipState === 'success' ? visitorAddress
      : dictionary[{ idle: 'ipIdle', pending: 'checkingIp', failure: 'ipUnknown' }[ipState]];
  }
  if (button) button.disabled = ipState === 'pending';
}

function validIpAddress(value) {
  if (typeof value !== 'string' || value.length > 45) return false;
  if (/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)) {
    return value.split('.').every(part => Number(part) <= 255);
  }
  if (!value.includes(':') || !/^[a-fA-F0-9:.]+$/.test(value)) return false;
  try {
    // Let the browser's URL parser validate IPv6 compression and embedded IPv4.
    return new URL(`http://[${value}]/`).hostname.startsWith('[');
  } catch {
    return false;
  }
}

async function showVisitorIp() {
  if (!document.getElementById('visitor-ip') || ipState === 'pending') return;
  ipState = 'pending';
  renderVisitorIp();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch('https://api.ipify.org?format=json', { signal: controller.signal });
    if (!response.ok) throw new Error('IP lookup failed');
    const data = await response.json();
    if (!validIpAddress(data?.ip)) throw new Error('Invalid IP response');
    visitorAddress = data.ip;
    ipState = 'success';
  } catch {
    visitorAddress = '';
    ipState = 'failure';
  } finally {
    clearTimeout(timeout);
    renderVisitorIp();
  }
}

function markAppReady() {
  document.body.classList.remove('app-loading');
  document.body.classList.add('app-ready');
}

async function initialize() {
  try {
    if (document.body.dataset.rendered !== 'true') await loadComponents();
    bindControls();
    applyTheme(readPreference('keimirror-theme', 'auto'));
    applyLanguage(detectInitialLanguage());
  } finally {
    markAppReady();
  }
}

initialize().catch((error) => {
  console.error(error);
});
