import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  resolveUserStateDir,
  resolveUserOAuthDir,
  resolveUserOAuthPath,
  resolveUserMemoryDir,
} from "./paths.js";

const VALID_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
const VALID_UUID_2 = "11111111-2222-3333-4444-555555555555";

describe("resolveUserStateDir", () => {
  it("returns {stateDir}/users/{userId} for a valid UUID", () => {
    const stateDir = "/tmp/test-state";
    const result = resolveUserStateDir(VALID_UUID, {}, stateDir);
    expect(result).toBe(path.join(stateDir, "users", VALID_UUID));
  });

  it("accepts uppercase UUIDs", () => {
    const upper = VALID_UUID.toUpperCase();
    const stateDir = "/tmp/state";
    const result = resolveUserStateDir(upper, {}, stateDir);
    expect(result).toBe(path.join(stateDir, "users", upper));
  });

  it("throws for an empty string", () => {
    expect(() => resolveUserStateDir("")).toThrow("Invalid user ID format");
  });

  it("throws for a non-UUID string", () => {
    expect(() => resolveUserStateDir("not-a-uuid")).toThrow("Invalid user ID format");
  });

  it("throws for path traversal attempts", () => {
    expect(() => resolveUserStateDir("../../../etc/passwd")).toThrow("Invalid user ID format");
    expect(() => resolveUserStateDir("..")).toThrow("Invalid user ID format");
    expect(() => resolveUserStateDir("./foo")).toThrow("Invalid user ID format");
  });

  it("throws for dots-only input", () => {
    expect(() => resolveUserStateDir("..")).toThrow("Invalid user ID format");
    expect(() => resolveUserStateDir(".")).toThrow("Invalid user ID format");
  });

  it("throws for strings with path separators", () => {
    expect(() => resolveUserStateDir("foo/bar")).toThrow("Invalid user ID format");
    expect(() => resolveUserStateDir("foo\\bar")).toThrow("Invalid user ID format");
  });

  it("throws for a UUID with extra characters", () => {
    expect(() => resolveUserStateDir(VALID_UUID + "x")).toThrow("Invalid user ID format");
  });

  it("respects OPENCLAW_STATE_DIR env override", () => {
    const env = { OPENCLAW_STATE_DIR: "/custom/state" } as NodeJS.ProcessEnv;
    const result = resolveUserStateDir(VALID_UUID, env, "/custom/state");
    expect(result).toBe(path.join("/custom/state", "users", VALID_UUID));
  });

  it("produces different directories for different userIds", () => {
    const stateDir = "/tmp/state";
    const dir1 = resolveUserStateDir(VALID_UUID, {}, stateDir);
    const dir2 = resolveUserStateDir(VALID_UUID_2, {}, stateDir);
    expect(dir1).not.toBe(dir2);
  });

  it("user dirs are nested under global state dir", () => {
    const stateDir = "/home/user/.openclaw";
    const result = resolveUserStateDir(VALID_UUID, {}, stateDir);
    expect(result.startsWith(stateDir)).toBe(true);
    expect(result).toContain("/users/");
  });
});

describe("resolveUserOAuthDir", () => {
  it("returns {userStateDir}/credentials/", () => {
    const userStateDir = `/tmp/state/users/${VALID_UUID}`;
    expect(resolveUserOAuthDir(userStateDir)).toBe(path.join(userStateDir, "credentials"));
  });

  it("does not overlap with global credentials dir", () => {
    const userDir = resolveUserOAuthDir(`/home/u/.openclaw/users/${VALID_UUID}`);
    const globalDir = "/home/u/.openclaw/credentials";
    expect(userDir).not.toBe(globalDir);
    expect(userDir).toContain("/users/");
  });
});

describe("resolveUserOAuthPath", () => {
  it("returns {userStateDir}/credentials/oauth.json", () => {
    const userStateDir = `/tmp/state/users/${VALID_UUID}`;
    expect(resolveUserOAuthPath(userStateDir)).toBe(
      path.join(userStateDir, "credentials", "oauth.json"),
    );
  });
});

describe("resolveUserMemoryDir", () => {
  it("returns {userStateDir}/memory/", () => {
    const userStateDir = `/tmp/state/users/${VALID_UUID}`;
    expect(resolveUserMemoryDir(userStateDir)).toBe(path.join(userStateDir, "memory"));
  });

  it("does not overlap with global memory dir", () => {
    const userMemory = resolveUserMemoryDir(`/home/u/.openclaw/users/${VALID_UUID}`);
    expect(userMemory).toContain("/users/");
    expect(userMemory).toContain("/memory");
  });
});
