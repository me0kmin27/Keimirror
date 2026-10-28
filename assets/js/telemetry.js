import { translations } from './i18n.js';

const telemetryGroups = {
  archlinux: ['archlinux'], gentoo: ['gentoo', 'gentoo-portage'], kali: ['kali', 'kali-images'],
  cachyos: ['cachyos'], artix: ['artix', 'artix-iso', 'artix-weekly-iso'], opnsense: ['opnsense'],
  endeavouros: ['endeavouros'], fyralabs: ['fyralabs'], manjaro: ['manjaro'], proxmox: ['proxmox'],
};
const telemetryFields = ['collected_at', 'size_measured_at', 'last_attempt_at', 'last_finished_at',
  'last_success_at', 'exit_code', 'sync_rx_bps', 'sync_tx_bps', 'rates_observed_at',
  'http_bytes_60s', 'rsync_bytes_60s', 'traffic_observed_since'];
let telemetryData = null;
let telemetryFetchState = 'idle';
const telemetryMirrorIds = ['mirror1', 'mirror2'];
const telemetryStates = ['syncing', 'failed', 'unknown', 'completed'];

function telemetryKeys(value, keys) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}

function telemetryTimestamp(value) {
  return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 8640000000000);
}

function validTelemetryMetric(metric, identityKey) {
  const fields = [...telemetryFields.filter(field => field !== 'collected_at'), 'state', 'warning', 'size_bytes', identityKey];
  if (!telemetryKeys(metric, fields) || !telemetryStates.includes(metric.state) || typeof metric.warning !== 'boolean') return false;
  for (const field of fields) {
    const value = metric[field];
    if (field.endsWith('_at') || field === 'traffic_observed_since') {
      if (!telemetryTimestamp(value)) return false;
    } else if (field === 'size_bytes' || field.endsWith('_bytes_60s') || field === 'exit_code') {
      if (value !== null && (!Number.isSafeInteger(value) || value < 0)) return false;
    } else if (field.endsWith('_bps')) {
      if (value !== null && (typeof value !== 'number' || !Number.isFinite(value) || value < 0)) return false;
    }
  }
  if (metric.state === 'completed' && (metric.last_success_at === null
    || !(metric.exit_code === 0 || (metric.exit_code === 24 && metric.warning)))) return false;
  return true;
}

function validTelemetrySnapshot(snapshot, mirrorId) {
  if (!telemetryKeys(snapshot, ['schema_version', 'mirror_id', 'collected_at', 'interval_seconds', 'projects'])
    || snapshot.schema_version !== 1 || snapshot.mirror_id !== mirrorId || snapshot.interval_seconds !== 15
    || !telemetryTimestamp(snapshot.collected_at) || !telemetryKeys(snapshot.projects, Object.keys(telemetryGroups))) return false;
  return Object.entries(telemetryGroups).every(([id, repositoryIds]) => {
    const project = snapshot.projects[id];
    return validTelemetryMetric(project, 'repositories') && Array.isArray(project.repositories)
      && project.repositories.length === repositoryIds.length
      && new Set(project.repositories.map(repository => repository?.id)).size === repositoryIds.length
      && project.repositories.every(repository => validTelemetryMetric(repository, 'id') && repositoryIds.includes(repository.id))
      && project.state === telemetryStates.find(state => project.repositories.some(repository => repository.state === state));
  });
}

function validatedTelemetry(data) {
  if (!telemetryKeys(data, ['schema_version', 'generated_at', 'refresh_seconds', 'mirrors'])
    || data.schema_version !== 1 || data.refresh_seconds !== 15 || !telemetryTimestamp(data.generated_at)
    || !telemetryKeys(data.mirrors, telemetryMirrorIds)) throw new Error('Invalid telemetry');
  const mirrors = {};
  for (const id of telemetryMirrorIds) {
    const mirror = data.mirrors[id];
    const valid = telemetryKeys(mirror, ['available', 'snapshot']) && typeof mirror.available === 'boolean'
      && (mirror.snapshot === null || validTelemetrySnapshot(mirror.snapshot, id));
    mirrors[id] = valid ? mirror : { available: false, snapshot: null };
  }
  return { generated_at: data.generated_at, mirrors };
}

function telemetryText(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function telemetryTime(value) {
  const dictionary = translations[document.documentElement.lang] || translations.en;
  if (value === null || value === undefined) return document.createTextNode(dictionary.telemetryUnavailable);
  const date = new Date(value * 1000);
  const time = telemetryText('time', new Intl.DateTimeFormat(document.documentElement.lang === 'ko' ? 'ko-KR' : 'en-US', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  }).format(date));
  time.dateTime = date.toISOString();
  return time;
}

function renderTelemetryMetrics(target, metric, collectedAt) {
  const dictionary = translations[document.documentElement.lang] || translations.en;
  const content = document.createDocumentFragment();
  for (const field of telemetryFields) {
    let value = field === 'collected_at' ? collectedAt : metric?.[field];
    if (field.endsWith('_bps') && metric?.rates_observed_at == null) value = null;
    if (field.endsWith('_bytes_60s') && (collectedAt == null || metric?.traffic_observed_since == null
      || collectedAt - metric.traffic_observed_since < 60)) value = null;
    const wrapper = document.createElement('div');
    const description = document.createElement('dd');
    description.dataset.metric = field;
    if (field.endsWith('_at') || field === 'traffic_observed_since') {
      description.append(telemetryTime(value));
    } else if (field.endsWith('_bps')) {
      description.textContent = value == null ? dictionary.telemetryUnavailable : `${telemetryBytes(value)}/s`;
    } else if (field === 'exit_code') {
      description.textContent = value == null ? dictionary.telemetryUnavailable : String(value);
    } else {
      description.textContent = telemetryBytes(value);
    }
    wrapper.append(telemetryText('dt', dictionary[`telemetryMetric_${field}`]), description);
    content.append(wrapper);
  }
  target.replaceChildren(content);
}

function telemetryStateLabel(metric) {
  const dictionary = translations[document.documentElement.lang] || translations.en;
  return dictionary[`telemetryState_${metric?.state || 'unknown'}`]
    + (metric?.warning ? ` · ${dictionary.telemetryWarningShort}` : '');
}

function renderTelemetryRepositories(target, project, projectId, collectedAt, current) {
  const dictionary = translations[document.documentElement.lang] || translations.en;
  const content = document.createDocumentFragment();
  for (const id of telemetryGroups[projectId] || []) {
    const repository = project?.repositories?.find(repository => repository.id === id);
    const section = document.createElement('section');
    section.dataset.repository = id;
    section.append(telemetryText('h4', id));
    const state = current ? telemetryStateLabel(repository) : dictionary.telemetryLastObserved.replace('{state}', telemetryStateLabel(repository));
    section.append(telemetryText('p', `${state} · ${dictionary.telemetryDiskUsage} ${telemetryBytes(telemetryMeasuredSize(repository))}`));
    if (repository?.warning) section.append(telemetryText('p', dictionary.telemetryWarning, 'telemetry-warning'));
    const metrics = document.createElement('dl');
    metrics.className = 'telemetry-metrics';
    renderTelemetryMetrics(metrics, repository, collectedAt);
    section.append(metrics);
    content.append(section);
  }
  target.replaceChildren(content);
}

function telemetryMeasuredSize(metric) {
  return metric?.size_measured_at == null || metric.size_measured_at - Date.now() / 1000 > 30 ? null : metric.size_bytes;
}

function telemetryBytes(value) {
  if (value === null || value === undefined) return translations[document.documentElement.lang].telemetryUnavailable;
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${new Intl.NumberFormat(document.documentElement.lang, { maximumFractionDigits: 1 }).format(value)} ${units[unit]}`;
}

export function renderTelemetry(freshnessOnly = false) {
  const dictionary = translations[document.documentElement.lang] || translations.en;
  const now = Date.now() / 1000;
  const notice = document.querySelector('[data-telemetry-note]');
  if (notice) notice.textContent = dictionary[document.hidden ? 'telemetryPausedNote' : 'telemetryUpdatingNote'];
  for (const row of document.querySelectorAll('[data-project]')) {
    for (const cell of row.querySelectorAll('[data-mirror-cell]')) {
      const mirror = telemetryData?.mirrors[cell.dataset.mirrorCell];
      const snapshot = mirror?.snapshot;
      const project = snapshot?.projects[row.dataset.project];
      const freshness = document.hidden ? 'paused'
        : telemetryNeedsFresh || (!snapshot && ['idle', 'loading'].includes(telemetryFetchState)) ? 'loading'
        : !snapshot ? 'offline'
        : telemetryFetchState === 'failure' || !mirror.available
          || !telemetryRecent(snapshot.collected_at, now) || !telemetryRecent(telemetryData.generated_at, now) ? 'stale' : 'live';
      const changed = freshness !== cell.dataset.freshness;
      cell.dataset.freshness = freshness;
      cell.dataset.syncState = freshness === 'live' ? project.state : 'unknown';
      cell.querySelector('[data-sync-state]').textContent = freshness === 'live'
        ? telemetryStateLabel(project) : dictionary[`telemetryFreshness_${freshness}`];
      const age = snapshot?.collected_at == null ? null : Math.floor(now - snapshot.collected_at);
      const sampleAge = cell.querySelector('[data-sample-age]');
      sampleAge.hidden = age === null;
      sampleAge.textContent = age === null ? dictionary.telemetryNoSample
        : age < 0 ? dictionary.telemetryFuture : dictionary.telemetryAge.replace('{age}', new Intl.NumberFormat(document.documentElement.lang).format(age));
      const note = cell.querySelector('[data-freshness-note]');
      note.textContent = freshness === 'live' ? '' : dictionary[freshness === 'paused' ? 'telemetryPausedNote'
        : freshness === 'loading' ? 'telemetryLoadingNote' : freshness === 'offline' ? 'telemetryNoSnapshot'
          : telemetryFetchState === 'failure' || !mirror.available ? 'telemetryUpdateFailed' : 'telemetryNotCurrent'];
      note.hidden = freshness === 'live';
      const sizeNote = cell.querySelector('[data-size-note]');
      sizeNote.textContent = dictionary.telemetrySizeOld;
      sizeNote.hidden = project?.size_measured_at == null || now - project.size_measured_at <= 86400;
      if (freshnessOnly && !changed) continue;
      cell.querySelector('[data-disk-usage]').textContent = telemetryBytes(telemetryMeasuredSize(project));
      const metrics = cell.querySelector('[data-group-metrics]');
      renderTelemetryMetrics(metrics, project, snapshot?.collected_at);
      const stateMetric = document.createElement('div');
      const stateValue = telemetryText('dd', freshness === 'live' ? telemetryStateLabel(project)
        : dictionary.telemetryLastObserved.replace('{state}', telemetryStateLabel(project)));
      stateValue.dataset.metric = 'state';
      stateMetric.append(telemetryText('dt', dictionary.telemetryMetric_state), stateValue);
      metrics.prepend(stateMetric);
      renderTelemetryRepositories(cell.querySelector('[data-repositories]'), project, row.dataset.project, snapshot?.collected_at, freshness === 'live');
    }
  }
}

function telemetryRecent(timestamp, now) {
  return timestamp !== null && timestamp !== undefined && now - timestamp <= 60 && timestamp - now <= 30;
}

let telemetryStarted = false;
let telemetryRequest = null;
let telemetryPoll;
let telemetryAgeTimer;
let telemetryNeedsFresh = false;

async function fetchTelemetry() {
  if (document.hidden || telemetryRequest) return;
  const controller = new AbortController();
  telemetryRequest = controller;
  const timeout = setTimeout(() => controller.abort(), 8000);
  if (telemetryFetchState === 'idle') telemetryFetchState = 'loading';
  renderTelemetry(true);
  try {
    const response = await fetch('/status/mirrors.json', {
      cache: 'no-store', credentials: 'same-origin', mode: 'same-origin', redirect: 'error', signal: controller.signal,
    });
    if (!response.ok) throw new Error('Telemetry unavailable');
    const data = validatedTelemetry(await response.json());
    if (telemetryRequest !== controller) return;
    // Preserve an older validated snapshot if one source is absent this tick.
    for (const id of telemetryMirrorIds) {
      if (!data.mirrors[id].snapshot && telemetryData?.mirrors[id]?.snapshot) {
        data.mirrors[id] = { available: false, snapshot: telemetryData.mirrors[id].snapshot };
      }
    }
    telemetryData = data;
    telemetryFetchState = 'success';
  } catch {
    if (telemetryRequest !== controller) return;
    telemetryFetchState = 'failure';
    // Never expose server errors, raw HTML, logs or unvalidated metrics.
  } finally {
    clearTimeout(timeout);
    if (telemetryRequest === controller) {
      telemetryRequest = null;
      telemetryNeedsFresh = false;
      renderTelemetry();
    }
  }
}

function telemetryVisibility() {
  clearInterval(telemetryPoll);
  clearInterval(telemetryAgeTimer);
  if (document.hidden) {
    telemetryRequest?.abort();
    telemetryRequest = null;
    telemetryNeedsFresh = true;
    renderTelemetry(true);
    return;
  }
  fetchTelemetry();
  telemetryPoll = setInterval(fetchTelemetry, 15000);
  telemetryAgeTimer = setInterval(() => renderTelemetry(true), 1000);
}

export function startTelemetry() {
  if (telemetryStarted || !document.querySelector('[data-mirror-cell]')) return;
  telemetryStarted = true;
  document.addEventListener('visibilitychange', telemetryVisibility);
  telemetryVisibility();
}
