// Client side of docs/DATA_CONTRACT.md "Encryption": PBKDF2-SHA256 -> AES-256-GCM, gzip inside.
const te = new TextEncoder();
const td = new TextDecoder();

export const b64d = str => Uint8Array.from(atob(str), c => c.charCodeAt(0));
export const b64e = u8 => { let s = ''; for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]); return btoa(s); };

export async function deriveRaw(passphrase, enc) {
  const base = await crypto.subtle.importKey('raw', te.encode(passphrase), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', hash: 'SHA-256', salt: b64d(enc.salt), iterations: Number(enc.iter) }, base, 256);
  return new Uint8Array(bits);
}

export const importKey = raw => crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']);

async function gunzip(buf) {
  const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).arrayBuffer();
}

export async function decryptBuffer(buffer, key, gzip) {
  const u = new Uint8Array(buffer);
  if (u.length < 28) throw new Error('ciphertext too short');
  const iv = u.slice(0, 12);
  const body = u.slice(12);
  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, body);
  return gzip ? td.decode(await gunzip(plain)) : td.decode(plain);
}

// Returns a CryptoKey when the passphrase (or stored raw key) opens the `check` blob, otherwise null.
export async function keyFromRaw(raw, enc) {
  try {
    const key = await importKey(raw);
    const text = await decryptBuffer(b64d(enc.check), key, false);
    return text === 'acumen-ok' ? key : null;
  } catch { return null; }
}
