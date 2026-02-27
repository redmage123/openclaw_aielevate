import { describe, expect, it } from "vitest";
import { resolveUserStateDir } from "../config/paths.js";
import type { GatewayClient, UserContext } from "./server-methods/types.js";

// Test the user context construction logic from handleGatewayRequest.
// We verify the pattern: if client.userId is set, userContext is constructed;
// otherwise it remains undefined.
// Rather than importing the full handler (which has many deps), we test
// the isolated logic that mirrors lines 146-153 of server-methods.ts.

function buildUserContext(client: GatewayClient | null): UserContext | undefined {
  if (client?.userId) {
    return {
      userId: client.userId,
      userStateDir: resolveUserStateDir(client.userId),
    };
  }
  return undefined;
}

const VALID_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";

// ConnectParams stub — only the fields needed by GatewayClient.
const CONNECT_STUB = {} as GatewayClient["connect"];

describe("userContext construction", () => {
  it("creates userContext when client.userId is set", () => {
    const client: GatewayClient = {
      connect: CONNECT_STUB,
      userId: VALID_UUID,
    };
    const ctx = buildUserContext(client);
    expect(ctx).toBeDefined();
    expect(ctx!.userId).toBe(VALID_UUID);
    expect(ctx!.userStateDir).toContain("/users/");
    expect(ctx!.userStateDir).toContain(VALID_UUID);
  });

  it("returns undefined when client.userId is absent", () => {
    const client: GatewayClient = { connect: CONNECT_STUB };
    expect(buildUserContext(client)).toBeUndefined();
  });

  it("returns undefined when client is null", () => {
    expect(buildUserContext(null)).toBeUndefined();
  });

  it("returns undefined when client.userId is empty string", () => {
    const client: GatewayClient = { connect: CONNECT_STUB, userId: "" };
    expect(buildUserContext(client)).toBeUndefined();
  });

  it("userStateDir is derived from resolveUserStateDir", () => {
    const client: GatewayClient = {
      connect: CONNECT_STUB,
      userId: VALID_UUID,
    };
    const ctx = buildUserContext(client);
    const expected = resolveUserStateDir(VALID_UUID);
    expect(ctx!.userStateDir).toBe(expected);
  });
});
