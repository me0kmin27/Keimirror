import { translations } from './i18n.js';

const root = document.documentElement;
const themes = ['auto', 'light', 'dark'];

const mirrorTargets = {
  mirror1: { url: 'https://mirror.keiminem.com/', titleKey: 'mirrorOneTitle' },
  mirror2: { url: 'https://mirror2.keiminem.com/', titleKey: 'mirrorTwoTitle' },
};

async function loadComponent(host) {
  const componentPath = host.dataset.component;
  const response = await fetch(componentPath);

  if (!response.ok) {
    throw new Error(`Failed to load component: ${componentPath}`);
  }

  host.innerHTML = await response.text();
  const nestedComponents = [...host.querySelectorAll('[data-component]')];
  await Promise.all(nestedComponents.map(loadComponent));
}

async function loadComponents() {
  const componentHosts = [...document.querySelectorAll('[data-component]')];
  await Promise.all(componentHosts.map(loadComponent));
}

function applyTheme(theme) {
  const themeToggle = document.getElementById('theme-toggle');

  if (theme === 'auto') {
    root.removeAttribute('data-theme');
  } else {
    root.dataset.theme = theme;
  }

  if (themeToggle) themeToggle.textContent = theme.charAt(0).toUpperCase() + theme.slice(1);
  localStorage.setItem('keimirror-theme', theme);
}

function applyLanguage(language) {
  const languageToggle = document.getElementById('language-toggle');
  const dictionary = translations[language] || translations.en;

  root.lang = language;
  document.querySelectorAll('[data-i18n]').forEach((element) => {
    const key = element.dataset.i18n;
    if (dictionary[key]) element.textContent = dictionary[key];
  });

  if (languageToggle) languageToggle.textContent = language === 'en' ? '한국어' : 'English';
  localStorage.setItem('keimirror-language', language);
}


function showMirrorList(mirrorId) {
  const mirror = mirrorTargets[mirrorId];
  if (!mirror) return;

  const placeholder = document.querySelector('[data-mirror-placeholder]');
  const mirrorList = document.querySelector('[data-mirror-list]');
  const mirrorTitle = document.querySelector('[data-mirror-title]');
  const mirrorFrame = document.querySelector('[data-mirror-frame]');
  const mirrorOpen = document.querySelector('[data-mirror-open]');
  const dictionary = translations[root.lang] || translations.en;

  if (!mirrorList || !mirrorFrame || !mirrorOpen) return;

  const hero = mirrorList.closest('.hero');

  if (placeholder) placeholder.hidden = true;
  mirrorList.hidden = false;
  hero?.classList.add('is-showing-mirror-list');
  mirrorList.dataset.activeMirror = mirrorId;
  mirrorFrame.src = mirror.url;
  mirrorOpen.href = mirror.url;

  if (mirrorTitle) mirrorTitle.textContent = dictionary[mirror.titleKey] || mirror.url;

  document.querySelectorAll('[data-mirror-trigger]').forEach((trigger) => {
    const isActive = trigger.dataset.mirrorTrigger === mirrorId;
    trigger.classList.toggle('primary', isActive);
    trigger.setAttribute('aria-pressed', String(isActive));
  });
}

function bindControls() {
  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    const current = localStorage.getItem('keimirror-theme') || 'auto';
    applyTheme(themes[(themes.indexOf(current) + 1) % themes.length]);
  });

  document.getElementById('language-toggle')?.addEventListener('click', () => {
    const current = localStorage.getItem('keimirror-language') || 'en';
    const nextLanguage = current === 'en' ? 'ko' : 'en';
    applyLanguage(nextLanguage);

    const activeMirror = document.querySelector('[data-mirror-list]')?.dataset.activeMirror;
    if (activeMirror) showMirrorList(activeMirror);
  });

  document.querySelectorAll('[data-mirror-trigger]').forEach((trigger) => {
    trigger.setAttribute('role', 'button');
    trigger.setAttribute('aria-pressed', 'false');
    trigger.addEventListener('click', (event) => {
      event.preventDefault();
      showMirrorList(trigger.dataset.mirrorTrigger);
    });
  });

  document.getElementById('visitor-ip-check')?.addEventListener('click', showVisitorIp);
}

function detectInitialLanguage() {
  const queryLanguage = new URLSearchParams(window.location.search).get('lang');
  return queryLanguage === 'ko' ? 'ko' : (localStorage.getItem('keimirror-language') || 'en');
}

async function showVisitorIp() {
  const visitorIp = document.getElementById('visitor-ip');
  if (!visitorIp) return;

  visitorIp.textContent = translations[root.lang]?.checkingIp || translations.en.checkingIp;

  try {
    const response = await fetch('https://api.ipify.org?format=json');
    if (!response.ok) throw new Error('IP lookup failed');
    const data = await response.json();
    visitorIp.textContent = data.ip || translations[root.lang]?.ipUnknown || translations.en.ipUnknown;
  } catch {
    visitorIp.textContent = translations[root.lang]?.ipUnknown || translations.en.ipUnknown;
  }
}

function markAppReady() {
  document.body.classList.remove('app-loading');
  document.body.classList.add('app-ready');
}

async function initialize() {
  try {
    await loadComponents();
    bindControls();
    applyTheme(localStorage.getItem('keimirror-theme') || 'auto');
    applyLanguage(detectInitialLanguage());
  } finally {
    markAppReady();
  }
}

initialize().catch((error) => {
  console.error(error);
});
