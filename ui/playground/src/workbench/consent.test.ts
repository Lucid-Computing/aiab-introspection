import { afterEach, describe, expect, it, vi } from "vitest";
import { base64, consentAccount, loadConsentKey, payloadBytes, signApproval } from "./consent";
import { api, request } from "./api";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const bytes = new Uint8Array([0, 255, 128, 10, 13, 32, 123, 125]);
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const generate = () => crypto.subtle.generateKey("Ed25519", false, ["sign", "verify"]) as Promise<CryptoKeyPair>;
const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

describe("browser-held Ed25519 consent", () => {
  it("scopes keys only to explicit authenticated IDs", () => {
    expect(consentAccount("alice")).toBe("alice");
    expect(consentAccount({ id: "alice", roles: ["ignored"] })).toBe("alice");
    for (const invalid of [null, {}, { email: "alice" }, { id: 4 }, "", " "])
      expect(() => consentAccount(invalid)).toThrow("authenticated account ID");
  });
  it("round-trips exact arbitrary bytes and rejects malformed payloads", () => {
    expect(payloadBytes(base64(bytes))).toEqual(bytes);
    for (const value of ["", "%%%", "YQ", "YR==", "YQ==\n"])
      expect(() => payloadBytes(value)).toThrow("Invalid signing payload");
  });
  it("fails closed when durable browser key storage is unavailable", async () => {
    vi.stubGlobal("indexedDB", undefined);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(api.approve("p", "digest", "alice", () => {})).rejects.toThrow("IndexedDB");
    expect(fetch).not.toHaveBeenCalled();
  });
  it("refuses unknown identity before requesting a key or enrollment", async () => {
    const keys = vi.fn();
    const transport = vi.fn();
    await expect(signApproval(transport, "p", "d", {}, () => {}, keys)).rejects.toThrow("authenticated account ID");
    expect(keys).not.toHaveBeenCalled();
    expect(transport).not.toHaveBeenCalled();
  });
  // Real WebCrypto is required here: unsupported Ed25519 must fail the test, not skip it.
  it("signs exact server bytes with nonextractable Ed25519, enrolls each time and sends only signed approval contracts", async () => {
    const key = await generate();
    expect(key.privateKey.extractable).toBe(false);
    await expect(crypto.subtle.exportKey("pkcs8", key.privateKey)).rejects.toThrow();
    const keys = vi.fn(async () => key);
    const approvalBytes = new TextEncoder().encode('{"signer":"alice","digest":"saved-digest"}\n');
    const calls: { path: string; body: Record<string, unknown> }[] = [];
    const fetch = vi.fn(async (path: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string);
      calls.push({ path, body });
      expect(init.credentials).toBe("same-origin");
      expect(body).not.toHaveProperty("actor");
      expect(body).not.toHaveProperty("roles");
      if (path.endsWith("/identities/challenge")) {
        expect(Object.keys(body)).toEqual(["public_key"]);
        expect(payloadBytes(body.public_key)).toHaveLength(32);
        return response({ challenge_id: "challenge", payload: base64(bytes) });
      }
      if (path.endsWith("/identities/enroll")) {
        expect(Object.keys(body).sort()).toEqual(["challenge_id", "signature"]);
        expect(body.challenge_id).toBe("challenge");
        expect(await crypto.subtle.verify("Ed25519", key.publicKey, payloadBytes(body.signature), bytes)).toBe(true);
        return response({ enrolled: true });
      }
      if (path.endsWith("/approval-payload")) {
        expect(body).toEqual({ digest: "saved-digest", nonce: expect.any(String), expires_at: null });
        expect(payloadBytes(body.nonce)).toHaveLength(32);
        return response({ payload: base64(approvalBytes) });
      }
      expect(path).toBe("/api/workbench/plans/my%2Fplan/approve");
      expect(body).toEqual({ ...calls[calls.length - 2].body, signature: expect.any(String) });
      expect(payloadBytes(body.signature)).toHaveLength(64);
      expect(await crypto.subtle.verify("Ed25519", key.publicKey, payloadBytes(body.signature), approvalBytes)).toBe(true);
      expect(await crypto.subtle.verify("Ed25519", key.publicKey, payloadBytes(body.signature), bytes)).toBe(false);
      return response({ approved: true });
    });
    vi.stubGlobal("fetch", fetch);
    const guard = vi.fn();
    for (let i = 0; i < 2; i++)
      await signApproval(post, "my/plan", "saved-digest", { id: "alice" }, guard, keys);
    expect(keys.mock.calls).toEqual([["alice"], ["alice"]]);
    expect(calls.map(c => c.path)).toEqual(Array(2).fill([
      "/api/workbench/identities/challenge", "/api/workbench/identities/enroll",
      "/api/workbench/plans/my%2Fplan/approval-payload", "/api/workbench/plans/my%2Fplan/approve",
    ]).flat());
    expect(calls[2].body.nonce).not.toBe(calls[6].body.nonce);
    expect(guard).toHaveBeenCalledTimes(10);
  });
  it("never rotates or approves after an enrollment mismatch", async () => {
    const key = await generate();
    const keys = vi.fn(async () => key);
    const transport = vi.fn(async (path: string) => {
      if (path.endsWith("/challenge")) return { challenge_id: "c", payload: base64(bytes) };
      throw new Error("identity key mismatch");
    });
    await expect(signApproval(transport as Parameters<typeof signApproval>[0], "p", "d", "alice", () => {}, keys))
      .rejects.toThrow("administrative recovery");
    expect(keys).toHaveBeenCalledTimes(1);
    expect(transport).toHaveBeenCalledTimes(2);
  });
  it.each(["enrollment unconfirmed", "approval payload denied"])("never submits approval after %s", async (failure) => {
    const key = await generate();
    const transport = vi.fn(async (path: string) => {
      if (path.endsWith("/challenge")) return { challenge_id: "c", payload: base64(bytes) };
      if (path.endsWith("/enroll")) return { enrolled: failure !== "enrollment unconfirmed" };
      throw new Error("payload denied");
    });
    await expect(signApproval(transport as Parameters<typeof signApproval>[0], "p", "d", "alice", () => {}, async () => key))
      .rejects.toThrow(failure === "enrollment unconfirmed" ? "administrative recovery" : "payload denied");
    expect(transport.mock.calls.some(([path]) => path.endsWith("/approve"))).toBe(false);
  });
  it("blocks stale edited agreements before any request and immediately before approval submission", async () => {
    const key = await generate();
    const transport = vi.fn(async (path: string) => path.endsWith("/challenge") ?
      { challenge_id: "c", payload: base64(bytes) } : path.endsWith("/enroll") ?
      { enrolled: true } : { payload: base64(bytes) });
    const keys = vi.fn(async () => key);
    const stale = () => { throw new Error("stale edited plan"); };
    await expect(signApproval(transport as Parameters<typeof signApproval>[0], "p", "d", "alice", stale, keys)).rejects.toThrow("stale");
    expect(transport).not.toHaveBeenCalled();
    expect(keys).not.toHaveBeenCalled();
    let checks = 0;
    await expect(signApproval(transport as Parameters<typeof signApproval>[0], "p", "d", "alice", () => {
      if (++checks === 5) stale();
    }, keys)).rejects.toThrow("stale");
    expect(transport.mock.calls.map(c => c[0])).toEqual(["/identities/challenge", "/identities/enroll", "/plans/p/approval-payload"]);
  });
  it("does not sign malformed payloads or accept extractable private keys", async () => {
    const key = await generate();
    const transport = vi.fn(async () => ({ challenge_id: "c", payload: "not base64" }));
    await expect(signApproval(transport as Parameters<typeof signApproval>[0], "p", "d", "alice", () => {}, async () => key)).rejects.toThrow("Invalid signing payload");
    expect(transport).toHaveBeenCalledTimes(1);
    transport.mockClear();
    const extractable = await crypto.subtle.generateKey("Ed25519", true, ["sign", "verify"]) as CryptoKeyPair;
    await expect(signApproval(transport as Parameters<typeof signApproval>[0], "p", "d", "alice", () => {}, async () => extractable)).rejects.toThrow("administrative recovery");
    expect(transport).not.toHaveBeenCalled();
  });
  it("does not generate a key without browser storage", async () => {
    vi.stubGlobal("indexedDB", undefined);
    const generateKey = vi.spyOn(crypto.subtle, "generateKey");
    await expect(loadConsentKey("alice")).rejects.toThrow("IndexedDB");
    expect(generateKey).not.toHaveBeenCalled();
  });
});
