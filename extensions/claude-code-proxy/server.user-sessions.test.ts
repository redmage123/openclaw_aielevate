import { homedir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// The proxy's session functions are private and the module has side effects
// (loads sessions on import). We test the core isolation logic by replicating
// the UUID regex and path resolution patterns used in server.ts.

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const GLOBAL_SESSION_FILE = join(homedir(), ".openclaw", "claude-code-sessions.json");

function resolveSessionFilePath(userId?: string): string {
  if (userId && UUID_RE.test(userId)) {
    return join(homedir(), ".openclaw", "users", userId, "claude-code-sessions.json");
  }
  return GLOBAL_SESSION_FILE;
}

function resolveStoreKey(userId?: string): string {
  return userId && UUID_RE.test(userId) ? userId : "";
}

const VALID_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
const VALID_UUID_2 = "11111111-2222-3333-4444-555555555555";

// ── resolveSessionFilePath ──────────────────────────────────────────

describe("resolveSessionFilePath (proxy sessions)", () => {
  it("returns global path when userId is undefined", () => {
    const result = resolveSessionFilePath(undefined);
    expect(result).toBe(GLOBAL_SESSION_FILE);
    expect(result).not.toContain("/users/");
  });

  it("returns global path when userId is empty string", () => {
    const result = resolveSessionFilePath("");
    expect(result).toBe(GLOBAL_SESSION_FILE);
  });

  it("returns per-user path for valid UUID", () => {
    const result = resolveSessionFilePath(VALID_UUID);
    expect(result).toContain("/users/");
    expect(result).toContain(VALID_UUID);
    expect(result.endsWith("claude-code-sessions.json")).toBe(true);
  });

  it("falls back to global for non-UUID userId", () => {
    const result = resolveSessionFilePath("not-a-uuid");
    expect(result).toBe(GLOBAL_SESSION_FILE);
  });

  it("falls back to global for path traversal attempt", () => {
    const result = resolveSessionFilePath("../../../etc/passwd");
    expect(result).toBe(GLOBAL_SESSION_FILE);
  });

  it("different valid UUIDs produce different paths", () => {
    const path1 = resolveSessionFilePath(VALID_UUID);
    const path2 = resolveSessionFilePath(VALID_UUID_2);
    expect(path1).not.toBe(path2);
  });
});

// ── store key isolation ─────────────────────────────────────────────

describe("session store isolation", () => {
  it("global store key is empty string when no userId", () => {
    expect(resolveStoreKey(undefined)).toBe("");
    expect(resolveStoreKey("")).toBe("");
  });

  it("valid UUID becomes the store key", () => {
    expect(resolveStoreKey(VALID_UUID)).toBe(VALID_UUID);
  });

  it("non-UUID falls back to global (empty) key", () => {
    expect(resolveStoreKey("invalid")).toBe("");
  });

  it("different userIds map to different store keys", () => {
    const key1 = resolveStoreKey(VALID_UUID);
    const key2 = resolveStoreKey(VALID_UUID_2);
    expect(key1).not.toBe(key2);
    expect(key1).not.toBe("");
    expect(key2).not.toBe("");
  });

  it("simulated store isolation: per-user maps are independent", () => {
    const stores = new Map<string, Map<string, { value: string }>>();

    function getStore(userId?: string) {
      const key = resolveStoreKey(userId);
      let store = stores.get(key);
      if (!store) {
        store = new Map();
        stores.set(key, store);
      }
      return store;
    }

    // Global store
    getStore().set("conv1", { value: "global-session" });

    // User 1 store
    getStore(VALID_UUID).set("conv1", { value: "user1-session" });

    // User 2 store
    getStore(VALID_UUID_2).set("conv1", { value: "user2-session" });

    // Verify isolation
    expect(getStore().get("conv1")!.value).toBe("global-session");
    expect(getStore(VALID_UUID).get("conv1")!.value).toBe("user1-session");
    expect(getStore(VALID_UUID_2).get("conv1")!.value).toBe("user2-session");

    // Modifying one doesn't affect others
    getStore(VALID_UUID).delete("conv1");
    expect(getStore().has("conv1")).toBe(true);
    expect(getStore(VALID_UUID_2).has("conv1")).toBe(true);
    expect(getStore(VALID_UUID).has("conv1")).toBe(false);
  });
});
