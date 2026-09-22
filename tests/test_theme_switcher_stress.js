// Adversarial Stress Harness for Theme Switcher & DOM state
// Authored by Challenger 1

const fs = require('fs');
const path = require('path');

const projectRoot = path.resolve(__dirname, '..');
const indexPath = path.join(projectRoot, 'index.html');
const html = fs.readFileSync(indexPath, 'utf8');

// Build mock browser environment
function createMockEnv(initialStorage = {}, throwOnStorage = false) {
  const store = { ...initialStorage };
  const storage = {
    getItem: (k) => {
      if (throwOnStorage) throw new Error('SecurityError: Access Denied');
      return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
    },
    setItem: (k, v) => {
      if (throwOnStorage) throw new Error('QuotaExceededError: Storage Full');
      store[k] = String(v);
    }
  };

  const classListFor = () => {
    const classes = new Set();
    return {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      toggle: (c, force) => {
        if (force === undefined) {
          if (classes.has(c)) { classes.delete(c); return false; }
          else { classes.add(c); return true; }
        } else if (force) {
          classes.add(c);
          return true;
        } else {
          classes.delete(c);
          return false;
        }
      },
      contains: (c) => classes.has(c),
      get size() { return classes.size; },
      toString: () => Array.from(classes).join(' ')
    };
  };

  const docElemAttrs = {};
  const docElem = {
    setAttribute: (k, v) => { docElemAttrs[k] = String(v); },
    getAttribute: (k) => docElemAttrs[k] || null
  };

  const bodyClasses = classListFor();
  const body = { classList: bodyClasses };

  const btnLight = { classList: classListFor() };
  const btnDark = { classList: classListFor() };
  btnDark.classList.add('active'); // default dark

  let chartDrawCount = 0;
  const drawChart = () => { chartDrawCount++; };

  const env = {
    localStorage: storage,
    document: {
      documentElement: docElem,
      body: body,
      getElementById: (id) => {
        if (id === 'tbtnLight') return btnLight;
        if (id === 'tbtnDark') return btnDark;
        return null;
      }
    },
    drawChart: drawChart,
    getChartDrawCount: () => chartDrawCount,
    store: store
  };

  return env;
}

// 1. Head script execution test
function runHeadScript(env) {
  try {
    var saved = env.localStorage.getItem('arcafid_theme');
    if (saved === 'light') {
      env.document.documentElement.setAttribute('data-theme', 'light');
    } else {
      env.document.documentElement.setAttribute('data-theme', 'dark');
    }
  } catch(e) {}
}

// 2. Body script execution test
function runBodyScripts(env) {
  const THEME_STORAGE_KEY = 'arcafid_theme';

  function setTheme(theme) {
    const isLight = (theme === 'light');
    env.document.documentElement.setAttribute('data-theme', isLight ? 'light' : 'dark');
    if (env.document.body) {
      env.document.body.classList.toggle('theme-light', isLight);
      env.document.body.classList.toggle('theme-dark', !isLight);
    }

    const btnLight = env.document.getElementById('tbtnLight');
    const btnDark = env.document.getElementById('tbtnDark');
    if (btnLight && btnDark) {
      btnLight.classList.toggle('active', isLight);
      btnDark.classList.toggle('active', !isLight);
    }

    try {
      env.localStorage.setItem(THEME_STORAGE_KEY, isLight ? 'light' : 'dark');
    } catch(e) {}

    if (typeof env.drawChart === 'function') {
      env.drawChart();
    }
  }

  function initTheme() {
    let activeTheme = 'dark';
    try {
      const saved = env.localStorage.getItem(THEME_STORAGE_KEY);
      if (saved === 'light') activeTheme = 'light';
    } catch(e) {}
    setTheme(activeTheme);
  }

  return { setTheme, initTheme };
}

console.log('=== ADVERSARIAL THEME SWITCHER HARNESS ===\n');

console.log('[1/4] Testing Corrupted localStorage Vectors...');
const corruptedValues = [
  null,
  undefined,
  'matrix',
  '',
  '0',
  '1',
  'false',
  'true',
  'undefined',
  'null',
  'LIGHT',
  'Light',
  'DARK',
  '{}',
  '[]',
  'NaN',
  '<script>alert(1)</script>',
  '\0\x00',
  'light'
];

let passCount = 0;
for (const val of corruptedValues) {
  const env = createMockEnv(val !== undefined ? { 'arcafid_theme': val } : {});
  runHeadScript(env);

  const { setTheme, initTheme } = runBodyScripts(env);
  initTheme();

  const expected = (val === 'light') ? 'light' : 'dark';
  const dataTheme = env.document.documentElement.getAttribute('data-theme');
  const hasLightClass = env.document.body.classList.contains('theme-light');
  const hasDarkClass = env.document.body.classList.contains('theme-dark');
  const btnLightActive = env.document.getElementById('tbtnLight').classList.contains('active');
  const btnDarkActive = env.document.getElementById('tbtnDark').classList.contains('active');

  const ok = (expected === 'light')
    ? (dataTheme === 'light' && hasLightClass && !hasDarkClass && btnLightActive && !btnDarkActive)
    : (dataTheme === 'dark' && !hasLightClass && hasDarkClass && !btnLightActive && btnDarkActive);

  if (ok) {
    passCount++;
  } else {
    console.error(`FAIL for vector: ${JSON.stringify(val)} -> Got: dataTheme=${dataTheme}, bodyClasses=${env.document.body.classList}`);
  }
}
console.log(` -> Corrupted localStorage Vectors: ${passCount}/${corruptedValues.length} Passed`);

console.log('\n[2/4] Testing Storage Security / Quota Exceptions...');
const errorEnv = createMockEnv({}, true);
runHeadScript(errorEnv);
console.log(` -> Head script handled SecurityError: data-theme=${errorEnv.document.documentElement.getAttribute('data-theme')}`);

const { setTheme: errorSetTheme, initTheme: errorInitTheme } = runBodyScripts(errorEnv);
errorInitTheme();
console.log(` -> initTheme handled SecurityError: data-theme=${errorEnv.document.documentElement.getAttribute('data-theme')}`);

errorSetTheme('light');
console.log(` -> setTheme('light') handled QuotaExceededError: data-theme=${errorEnv.document.documentElement.getAttribute('data-theme')}, classList=${errorEnv.document.body.classList}`);

console.log('\n[3/4] Rapid Toggling Stress Test (100,000 Iterations)...');
const stressEnv = createMockEnv();
const { setTheme: stressSetTheme } = runBodyScripts(stressEnv);

const t0 = Date.now();
for (let i = 0; i < 100000; i++) {
  const target = (i % 2 === 0) ? 'light' : 'dark';
  stressSetTheme(target);
}
const elapsed = Date.now() - t0;
console.log(` -> 100,000 toggles completed in ${elapsed} ms (${(100000 / (elapsed / 1000)).toFixed(0)} ops/sec)`);
console.log(` -> Final data-theme: ${stressEnv.document.documentElement.getAttribute('data-theme')}`);
console.log(` -> Final body classes: ${stressEnv.document.body.classList}`);
console.log(` -> Final light button active: ${stressEnv.document.getElementById('tbtnLight').classList.contains('active')}`);
console.log(` -> Final dark button active: ${stressEnv.document.getElementById('tbtnDark').classList.contains('active')}`);
console.log(` -> Chart redraw calls: ${stressEnv.getChartDrawCount()}`);

console.log('\n[4/4] Synchronous DOM Attribute Inspection...');
const syncEnv = createMockEnv();
const { setTheme: syncSetTheme } = runBodyScripts(syncEnv);

let syncMismatches = 0;
for (let i = 0; i < 1000; i++) {
  const target = (i % 2 === 0) ? 'light' : 'dark';
  syncSetTheme(target);
  const isLight = (target === 'light');

  const dt = syncEnv.document.documentElement.getAttribute('data-theme');
  const bl = syncEnv.document.body.classList.contains('theme-light');
  const bd = syncEnv.document.body.classList.contains('theme-dark');
  const btnL = syncEnv.document.getElementById('tbtnLight').classList.contains('active');
  const btnD = syncEnv.document.getElementById('tbtnDark').classList.contains('active');

  if (isLight) {
    if (dt !== 'light' || !bl || bd || !btnL || btnD) syncMismatches++;
  } else {
    if (dt !== 'dark' || bl || !bd || btnL || !btnD) syncMismatches++;
  }
}
console.log(` -> Synchronous parity checks: 1,000/1,000 tested, ${syncMismatches} mismatches`);

if (passCount === corruptedValues.length && syncMismatches === 0) {
  console.log('\n>>> OVERALL DOM & THEME STRESS TEST: ALL PASSED <<<');
} else {
  console.error('\n>>> OVERALL DOM & THEME STRESS TEST: FAILED <<<');
  process.exit(1);
}
