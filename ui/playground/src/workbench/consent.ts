/** Browser-held consent keys. This does not provide protected/confidential execution. */
export function consentAccount(user: unknown): string {
  const value = typeof user === "string" ? user :
    user && typeof user === "object" && "id" in user ? user.id : undefined;
  if (typeof value !== "string" || !value.trim())
    throw new Error("Approval requires an authenticated account ID. Refresh your session.");
  return value;
}
export function base64(bytes: ArrayBuffer | Uint8Array): string {
  const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let raw = "";
  for (let offset = 0; offset < data.length; offset += 32768)
    raw += String.fromCharCode(...data.subarray(offset, offset + 32768));
  return btoa(raw);
}
export function payloadBytes(value: string): Uint8Array<ArrayBuffer> {
  if (typeof value !== "string" || !value || value.length % 4 !== 0 ||
      !/^[A-Za-z0-9+/]*={0,2}$/.test(value))
    throw new Error("Invalid signing payload from service.");
  const bytes = Uint8Array.from(atob(value), c => c.charCodeAt(0));
  if (base64(bytes) !== value) throw new Error("Invalid signing payload from service.");
  return bytes;
}
export type KeyStore = (account: string) => Promise<CryptoKeyPair>;
const recovery = "Browser-held consent key unavailable or rejected. Key loss or account/key mismatch requires administrative recovery; no replacement identity was enrolled.";

/** IndexedDB structured-clones CryptoKeys; the private key is never exported. */
export const loadConsentKey: KeyStore = async (account) => {
  if (!globalThis.crypto?.subtle || !globalThis.indexedDB)
    throw new Error("Approval requires WebCrypto Ed25519 and IndexedDB in a secure browser context.");
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const open = indexedDB.open("aiab-workbench-consent-v1", 1);
    open.onupgradeneeded = () => open.result.createObjectStore("keys");
    open.onsuccess = () => resolve(open.result);
    open.onerror = () => reject(new Error(recovery));
    open.onblocked = () => reject(new Error("Consent key storage is blocked. Close other workbench tabs and retry."));
  });
  try {
    const read = () => new Promise<CryptoKeyPair | undefined>((resolve, reject) => {
      const tx = db.transaction("keys", "readonly");
      const get = tx.objectStore("keys").get(account);
      tx.oncomplete = () => resolve(get.result);
      tx.onabort = tx.onerror = () => reject(new Error(recovery));
    });
    const existing = await read();
    if (existing) return validateKey(existing);
    const candidate = await crypto.subtle.generateKey("Ed25519", false, ["sign", "verify"]) as CryptoKeyPair;
    // Serialize competing tabs: use an existing winner rather than overwrite it.
    const selected = await new Promise<CryptoKeyPair>((resolve, reject) => {
      const tx = db.transaction("keys", "readwrite");
      const store = tx.objectStore("keys");
      const get = store.get(account);
      let key = candidate;
      get.onsuccess = () => {
        if (get.result) key = get.result;
        else store.add(candidate, account);
      };
      tx.oncomplete = () => resolve(key);
      tx.onabort = tx.onerror = () => reject(new Error(recovery));
    });
    return validateKey(selected);
  } finally { db.close(); }
};
function validateKey(key: CryptoKeyPair): CryptoKeyPair {
  if (!key?.privateKey || key.privateKey.extractable ||
      key.privateKey.algorithm.name !== "Ed25519" || key.privateKey.type !== "private" ||
      !key.privateKey.usages.includes("sign") || key.publicKey?.algorithm.name !== "Ed25519" ||
      key.publicKey.type !== "public" || !key.publicKey.extractable)
    throw new Error(recovery);
  return key;
}

type Post = <T>(path: string, body: unknown) => Promise<T>;
/** Account is only a local storage scope; HTTP authority always comes from the session. */
export async function signApproval(
  post: Post, planId: string, digest: string, user: unknown,
  assertCurrent: () => void | Promise<void>, keys: KeyStore = loadConsentKey,
): Promise<object> {
  const account = consentAccount(user);
  if (!digest) throw new Error("Approval requires the exact saved digest.");
  await assertCurrent();
  const key = validateKey(await keys(account));
  const sign = async (payload: string) => base64(await crypto.subtle.sign("Ed25519", key.privateKey, payloadBytes(payload)));
  const public_key = base64(await crypto.subtle.exportKey("raw", key.publicKey));
  await assertCurrent();
  const challenge = await post<{ challenge_id: string; payload: string }>("/identities/challenge", { public_key });
  if (typeof challenge.challenge_id !== "string" || !challenge.challenge_id)
    throw new Error("Invalid enrollment challenge from service.");
  const signature = await sign(challenge.payload);
  await assertCurrent();
  try {
    const enrolled = await post<{ enrolled: boolean }>("/identities/enroll", { challenge_id: challenge.challenge_id, signature });
    if (enrolled.enrolled !== true) throw new Error("Enrollment was not confirmed.");
  } catch { throw new Error(recovery); }
  const body = { digest, nonce: base64(crypto.getRandomValues(new Uint8Array(32))), expires_at: null };
  const path = `/plans/${encodeURIComponent(planId)}`;
  await assertCurrent();
  const payload = await post<{ payload: string }>(`${path}/approval-payload`, body);
  const approvalSignature = await sign(payload.payload);
  await assertCurrent();
  return post<object>(`${path}/approve`, { ...body, signature: approvalSignature });
}
