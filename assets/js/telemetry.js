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

function telemetryStateLabel(metric) {
  const dictionary = translations[document.documentElement.lang] || translations.en;
  return dictionary[`telemetryState_${metric?.state || 'unknown'}`]
    + (metric?.warning ? ` · ${dictionary.telemetryWarningShort}` : '');
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

export function renderTelemetry() {
  const dictionary = translations[document.documentElement.lang] || translations.en;
  const now = Date.now() / 1000;
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
      cell.dataset.freshness = freshness;
      cell.dataset.syncState = freshness === 'live' ? project.state : 'unknown';
      cell.querySelector('[data-sync-state]').textContent = freshness === 'live'
        ? telemetryStateLabel(project) : dictionary[`telemetryFreshness_${freshness}`];
      const size = cell.querySelector('[data-disk-usage]');
      size.textContent = telemetryBytes(telemetryMeasuredSize(project));
      size.title = dictionary.telemetryStoredSize
        + (project?.size_measured_at != null && now - project.size_measured_at > 86400 ? ` · ${dictionary.telemetrySizeOld}` : '')
        + (project && freshness !== 'live' ? ` · ${dictionary.telemetryFreshness_stale}` : '');
      for (const direction of ['rx', 'tx']) {
        const rate = cell.querySelector(`[data-sync-${direction}]`);
        const value = freshness === 'live' && telemetryRecent(project?.rates_observed_at, now)
          ? project[`sync_${direction}_bps`] : null;
        rate.textContent = value == null ? dictionary.telemetryUnavailable : `${telemetryBytes(value)}/s`;
        rate.title = `${direction.toUpperCase()}: ${dictionary.telemetryObservedTraffic}`;
      }
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
  renderTelemetry();
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
    renderTelemetry();
    return;
  }
  fetchTelemetry();
  telemetryPoll = setInterval(fetchTelemetry, 15000);
  telemetryAgeTimer = setInterval(() => renderTelemetry(), 1000);
}

export function startTelemetry() {
  if (telemetryStarted || !document.querySelector('[data-mirror-cell]')) return;
  telemetryStarted = true;
  document.addEventListener('visibilitychange', telemetryVisibility);
  telemetryVisibility();
}
