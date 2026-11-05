import { payloadBytes } from "./consent";
import { isObject } from "./model";

/** RFC8785 for parsed JSON: JS number serialization and UTF-16 property order. */
export function canonical(value: unknown): string {
  if (typeof value === "string") {
    // RFC8785 rejects lone surrogates rather than UTF-8 replacement characters.
    if (
      /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(
        value,
      )
    )
      throw new Error("Invalid Unicode in receipt.");
    return JSON.stringify(value);
  }
  if (value === null || typeof value === "boolean")
    return JSON.stringify(value);
  if (typeof value === "number" && Number.isFinite(value))
    return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isObject(value))
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${canonical(k)}:${canonical(value[k])}`)
      .join(",")}}`;
  throw new Error("Receipt contains a non-JSON value.");
}
export async function verifyPackage(
  value: unknown,
  pinnedKey: string,
  expected: Record<string, unknown> = {},
) {
  if (!pinnedKey.trim())
    throw new Error("Supply an independently pinned Ed25519 public key.");
  if (
    !isObject(value) ||
    !isObject(value.receipt) ||
    !isObject(value.receipt.payload) ||
    !isObject(value.projection)
  )
    throw new Error("Invalid receipt package.");
  const { payload, signature } = value.receipt;
  if (
    payload.version !== "aiab-governance/receipt/v1" ||
    typeof signature !== "string"
  )
    throw new Error("Unsupported receipt.");
  const key = await crypto.subtle.importKey(
    "raw",
    payloadBytes(pinnedKey.trim()),
    "Ed25519",
    false,
    ["verify"],
  );
  const encode = (s: string) => new TextEncoder().encode(s);
  if (
    !(await crypto.subtle.verify(
      "Ed25519",
      key,
      payloadBytes(signature),
      encode(canonical(payload)),
    ))
  )
    throw new Error("Receipt signature verification failed.");
  const hash = await crypto.subtle.digest(
    "SHA-256",
    encode(`aiab-governance/projection/v1\0${canonical(value.projection)}`),
  );
  const digest = Array.from(new Uint8Array(hash), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
  if (payload.projection_digest !== digest)
    throw new Error("Projection commitment mismatch.");
  for (const [field, val] of Object.entries(expected))
    if (canonical(payload[field] ?? null) !== canonical(val))
      throw new Error(`Receipt binding mismatch: ${field}.`);
  return true;
}
