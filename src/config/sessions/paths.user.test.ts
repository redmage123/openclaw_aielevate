import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  resolveDefaultSessionStorePath,
  resolveSessionTranscriptPath,
  resolveStorePath,
  resolveUserAgentSessionsDir,
} from "./paths.js";

const USER_STATE_DIR = "/tmp/state/users/a1b2c3d4-e5f6-7890-abcd-ef1234567890";

describe("resolveUserAgentSessionsDir", () => {
  it("returns {userStateDir}/agents/{agentId}/sessions", () => {
    const result = resolveUserAgentSessionsDir(USER_STATE_DIR, "my-agent");
    expect(result).toBe(path.join(USER_STATE_DIR, "agents", "my-agent", "sessions"));
  });

  it("defaults to 'default' agentId when omitted", () => {
    const result = resolveUserAgentSessionsDir(USER_STATE_DIR);
    expect(result).toContain("/agents/");
    expect(result).toContain("/sessions");
  });
});

describe("resolveDefaultSessionStorePath", () => {
  it("uses user-scoped path when userStateDir is provided", () => {
    const result = resolveDefaultSessionStorePath("default", USER_STATE_DIR);
    expect(result).toContain(USER_STATE_DIR);
    expect(result.endsWith("sessions.json")).toBe(true);
  });

  it("falls back to global when userStateDir is undefined", () => {
    const result = resolveDefaultSessionStorePath("default", undefined);
    expect(result).not.toContain("/users/");
    expect(result.endsWith("sessions.json")).toBe(true);
  });

  it("user-scoped and global paths differ", () => {
    const userPath = resolveDefaultSessionStorePath("default", USER_STATE_DIR);
    const globalPath = resolveDefaultSessionStorePath("default", undefined);
    expect(userPath).not.toBe(globalPath);
  });
});

describe("resolveSessionTranscriptPath", () => {
  it("returns user-scoped transcript path when userStateDir provided", () => {
    const result = resolveSessionTranscriptPath("session1", "default", undefined, USER_STATE_DIR);
    expect(result).toContain(USER_STATE_DIR);
    expect(result.endsWith("session1.jsonl")).toBe(true);
  });

  it("returns global transcript path when userStateDir is undefined", () => {
    const result = resolveSessionTranscriptPath("session1", "default", undefined, undefined);
    expect(result).not.toContain("/users/");
    expect(result.endsWith("session1.jsonl")).toBe(true);
  });

  it("includes topic suffix when topicId provided", () => {
    const result = resolveSessionTranscriptPath("session1", "default", "topic42", USER_STATE_DIR);
    expect(result).toContain("session1-topic-topic42");
  });

  it("backward compat: identical output when userStateDir is undefined", () => {
    const withUndefined = resolveSessionTranscriptPath("s1", "default", undefined, undefined);
    const withoutParam = resolveSessionTranscriptPath("s1", "default");
    expect(withUndefined).toBe(withoutParam);
  });
});

describe("resolveStorePath", () => {
  it("uses user-scoped default when no custom store is provided", () => {
    const result = resolveStorePath(undefined, { userStateDir: USER_STATE_DIR });
    expect(result).toContain(USER_STATE_DIR);
    expect(result.endsWith("sessions.json")).toBe(true);
  });

  it("custom store path overrides user scoping (admin-controlled)", () => {
    const customStore = "/admin/shared/sessions.json";
    const result = resolveStorePath(customStore, { userStateDir: USER_STATE_DIR });
    expect(result).not.toContain(USER_STATE_DIR);
    expect(result).toContain(customStore);
  });

  it("falls back to global when userStateDir is undefined", () => {
    const result = resolveStorePath(undefined, { userStateDir: undefined });
    expect(result).not.toContain("/users/");
    expect(result.endsWith("sessions.json")).toBe(true);
  });

  it("expands {agentId} placeholder in custom store", () => {
    const result = resolveStorePath("/data/{agentId}/store.json", { agentId: "bot" });
    expect(result).toContain("/data/bot/store.json");
  });
});
