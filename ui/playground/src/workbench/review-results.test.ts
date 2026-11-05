import { createElement } from "react";
import {
  act,
  create,
  type ReactTestRenderer,
  type ReactTestInstance,
} from "react-test-renderer";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import Reviews from "./Reviews";
import Results from "./Results";
import WorkbenchApp from "./WorkbenchApp";
import { ChallengeHistory, ReceiptVerifier } from "./ResultTools";
import { base64 } from "./consent";
import { canonical, verifyPackage } from "./receipt";
import { blankPlan, deterministicRehearsal, semanticDiff } from "./model";
import { api } from "./api";

const mounted: ReactTestRenderer[] = [];
async function mount(element: Parameters<typeof create>[0]) {
  let tree!: ReactTestRenderer;
  await act(async () => {
    tree = create(element);
  });
  mounted.push(tree);
  return tree;
}
const text = (node: ReactTestInstance): string =>
  node.children.map((c) => (typeof c === "string" ? c : text(c))).join("");
const button = (tree: ReactTestRenderer, label: string) =>
  tree.root.findAllByType("button").find((b) => text(b) === label)!;
const field = (tree: ReactTestRenderer, label: string) =>
  tree.root
    .findAllByType("label")
    .find((l) => l.findAllByType("span").some((s) => text(s) === label))!
    .findAll(
      (n) => n.type === "input" || n.type === "textarea" || n.type === "select",
    )[0];
async function change(tree: ReactTestRenderer, label: string, value: string) {
  await act(async () => {
    field(tree, label).props.onChange({ target: { value } });
  });
}
async function click(tree: ReactTestRenderer, label: string) {
  await act(async () => {
    await button(tree, label).props.onClick();
  });
}
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
afterEach(() => {
  act(() => mounted.splice(0).forEach((t) => t.unmount()));
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const plan = () => {
  const p = blankPlan();
  p.id = "shared/plan";
  p.steps = [
    {
      id: "measure",
      title: "Measure",
      kind: "rules",
      inputs: [],
      config: { operator: "domain_audit/v1" },
    },
    {
      id: "investigate",
      title: "Investigate",
      kind: "agent",
      inputs: [],
      config: { instructions: "Check alternatives" },
    },
  ];
  return p;
};

describe("durable exact-version review UI", () => {
  it("stores exact pointer/digest with explicit sharing, then reloads durable notes after remount", async () => {
    const p = plan();
    const stored: Record<string, unknown>[] = [];
    const fetcher = vi.fn(async (_url, options) => {
      if (options?.method === "POST") {
        const body = JSON.parse(options.body);
        const row = {
          ...body,
          id: "note1",
          actor: "reviewer",
          plan_digest: body.digest,
          clause_digest: "clause-hash",
        };
        stored.push(row);
        return response(row);
      }
      return response({ reviews: stored });
    });
    vi.stubGlobal("fetch", fetcher);
    const props = {
      plan: p,
      saved: { plan: p, digest: "revision-1" },
      dirty: false,
      online: true,
      onUnsaved: vi.fn(),
    };
    const tree = await mount(createElement(Reviews, props));
    await change(tree, "Exact clause", "/steps/1");
    await change(
      tree,
      "Question or comment",
      "What counterevidence was checked?",
    );
    await change(
      tree,
      "Share with authorized recipient IDs (optional)",
      "reviewer, owner",
    );
    expect(text(tree.root)).toContain("Unsaved note");
    await click(tree, "Store exact-version note");
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/workbench/plans/shared%2Fplan/reviews",
    );
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
      digest: "revision-1",
      clause: "/steps/1",
      note: "What counterevidence was checked?",
      recipients: ["reviewer", "owner"],
    });
    expect(field(tree, "Question or comment").props.value).toBe("");
    const reopened = await mount(createElement(Reviews, props));
    await click(reopened, "Load revision notes");
    expect(text(reopened.root)).toContain("What counterevidence was checked?");
    await change(
      reopened,
      "Historical revision digest (optional)",
      "older/digest",
    );
    await click(reopened, "Load revision notes");
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("?digest=older%2Fdigest");
    expect(button(reopened, "Store exact-version note").props.disabled).toBe(
      true,
    );
  });
  it("keeps a rejected note unsaved and never invents resolution or consent", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({ error: { message: "Version changed", code: "stale" } }, 409),
      ),
    );
    const p = plan();
    const tree = await mount(
      createElement(Reviews, {
        plan: p,
        saved: { plan: p, digest: "old" },
        dirty: false,
        online: true,
        onUnsaved: vi.fn(),
      }),
    );
    await change(tree, "Question or comment", "Keep my draft");
    await click(tree, "Store exact-version note");
    expect(field(tree, "Question or comment").props.value).toBe(
      "Keep my draft",
    );
    expect(text(tree.root)).toContain("Version changed");
    expect(text(tree.root)).toContain("Unsaved note");
    expect(text(tree.root)).not.toContain("Resolve");
  });
});

describe("review revision changes", () => {
  it("retains an unsaved note and requires explicit retargeting when the saved revision changes", async () => {
    const fetcher = vi.fn(async (_url: string, options?: RequestInit) =>
      response({
        ...JSON.parse(String(options?.body)),
        id: "stored",
        actor: "reviewer",
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    const p = plan();
    const props = {
      plan: p,
      saved: { plan: p, digest: "revision-1" },
      dirty: false,
      online: true,
      onUnsaved: vi.fn(),
    };
    const tree = await mount(createElement(Reviews, props));
    await change(tree, "Question or comment", "Check this clause");
    await act(async () => {
      tree.update(
        createElement(Reviews, {
          ...props,
          saved: { plan: p, digest: "revision-2" },
        }),
      );
    });
    expect(text(tree.root)).toContain(
      "This unsaved note began on shared/plan:revision-1",
    );
    expect(button(tree, "Store exact-version note").props.disabled).toBe(true);
    expect(field(tree, "Question or comment").props.value).toBe(
      "Check this clause",
    );
    expect(fetcher).not.toHaveBeenCalled();
    await click(tree, "Use displayed saved revision for this unsaved note");
    await click(tree, "Store exact-version note");
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).digest).toBe(
      "revision-2",
    );
  });
});

describe("shared agreements and deliberate rehearsal selection", () => {
  it("retains the default agent, creates a separate unsigned deterministic method, and rejects dangling dependencies", () => {
    const p = plan();
    const next = deterministicRehearsal(p);
    expect(p.steps.map((s) => s.kind)).toEqual(["rules", "agent"]);
    expect(next.steps.map((s) => s.kind)).toEqual(["rules"]);
    expect(next.agent).toEqual(p.agent);
    expect(next.id).not.toBe(p.id);
    expect(semanticDiff(p, next).map((d) => d.path)).toContain("steps");
    p.steps[0].inputs = ["investigate"];
    expect(() => deterministicRehearsal(p)).toThrow(
      "depends on the investigating agent",
    );
  });
  it("discovers an authorized shared agreement and displays boolean readiness without private signer metadata", async () => {
    vi.stubGlobal("window", new EventTarget());
    const p = plan();
    const fetcher = vi.fn(async (url: string) =>
      url.endsWith("bootstrap")
        ? response({
            user: { id: "reviewer" },
            capabilities: {},
            audits: [],
            datasets: [],
            plans: [{ plan: p, digest: "revision" }],
          })
        : url.endsWith("readiness")
          ? response({ ready: false })
          : response({
              plan: p,
              digest: "revision",
              approvals: [{ actor: "PRIVATE_REFUSAL", state: "refused" }],
            }),
    );
    vi.stubGlobal("fetch", fetcher);
    const tree = await mount(createElement(WorkbenchApp));
    const saved = tree.root
      .findAllByType("button")
      .find((b) => b.props.className === "wb-saved")!;
    await act(async () => {
      saved.props.onClick();
    });
    await click(tree, "Check shared readiness");
    expect(text(tree.root)).toContain("Individual decisions remain private");
    expect(text(tree.root)).not.toContain("PRIVATE_REFUSAL");
    await click(tree, "Create deterministic rehearsal variant");
    expect(text(tree.root)).toContain(
      "Unsigned deterministic rehearsal variant",
    );
    expect(text(tree.root)).toContain("No approval transfers");
    expect(
      fetcher.mock.calls.every((call) => !/runs|approve/.test(String(call[0]))),
    ).toBe(true);
  });
});

async function signedPackage() {
  const keys = (await crypto.subtle.generateKey("Ed25519", true, [
    "sign",
    "verify",
  ])) as CryptoKeyPair;
  const projection = {
    release_state: "released",
    counts: { state: "released", values: { supported: 2 } },
    status: "completed",
  };
  const encoded = new TextEncoder().encode(
    `aiab-governance/projection/v1\0${canonical(projection)}`,
  );
  const hash = await crypto.subtle.digest("SHA-256", encoded);
  const payload = {
    version: "aiab-governance/receipt/v1",
    run_id: "governance-run",
    recipient: "reviewer",
    plan_digest: "revision",
    binding_digest: "binding",
    projection_digest: Array.from(new Uint8Array(hash), (b) =>
      b.toString(16).padStart(2, "0"),
    ).join(""),
  };
  const signature = base64(
    await crypto.subtle.sign(
      "Ed25519",
      keys.privateKey,
      new TextEncoder().encode(canonical(payload)),
    ),
  );
  return {
    package: { projection, receipt: { payload, signature } },
    publicKey: base64(await crypto.subtle.exportKey("raw", keys.publicKey)),
  };
}

describe("independent pinned-key receipt verification with real WebCrypto", () => {
  it("uses canonical UTF-16 property order and ECMAScript numbers, rejecting invalid Unicode", () => {
    expect(canonical({ "\ufb33": 4, "😀": 3, "€": 2, a: 1 })).toBe(
      '{"a":1,"€":2,"😀":3,"דּ":4}',
    );
    expect(canonical([1e30, 0.0000001, -0, 4.5])).toBe("[1e+30,1e-7,0,4.5]");
    expect(() => canonical({ invalid: "\ud800" })).toThrow("Invalid Unicode");
    expect(() => canonical({ "\udfff": 1 })).toThrow("Invalid Unicode");
  });
  it("verifies exact signed bytes and bindings; rejects tampering, foreign keys and UI run ID substitution", async () => {
    const fixture = await signedPackage();
    await expect(
      verifyPackage(fixture.package, fixture.publicKey, {
        run_id: "governance-run",
        recipient: "reviewer",
      }),
    ).resolves.toBe(true);
    await expect(
      verifyPackage(fixture.package, fixture.publicKey, { run_id: "ui-run" }),
    ).rejects.toThrow("binding mismatch");
    const altered = structuredClone(fixture.package);
    altered.projection.counts.values.supported = 0;
    await expect(verifyPackage(altered, fixture.publicKey)).rejects.toThrow(
      "commitment mismatch",
    );
    const other = await signedPackage();
    await expect(
      verifyPackage(fixture.package, other.publicKey),
    ).rejects.toThrow("signature verification failed");
    await expect(verifyPackage(fixture.package, "")).rejects.toThrow(
      "independently pinned",
    );
  });
  it("verifies an exported package through the actual UI without any private lookup or fetched self key", async () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const fixture = await signedPackage();
    const tree = await mount(createElement(ReceiptVerifier, { run: null }));
    await change(
      tree,
      "Independently pinned public key (base64 raw 32 bytes)",
      fixture.publicKey,
    );
    await change(
      tree,
      "Exported package JSON (projection and receipt)",
      JSON.stringify(fixture.package),
    );
    await change(
      tree,
      "Expected receipt bindings JSON (optional)",
      JSON.stringify({ recipient: "reviewer", run_id: "governance-run" }),
    );
    await click(tree, "Verify with pinned key");
    expect(text(tree.root)).toContain("Verified signature, projection bytes");
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("recipient result and challenge contracts", () => {
  it("handles actual suppressed findings objects without crashing or reconstructing counts", () => {
    const html = renderToStaticMarkup(
      createElement(Results, {
        run: {
          run_id: "ui",
          mode: "audit",
          status: "completed",
          result: {
            release_state: "suppressed",
            findings: { state: "suppressed", value: null },
            counts: { state: "suppressed", value: null },
          },
        } as never,
        onCancel: vi.fn(),
        onRefresh: vi.fn(),
        onChallenge: vi.fn(),
        busy: false,
      }),
    );
    expect(html).toContain("Counts withheld, never zero");
    expect(html).not.toContain("Challenge this finding");
    expect(html).toContain("no signed recipient package");
  });
  it("renders supplied timeline, cohort and lineage as readable data, with no resource fetches", () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const html = renderToStaticMarkup(
      createElement(Results, {
        run: {
          status: "completed",
          mode: "rehearse",
          result: {
            timeline: [{ time: "2026-01-01", event: "Artifact built" }],
            cohorts: [
              { population: "A", counts: { state: "suppressed", value: null } },
            ],
            lineage: [{ from: "source-1", to: "artifact-1" }],
          },
        },
        onCancel: vi.fn(),
        onRefresh: vi.fn(),
        onChallenge: vi.fn(),
        busy: false,
      }),
    );
    expect(html).toContain("Artifact built");
    expect(html).toContain("artifact-1");
    expect(html).toContain("Withheld / unavailable");
    expect(html).toContain("fictional evidence");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("loads permitted immutable history and sends exact disposition lineage without fake approval metadata", async () => {
    const fetcher = vi.fn(async (_url: string, _options?: RequestInit) =>
      response({
        challenges: [
          {
            id: "parent",
            claim_id: "finding-1",
            actor: "challenger",
            recipients: ["reviewer"],
            requested_remedy: "Review",
            correction: "Original correction",
            correction_commitment: "commitment",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetcher);
    const submit = vi.fn().mockResolvedValue(undefined);
    const tree = await mount(
      createElement(ChallengeHistory, {
        run: { run_id: "ui-run", status: "completed", mode: "audit" },
        onChallenge: submit,
        busy: false,
      }),
    );
    await click(tree, "Load challenge history");
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/workbench/runs/ui-run/challenges",
    );
    await change(tree, "Parent challenge", "parent");
    await change(tree, "Disposition", "amended");
    await change(
      tree,
      "Disposition explanation / correction",
      "Reviewed correction",
    );
    await change(tree, "Narrowed authorized recipients", "reviewer");
    await change(tree, "Existing corrected release ID (optional)", "release-2");
    await act(async () => {
      await tree.root
        .findByType("form")
        .props.onSubmit({ preventDefault: vi.fn() });
    });
    expect(submit).toHaveBeenCalledWith({
      finding_id: "finding-1",
      correction: "Reviewed correction",
      evidence_ids: [],
      requested_remedy: "Review",
      recipients: ["reviewer"],
      parent_id: "parent",
      disposition: "amended",
      corrected_release_id: "release-2",
    });
    expect(text(tree.root)).toContain("Disposition stored");
  });
  it("keeps a denied disposition as an unsaved request and leaves original history intact", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({
          challenges: [
            {
              id: "parent",
              claim_id: "finding-1",
              requested_remedy: "Review",
              correction: "Original correction",
              recipients: ["reviewer"],
            },
          ],
        }),
      ),
    );
    const submit = vi.fn().mockRejectedValue(new Error("Request blocked"));
    const tree = await mount(
      createElement(ChallengeHistory, {
        run: { run_id: "ui", mode: "audit", status: "completed" },
        onChallenge: submit,
        busy: false,
      }),
    );
    await click(tree, "Load challenge history");
    await change(tree, "Parent challenge", "parent");
    await change(
      tree,
      "Disposition explanation / correction",
      "Proposed amendment",
    );
    await change(tree, "Narrowed authorized recipients", "reviewer");
    await act(async () => {
      await tree.root
        .findByType("form")
        .props.onSubmit({ preventDefault: vi.fn() });
    });
    expect(text(tree.root)).toContain("Request blocked");
    expect(text(tree.root)).toContain("Original correction");
    expect(text(tree.root)).not.toContain("Disposition stored");
    expect(
      field(tree, "Disposition explanation / correction").props.value,
    ).toBe("Proposed amendment");
  });
  it("requests release only to authenticated self, never sends destination or source grants", async () => {
    const fetcher = vi.fn(async (_url: string, _options?: RequestInit) =>
      response({ projection: { status: "completed" }, receipt: {} }),
    );
    vi.stubGlobal("fetch", fetcher);
    await api.release("run/id");
    expect(fetcher.mock.calls[0][0]).toBe(
      "/api/workbench/runs/run%2Fid/release",
    );
    expect(fetcher.mock.calls[0][1]?.body).toBeUndefined();
  });
});
