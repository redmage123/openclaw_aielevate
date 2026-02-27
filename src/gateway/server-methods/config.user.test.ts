import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { configHandlers } from "./config.js";
import type { GatewayRequestHandlerOptions, UserContext } from "./types.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "config-user-test-"));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

type RespondCapture = {
  ok: boolean;
  payload: unknown;
  error: unknown;
};

function makeRespond(): {
  respond: GatewayRequestHandlerOptions["respond"];
  capture: RespondCapture;
} {
  const capture: RespondCapture = { ok: false, payload: undefined, error: undefined };
  const respond: GatewayRequestHandlerOptions["respond"] = (ok, payload, error) => {
    capture.ok = ok;
    capture.payload = payload;
    capture.error = error;
  };
  return { respond, capture };
}

const REQ_STUB = { method: "config.user.get", params: {} } as GatewayRequestHandlerOptions["req"];
const CTX_STUB = {} as GatewayRequestHandlerOptions["context"];

function makeOpts(overrides: Partial<GatewayRequestHandlerOptions>): GatewayRequestHandlerOptions {
  const { respond } = makeRespond();
  return {
    req: REQ_STUB,
    params: {},
    client: null,
    isWebchatConnect: () => false,
    respond: overrides.respond ?? respond,
    context: CTX_STUB,
    userContext: undefined,
    ...overrides,
  };
}

// ── config.user.get ─────────────────────────────────────────────────

describe("config.user.get", () => {
  const handler = configHandlers["config.user.get"];

  it("returns error when userContext is absent", async () => {
    const { respond, capture } = makeRespond();
    await handler(makeOpts({ respond, userContext: undefined }));
    expect(capture.ok).toBe(false);
    expect(capture.error).toBeDefined();
  });

  it("returns empty overlay when user config does not exist", async () => {
    const { respond, capture } = makeRespond();
    const userContext: UserContext = {
      userId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      userStateDir: path.join(tmpDir, "nonexistent"),
    };
    await handler(makeOpts({ respond, userContext }));
    expect(capture.ok).toBe(true);
    expect((capture.payload as Record<string, unknown>).overlay).toEqual({});
  });

  it("returns overlay when user config exists", async () => {
    const userDir = path.join(tmpDir, "user1");
    fs.mkdirSync(userDir, { recursive: true });
    fs.writeFileSync(
      path.join(userDir, "openclaw.json"),
      JSON.stringify({ models: { primary: "test-model" } }),
    );

    const { respond, capture } = makeRespond();
    const userContext: UserContext = {
      userId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      userStateDir: userDir,
    };
    await handler(makeOpts({ respond, userContext }));
    expect(capture.ok).toBe(true);
    expect((capture.payload as Record<string, unknown>).overlay).toEqual({
      models: { primary: "test-model" },
    });
  });
});

// ── config.user.set ─────────────────────────────────────────────────

describe("config.user.set", () => {
  const handler = configHandlers["config.user.set"];

  it("returns error when userContext is absent", async () => {
    const { respond, capture } = makeRespond();
    await handler(
      makeOpts({
        respond,
        userContext: undefined,
        params: { overlay: { models: {} } },
      }),
    );
    expect(capture.ok).toBe(false);
    expect(capture.error).toBeDefined();
  });

  it("returns error for non-object overlay", async () => {
    const { respond, capture } = makeRespond();
    const userContext: UserContext = {
      userId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      userStateDir: path.join(tmpDir, "user2"),
    };
    await handler(
      makeOpts({
        respond,
        userContext,
        params: { overlay: "not-an-object" },
      }),
    );
    expect(capture.ok).toBe(false);
  });

  it("returns error for array overlay", async () => {
    const { respond, capture } = makeRespond();
    const userContext: UserContext = {
      userId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      userStateDir: path.join(tmpDir, "user3"),
    };
    await handler(
      makeOpts({
        respond,
        userContext,
        params: { overlay: [1, 2, 3] },
      }),
    );
    expect(capture.ok).toBe(false);
  });

  it("returns error for null overlay", async () => {
    const { respond, capture } = makeRespond();
    const userContext: UserContext = {
      userId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      userStateDir: path.join(tmpDir, "user4"),
    };
    await handler(
      makeOpts({
        respond,
        userContext,
        params: { overlay: null },
      }),
    );
    expect(capture.ok).toBe(false);
  });

  it("writes valid overlay and returns ok", async () => {
    const userDir = path.join(tmpDir, "user-write");
    const { respond, capture } = makeRespond();
    const userContext: UserContext = {
      userId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      userStateDir: userDir,
    };
    await handler(
      makeOpts({
        respond,
        userContext,
        params: { overlay: { models: { primary: "new-model" } } },
      }),
    );
    expect(capture.ok).toBe(true);
    const content = JSON.parse(fs.readFileSync(path.join(userDir, "openclaw.json"), "utf-8"));
    expect(content.models.primary).toBe("new-model");
  });

  it("rejects admin-only keys in overlay", async () => {
    const userDir = path.join(tmpDir, "user-admin-keys");
    const { respond, capture } = makeRespond();
    const userContext: UserContext = {
      userId: "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      userStateDir: userDir,
    };
    await handler(
      makeOpts({
        respond,
        userContext,
        params: { overlay: { gateway: { port: 9999 } } },
      }),
    );
    expect(capture.ok).toBe(false);
    expect(JSON.stringify(capture.error)).toContain("admin-only");
  });
});
