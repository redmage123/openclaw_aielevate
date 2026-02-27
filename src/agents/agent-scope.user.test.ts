import path from "node:path";
import { describe, expect, it } from "vitest";
import type { OpenClawConfig } from "../config/config.js";
import { resolveAgentWorkspaceDirForUser } from "./agent-scope.js";

const USER_STATE_DIR = "/tmp/state/users/a1b2c3d4-e5f6-7890-abcd-ef1234567890";
const EMPTY_CFG: OpenClawConfig = {};

describe("resolveAgentWorkspaceDirForUser", () => {
  it("returns {userStateDir}/workspace/{agentId} when userStateDir provided", () => {
    const result = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "my-bot", USER_STATE_DIR);
    expect(result).toBe(path.join(USER_STATE_DIR, "workspace", "my-bot"));
  });

  it("normalizes agentId to lowercase", () => {
    const result = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "My-Bot", USER_STATE_DIR);
    expect(result).toBe(path.join(USER_STATE_DIR, "workspace", "my-bot"));
  });

  it("strips null bytes from agentId", () => {
    const result = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "bot\0id", USER_STATE_DIR);
    expect(result).not.toContain("\0");
    // normalizeAgentId replaces invalid chars with "-", then stripNullBytes runs
    expect(result).toBe(path.join(USER_STATE_DIR, "workspace", "bot-id"));
  });

  it("falls back to global workspace when userStateDir is undefined", () => {
    const result = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "default", undefined);
    expect(result).not.toContain("/users/");
    expect(result).toBeTruthy();
  });

  it("user workspace differs from global workspace", () => {
    const userResult = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "default", USER_STATE_DIR);
    const globalResult = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "default", undefined);
    expect(userResult).not.toBe(globalResult);
  });

  it("different userStateDirs produce different workspaces", () => {
    const dir1 = "/tmp/state/users/11111111-1111-1111-1111-111111111111";
    const dir2 = "/tmp/state/users/22222222-2222-2222-2222-222222222222";
    const result1 = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "bot", dir1);
    const result2 = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "bot", dir2);
    expect(result1).not.toBe(result2);
  });

  it("different agentIds under same user produce different workspaces", () => {
    const result1 = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "bot-a", USER_STATE_DIR);
    const result2 = resolveAgentWorkspaceDirForUser(EMPTY_CFG, "bot-b", USER_STATE_DIR);
    expect(result1).not.toBe(result2);
  });
});
