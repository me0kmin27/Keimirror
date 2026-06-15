import { translations } from './i18n.js';

const root = document.documentElement;
const themes = ['auto', 'light', 'dark'];

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

function bindControls() {
  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    const current = localStorage.getItem('keimirror-theme') || 'auto';
    applyTheme(themes[(themes.indexOf(current) + 1) % themes.length]);
  });

  document.getElementById('language-toggle')?.addEventListener('click', () => {
    const current = localStorage.getItem('keimirror-language') || 'en';
    applyLanguage(current === 'en' ? 'ko' : 'en');
  });
}

function detectInitialLanguage() {
  const queryLanguage = new URLSearchParams(window.location.search).get('lang');
  return queryLanguage === 'ko' ? 'ko' : (localStorage.getItem('keimirror-language') || 'en');
}

async function showVisitorIp() {
  const visitorIp = document.getElementById('visitor-ip');
  if (!visitorIp) return;

  try {
    const response = await fetch('https://api.ipify.org?format=json');
    if (!response.ok) throw new Error('IP lookup failed');
    const data = await response.json();
    visitorIp.textContent = data.ip || translations[root.lang]?.ipUnknown || translations.en.ipUnknown;
  } catch {
    visitorIp.textContent = translations[root.lang]?.ipUnknown || translations.en.ipUnknown;
  }
}

async function initialize() {
  await loadComponents();
  bindControls();
  applyTheme(localStorage.getItem('keimirror-theme') || 'auto');
  applyLanguage(detectInitialLanguage());
  showVisitorIp();
}

initialize().catch((error) => {
  console.error(error);
});
