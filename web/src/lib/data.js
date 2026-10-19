import { S, Secret } from './store.js';
import { b64d, b64e, deriveRaw, keyFromRaw, decryptBuffer } from './cryptoweb.js';
import { sanitizeId } from './util.js';

// Loads the files described in docs/DATA_CONTRACT.md, transparently handling encryption and offline cache.
class DataStore {
  constructor() {
    this.base = 'data/';
    this.meta = null;
    this.key = null;
    this.cache = new Map();
    this.pending = new Map();
    this.offline = false;
    this.errors = [];
  }

  version(name) {
    const m = this.meta || {};
    return (name === 'prices' && m.prices_at) || m.generated_at || '';
  }

  async fetchRaw(path, { binary = false, bust = '' } = {}) {
    const url = this.base + path + (bust ? '?v=' + encodeURIComponent(bust) : '');
    let res;
    try {
      res = await fetch(url, { cache: bust ? 'default' : 'no-store' });
    } catch (e) {
      this.offline = true;
      throw new Error('Network unavailable');
    }
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return binary ? res.arrayBuffer() : res.json();
  }

  async loadMeta() {
    this.meta = await this.fetchRaw('meta.json');
    return this.meta;
  }

  get encrypted() { return !!(this.meta && this.meta.enc); }

  // Try the key saved on this device. true = unlocked (or nothing to unlock).
  async tryStoredKey() {
    if (!this.encrypted) return true;
    const stored = Secret.get();
    if (!stored) return false;
    try {
      const key = await keyFromRaw(b64d(stored), this.meta.enc);
      if (key) { this.key = key; return true; }
    } catch { /* fall through */ }
    Secret.clear();
    return false;
  }

  async unlock(passphrase, remember = true) {
    const enc = this.meta.enc;
    const raw = await deriveRaw(passphrase, enc);
    const key = await keyFromRaw(raw, enc);
    if (!key) return false;
    this.key = key;
    if (remember) Secret.set(b64e(raw)); else Secret.clear();
    return true;
  }

  lock() { Secret.clear(); this.key = null; this.cache.clear(); }

  async read(path) {
    const ext = this.meta.ext || 'json';
    const file = path.endsWith('.' + ext) ? path : path + '.' + ext;
    const bust = this.version(path.split('/')[0].replace(/\.(json|enc)$/, ''));
    if (ext === 'enc') {
      const buf = await this.fetchRaw(file, { binary: true, bust });
      try {
        const text = await decryptBuffer(buf, this.key, this.meta.enc.gzip !== false);
        return JSON.parse(text);
      } catch (e) {
        const err = new Error('Could not decrypt ' + file + ' (wrong passphrase or data was rebuilt with another one)');
        err.code = 'DECRYPT';
        throw err;
      }
    }
    return this.fetchRaw(file, { bust });
  }

  get(name) {
    if (this.cache.has(name)) return Promise.resolve(this.cache.get(name));
    if (this.pending.has(name)) return this.pending.get(name);
    const file = this.meta.files && this.meta.files[name];
    if (!file) return Promise.reject(new Error('Unknown data file: ' + name));
    const p = this.read(file.replace(/\.(json|enc)$/, '')).then(v => {
      this.cache.set(name, v);
      this.pending.delete(name);
      return v;
    }).catch(e => { this.pending.delete(name); throw e; });
    this.pending.set(name, p);
    return p;
  }

  // Every top-level data file the app can show. The browser keeps whatever the app fetches through its service worker,
  // so fetching them all once means the app opens offline later. Per-instrument files stay lazy: there are hundreds.
  expectedFiles() {
    const ext = (this.meta && this.meta.ext) || 'json';
    const files = (this.meta && this.meta.files) || {};
    return Object.values(files).map(f => f.replace(/\.(json|enc)$/, '') + '.' + ext);
  }

  async warm() {
    if (!this.meta) return;
    // meta.json was fetched before the worker took control on a first visit; fetch it again so that copy is kept too
    try { await this.fetchRaw('meta.json'); } catch { /* offline */ }
    for (const file of this.expectedFiles()) {
      const name = file.replace(/\.(json|enc)$/, '');
      // the same URL the screens ask for (file + ?v=version), read as raw bytes so nothing is parsed or decrypted
      try { await this.fetchRaw(file, { binary: true, bust: this.version(name) }); } catch { /* offline or missing: next visit tries again */ }
    }
  }

  has(name) { return this.cache.has(name); }
  peek(name) { return this.cache.get(name); }

  detail(id) {
    const [market, ...rest] = id.split(':');
    const sym = rest.join(':');
    const key = 'detail:' + id;
    if (this.cache.has(key)) return Promise.resolve(this.cache.get(key));
    if (this.pending.has(key)) return this.pending.get(key);
    const path = (this.meta.detail_path || 'detail/{market}/{symbol}.json')
      .replace('{market}', market.toLowerCase()).replace('{symbol}', sanitizeId(sym))
      .replace(/\.(json|enc)$/, '');
    const p = this.read(path).then(v => { this.cache.set(key, v); this.pending.delete(key); return v; })
      .catch(e => { this.pending.delete(key); throw e; });
    this.pending.set(key, p);
    return p;
  }

  async refreshPrices() {
    this.cache.delete('prices');
    try { await this.loadMeta(); } catch { /* keep old meta */ }
    return this.get('prices');
  }

  // --- derived lookups (built lazily from instruments) ---
  async index() {
    if (this._index) return this._index;
    const ins = await this.get('instruments');
    const byId = new Map();
    for (const r of ins.rows) byId.set(r.id, r);
    this._index = byId;
    return byId;
  }
}

export const D = new DataStore();
