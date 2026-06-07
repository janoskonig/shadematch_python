// env_capture.js — passive device/browser/environment snapshot for QC.
// Cheap, synchronous, no user prompts. UA-CH high-entropy values are merged
// asynchronously when available and cached so subsequent calls are instant.

const COLOR_GAMUT_LADDER = ['rec2020', 'p3', 'srgb'];

let cachedHighEntropyUa = null;
let highEntropyPromise = null;

function safeMatchMedia(query) {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false;
  } catch {
    return false;
  }
}

function detectColorGamut() {
  for (const bucket of COLOR_GAMUT_LADDER) {
    if (safeMatchMedia(`(color-gamut: ${bucket})`)) return bucket;
  }
  return null;
}

function detectColorScheme() {
  if (safeMatchMedia('(prefers-color-scheme: dark)')) return 'dark';
  if (safeMatchMedia('(prefers-color-scheme: light)')) return 'light';
  return 'no-preference';
}

function isFullscreen() {
  if (typeof document === 'undefined') return false;
  return Boolean(
    document.fullscreenElement
    || document.webkitFullscreenElement
    || document.msFullscreenElement,
  );
}

function safeNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function getConnectionInfo() {
  if (typeof navigator === 'undefined') return null;
  const c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (!c) return null;
  return {
    effective_type: typeof c.effectiveType === 'string' ? c.effectiveType : null,
    downlink_mbps: safeNumber(c.downlink),
    rtt_ms: safeNumber(c.rtt),
    save_data: typeof c.saveData === 'boolean' ? c.saveData : null,
  };
}

function getOrientationType() {
  try {
    const o = (typeof screen !== 'undefined' && screen.orientation) ? screen.orientation : null;
    return o && typeof o.type === 'string' ? o.type : null;
  } catch {
    return null;
  }
}

function getTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

function getMaxTouchPoints() {
  try {
    return typeof navigator !== 'undefined' && Number.isFinite(navigator.maxTouchPoints)
      ? Number(navigator.maxTouchPoints)
      : 0;
  } catch {
    return 0;
  }
}

// device_kind: 'mobile' | 'tablet' | 'desktop' | 'unknown'.
// Strategy:
//   1. Use navigator.userAgentData.mobile when available (Chromium-based).
//      It only distinguishes mobile vs non-mobile, so combine with UA-string
//      heuristics to surface tablets specifically.
//   2. Fall back to UA-string parsing for engines without UA-CH (Safari,
//      older Firefox). iPadOS desktop-mode UA looks like macOS but reports
//      multi-touch; treat those as tablets.
function detectDeviceKind() {
  if (typeof navigator === 'undefined') return 'unknown';
  const ua = navigator.userAgent || '';
  const uaData = navigator.userAgentData || null;
  const touchPoints = getMaxTouchPoints();
  const looksLikeIPad = /iPad/i.test(ua);
  const looksLikeIPhone = /iPhone|iPod/i.test(ua);
  const looksLikeAndroidTablet = /Android/i.test(ua) && !/Mobile/i.test(ua);
  const looksLikeAndroidPhone = /Android/i.test(ua) && /Mobile/i.test(ua);
  const isMacWithTouch = /Macintosh/i.test(ua) && touchPoints > 1;

  if (uaData && typeof uaData.mobile === 'boolean') {
    if (uaData.mobile) {
      if (looksLikeIPad || looksLikeAndroidTablet) return 'tablet';
      return 'mobile';
    }
    if (looksLikeIPad || isMacWithTouch || looksLikeAndroidTablet) return 'tablet';
    return 'desktop';
  }

  if (!ua) return 'unknown';
  if (looksLikeIPad || looksLikeAndroidTablet || isMacWithTouch) return 'tablet';
  if (looksLikeIPhone || looksLikeAndroidPhone || /Mobile/i.test(ua)) return 'mobile';
  return 'desktop';
}

async function refreshHighEntropyUa() {
  if (typeof navigator === 'undefined') return null;
  const uaData = navigator.userAgentData;
  if (!uaData || typeof uaData.getHighEntropyValues !== 'function') return null;
  if (highEntropyPromise) return highEntropyPromise;
  highEntropyPromise = uaData
    .getHighEntropyValues(['platform', 'platformVersion', 'architecture', 'model', 'bitness'])
    .then((values) => {
      cachedHighEntropyUa = {
        brands: Array.isArray(uaData.brands) ? uaData.brands.slice() : [],
        mobile: typeof uaData.mobile === 'boolean' ? uaData.mobile : null,
        platform: values.platform || null,
        platform_version: values.platformVersion || null,
        architecture: values.architecture || null,
        model: values.model || null,
        bitness: values.bitness || null,
      };
      return cachedHighEntropyUa;
    })
    .catch(() => null);
  return highEntropyPromise;
}

if (typeof window !== 'undefined') {
  // Kick off UA-CH resolution as soon as the module is loaded so the first
  // captureEnv() call after a tick benefits from the richer data.
  refreshHighEntropyUa();
}

export function captureEnv() {
  const now = new Date();
  const screenInfo = (typeof screen !== 'undefined' && screen) ? {
    width: safeNumber(screen.width),
    height: safeNumber(screen.height),
    avail_width: safeNumber(screen.availWidth),
    avail_height: safeNumber(screen.availHeight),
    color_depth: safeNumber(screen.colorDepth),
    pixel_depth: safeNumber(screen.pixelDepth),
    orientation: getOrientationType(),
  } : null;

  const viewport = typeof window !== 'undefined' ? {
    inner_width: safeNumber(window.innerWidth),
    inner_height: safeNumber(window.innerHeight),
    dpr: safeNumber(window.devicePixelRatio),
  } : null;

  const env = {
    schema_version: 2,
    captured_at: now.toISOString(),
    local_iso: now.toISOString(),
    hour_of_day_local: now.getHours(),
    tz: getTimezone(),
    tz_offset_min: now.getTimezoneOffset(),
    locale: typeof navigator !== 'undefined' ? (navigator.language || null) : null,
    ua: typeof navigator !== 'undefined' ? (navigator.userAgent || null) : null,
    ua_ch: cachedHighEntropyUa,
    device_kind: detectDeviceKind(),
    max_touch_points: getMaxTouchPoints(),
    screen: screenInfo,
    viewport,
    color_gamut: detectColorGamut(),
    prefers_color_scheme: detectColorScheme(),
    prefers_reduced_motion: safeMatchMedia('(prefers-reduced-motion: reduce)'),
    prefers_contrast_more: safeMatchMedia('(prefers-contrast: more)'),
    inverted_colors: safeMatchMedia('(inverted-colors: inverted)'),
    forced_colors: safeMatchMedia('(forced-colors: active)'),
    fullscreen: isFullscreen(),
    visibility_state: typeof document !== 'undefined' ? (document.visibilityState || null) : null,
    device_memory_gb: typeof navigator !== 'undefined' ? safeNumber(navigator.deviceMemory) : null,
    hardware_concurrency: typeof navigator !== 'undefined' ? safeNumber(navigator.hardwareConcurrency) : null,
    connection: getConnectionInfo(),
  };

  if (!env.ua_ch) {
    // Refresh in the background; first call returns without UA-CH, subsequent calls have it.
    refreshHighEntropyUa();
  }

  return env;
}

export default captureEnv;
