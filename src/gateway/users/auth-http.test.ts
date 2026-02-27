import fs from "node:fs";
import { type IncomingMessage, type ServerResponse } from "node:http";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { handleAuthHttpRequest, type AuthHttpOptions } from "./auth-http.js";
import { UserDb } from "./user-db.js";

let tmpDir: string;
let userDb: UserDb;
let opts: AuthHttpOptions;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "auth-http-test-"));
  const dbPath = path.join(tmpDir, "users.db");
  userDb = new UserDb({ dbPath, sessionLifetimeHours: 1 });
  opts = { userDb, allowSignup: true };
});

afterEach(() => {
  try {
    userDb.close();
  } catch {
    // ok
  }
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

function makeReq(method: string, url: string, body?: unknown, headers?: Record<string, string>) {
  const bodyStr = body ? JSON.stringify(body) : "";
  const readable = Readable.from([bodyStr]) as IncomingMessage;
  readable.method = method;
  readable.url = url;
  readable.headers = {
    "content-type": "application/json",
    ...headers,
  };
  readable.socket = { remoteAddress: "127.0.0.1" } as IncomingMessage["socket"];
  return readable;
}

type CapturedResponse = {
  statusCode: number;
  headers: Record<string, string>;
  body: string;
  json: () => Record<string, unknown>;
};

function makeRes(): { res: ServerResponse; captured: () => CapturedResponse } {
  const chunks: Buffer[] = [];
  const headers: Record<string, string> = {};
  const res = {
    statusCode: 200,
    setHeader(name: string, value: string) {
      headers[name.toLowerCase()] = value;
    },
    getHeader(name: string) {
      return headers[name.toLowerCase()];
    },
    end(data?: string | Buffer) {
      if (data) {
        chunks.push(Buffer.from(data));
      }
    },
    write(data: string | Buffer) {
      chunks.push(Buffer.from(data));
      return true;
    },
    flushHeaders() {},
  } as unknown as ServerResponse;

  return {
    res,
    captured: () => {
      const body = Buffer.concat(chunks).toString("utf-8");
      return {
        statusCode: (res as { statusCode: number }).statusCode,
        headers,
        body,
        json: () => JSON.parse(body) as Record<string, unknown>,
      };
    },
  };
}

const VALID_USER = {
  username: "testuser",
  email: "test@example.com",
  password: "password123",
  displayName: "Test User",
};

// ── Routing ─────────────────────────────────────────────────────────

describe("routing", () => {
  it("returns false for non-matching paths", async () => {
    const req = makeReq("GET", "/api/other");
    const { res } = makeRes();
    const handled = await handleAuthHttpRequest(req, res, opts);
    expect(handled).toBe(false);
  });

  it("returns 405 for wrong method on signup", async () => {
    const req = makeReq("GET", "/api/auth/signup");
    const { res, captured } = makeRes();
    const handled = await handleAuthHttpRequest(req, res, opts);
    expect(handled).toBe(true);
    expect(captured().statusCode).toBe(405);
  });

  it("returns 405 for wrong method on login", async () => {
    const req = makeReq("GET", "/api/auth/login");
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(405);
  });

  it("returns 405 for wrong method on me", async () => {
    const req = makeReq("POST", "/api/auth/me");
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(405);
  });
});

// ── Signup ───────────────────────────────────────────────────────────

describe("signup", () => {
  it("creates user and returns token (201)", async () => {
    const req = makeReq("POST", "/api/auth/signup", VALID_USER);
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    const data = captured();
    expect(data.statusCode).toBe(201);
    const body = data.json();
    expect(body.token).toBeTruthy();
    expect((body.user as Record<string, unknown>).username).toBe("testuser");
  });

  it("first signup user gets admin role", async () => {
    const req = makeReq("POST", "/api/auth/signup", VALID_USER);
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    const body = captured().json();
    expect((body.user as Record<string, unknown>).role).toBe("admin");
  });

  it("returns 403 when signup is disabled", async () => {
    const req = makeReq("POST", "/api/auth/signup", VALID_USER);
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, { ...opts, allowSignup: false });
    expect(captured().statusCode).toBe(403);
  });

  it("rejects invalid username", async () => {
    const req = makeReq("POST", "/api/auth/signup", { ...VALID_USER, username: "a" });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });

  it("rejects invalid email", async () => {
    const req = makeReq("POST", "/api/auth/signup", { ...VALID_USER, email: "not-email" });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });

  it("rejects short password", async () => {
    const req = makeReq("POST", "/api/auth/signup", { ...VALID_USER, password: "short" });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });

  it("rejects missing displayName", async () => {
    const req = makeReq("POST", "/api/auth/signup", { ...VALID_USER, displayName: "" });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });

  it("rejects duplicate email (409)", async () => {
    userDb.createUser(VALID_USER);
    const req = makeReq("POST", "/api/auth/signup", {
      ...VALID_USER,
      username: "other",
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(409);
  });

  it("rejects duplicate username (409)", async () => {
    userDb.createUser(VALID_USER);
    const req = makeReq("POST", "/api/auth/signup", {
      ...VALID_USER,
      email: "other@test.com",
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(409);
  });
});

// ── Login ────────────────────────────────────────────────────────────

describe("login", () => {
  it("returns token for valid credentials", async () => {
    userDb.createUser(VALID_USER);
    const req = makeReq("POST", "/api/auth/login", {
      username: "testuser",
      password: "password123",
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    const data = captured();
    expect(data.statusCode).toBe(200);
    const body = data.json();
    expect(body.token).toBeTruthy();
    expect((body.user as Record<string, unknown>).username).toBe("testuser");
  });

  it("returns 401 for wrong password", async () => {
    userDb.createUser(VALID_USER);
    const req = makeReq("POST", "/api/auth/login", {
      username: "testuser",
      password: "wrongpassword",
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(401);
  });

  it("accepts email as identifier", async () => {
    userDb.createUser(VALID_USER);
    const req = makeReq("POST", "/api/auth/login", {
      email: "test@example.com",
      password: "password123",
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(200);
  });

  it("returns 400 for missing identifier", async () => {
    const req = makeReq("POST", "/api/auth/login", { password: "test1234" });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });
});

// ── Logout ───────────────────────────────────────────────────────────

describe("logout", () => {
  it("revokes session and returns 200", async () => {
    const user = userDb.createUser(VALID_USER);
    const session = userDb.createSession(user.id);
    const req = makeReq(
      "POST",
      "/api/auth/logout",
      {},
      {
        authorization: `Bearer ${session.token}`,
      },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(200);
    expect(userDb.validateSession(session.token)).toBeNull();
  });

  it("returns 401 without token", async () => {
    const req = makeReq("POST", "/api/auth/logout");
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(401);
  });
});

// ── GET /me ──────────────────────────────────────────────────────────

describe("GET /me", () => {
  it("returns user for valid token", async () => {
    const user = userDb.createUser(VALID_USER);
    const session = userDb.createSession(user.id);
    const req = makeReq("GET", "/api/auth/me", undefined, {
      authorization: `Bearer ${session.token}`,
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    const data = captured();
    expect(data.statusCode).toBe(200);
    const body = data.json();
    expect((body.user as Record<string, unknown>).username).toBe("testuser");
  });

  it("returns 401 for invalid token", async () => {
    const req = makeReq("GET", "/api/auth/me", undefined, {
      authorization: "Bearer invalid-token",
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(401);
  });

  it("returns 401 without authorization header", async () => {
    const req = makeReq("GET", "/api/auth/me");
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(401);
  });
});

// ── PATCH /me ────────────────────────────────────────────────────────

describe("PATCH /me", () => {
  it("updates displayName", async () => {
    const user = userDb.createUser(VALID_USER);
    const session = userDb.createSession(user.id);
    const req = makeReq(
      "PATCH",
      "/api/auth/me",
      { displayName: "New Name" },
      {
        authorization: `Bearer ${session.token}`,
      },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(200);
    const found = userDb.findById(user.id);
    expect(found!.displayName).toBe("New Name");
  });

  it("rejects invalid email format", async () => {
    const user = userDb.createUser(VALID_USER);
    const session = userDb.createSession(user.id);
    const req = makeReq(
      "PATCH",
      "/api/auth/me",
      { email: "bad-email" },
      {
        authorization: `Bearer ${session.token}`,
      },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });
});

// ── PUT /me/password ─────────────────────────────────────────────────

describe("PUT /me/password", () => {
  it("changes password with valid current password", async () => {
    const user = userDb.createUser(VALID_USER);
    const session = userDb.createSession(user.id);
    const req = makeReq(
      "PUT",
      "/api/auth/me/password",
      {
        currentPassword: "password123",
        newPassword: "newpassword456",
      },
      { authorization: `Bearer ${session.token}` },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(200);
    expect(userDb.authenticateUser("testuser", "newpassword456")).not.toBeNull();
  });

  it("rejects wrong current password", async () => {
    const user = userDb.createUser(VALID_USER);
    const session = userDb.createSession(user.id);
    const req = makeReq(
      "PUT",
      "/api/auth/me/password",
      {
        currentPassword: "wrong",
        newPassword: "newpassword456",
      },
      { authorization: `Bearer ${session.token}` },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(401);
  });

  it("rejects short new password", async () => {
    const user = userDb.createUser(VALID_USER);
    const session = userDb.createSession(user.id);
    const req = makeReq(
      "PUT",
      "/api/auth/me/password",
      {
        currentPassword: "password123",
        newPassword: "short",
      },
      { authorization: `Bearer ${session.token}` },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });
});

// ── Admin: GET /users ────────────────────────────────────────────────

describe("admin: GET /users", () => {
  it("returns user list for admin", async () => {
    const admin = userDb.createUser(VALID_USER);
    const session = userDb.createSession(admin.id);
    const req = makeReq("GET", "/api/auth/users", undefined, {
      authorization: `Bearer ${session.token}`,
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    const data = captured();
    expect(data.statusCode).toBe(200);
    const body = data.json();
    expect(body.users).toHaveLength(1);
  });

  it("returns 403 for non-admin", async () => {
    userDb.createUser(VALID_USER);
    const regular = userDb.createUser({
      username: "regular",
      email: "regular@test.com",
      password: "password123",
      displayName: "Regular",
    });
    const session = userDb.createSession(regular.id);
    const req = makeReq("GET", "/api/auth/users", undefined, {
      authorization: `Bearer ${session.token}`,
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(403);
  });
});

// ── Admin: PATCH /users/:id ──────────────────────────────────────────

describe("admin: PATCH /users/:id", () => {
  it("updates user role", async () => {
    const admin = userDb.createUser(VALID_USER);
    const regular = userDb.createUser({
      username: "regular",
      email: "reg@test.com",
      password: "password123",
      displayName: "Reg",
    });
    const session = userDb.createSession(admin.id);
    const req = makeReq(
      "PATCH",
      `/api/auth/users/${regular.id}`,
      { role: "admin" },
      {
        authorization: `Bearer ${session.token}`,
      },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(200);
    const found = userDb.findById(regular.id);
    expect(found!.role).toBe("admin");
  });

  it("returns 403 for non-admin", async () => {
    userDb.createUser(VALID_USER);
    const regular = userDb.createUser({
      username: "regular",
      email: "reg@test.com",
      password: "password123",
      displayName: "Reg",
    });
    const session = userDb.createSession(regular.id);
    const req = makeReq(
      "PATCH",
      `/api/auth/users/${regular.id}`,
      { role: "admin" },
      {
        authorization: `Bearer ${session.token}`,
      },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(403);
  });

  it("prevents admin from demoting themselves", async () => {
    const admin = userDb.createUser(VALID_USER);
    const session = userDb.createSession(admin.id);
    const req = makeReq(
      "PATCH",
      `/api/auth/users/${admin.id}`,
      { role: "user" },
      {
        authorization: `Bearer ${session.token}`,
      },
    );
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(400);
  });
});

// ── Admin: DELETE /users/:id/sessions ────────────────────────────────

describe("admin: DELETE /users/:id/sessions", () => {
  it("revokes all sessions for target user", async () => {
    const admin = userDb.createUser(VALID_USER);
    const target = userDb.createUser({
      username: "target",
      email: "target@test.com",
      password: "password123",
      displayName: "Target",
    });
    const targetSession = userDb.createSession(target.id);
    const adminSession = userDb.createSession(admin.id);
    const req = makeReq("DELETE", `/api/auth/users/${target.id}/sessions`, undefined, {
      authorization: `Bearer ${adminSession.token}`,
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    const data = captured();
    expect(data.statusCode).toBe(200);
    const body = data.json();
    expect(typeof body.revokedCount === "number" && body.revokedCount >= 1).toBe(true);
    expect(userDb.validateSession(targetSession.token)).toBeNull();
  });

  it("returns 403 for non-admin", async () => {
    userDb.createUser(VALID_USER);
    const regular = userDb.createUser({
      username: "regular",
      email: "reg@test.com",
      password: "password123",
      displayName: "Reg",
    });
    const session = userDb.createSession(regular.id);
    const req = makeReq("DELETE", `/api/auth/users/${regular.id}/sessions`, undefined, {
      authorization: `Bearer ${session.token}`,
    });
    const { res, captured } = makeRes();
    await handleAuthHttpRequest(req, res, opts);
    expect(captured().statusCode).toBe(403);
  });
});
