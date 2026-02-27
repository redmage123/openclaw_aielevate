import type { IncomingMessage, ServerResponse } from "node:http";
import type { AuthRateLimiter } from "../auth-rate-limit.js";
import {
  readJsonBodyOrError,
  sendInvalidRequest,
  sendJson,
  sendMethodNotAllowed,
  sendRateLimited,
  sendUnauthorized,
} from "../http-common.js";
import { resolveClientIp } from "../net.js";
import type { UserDb } from "./user-db.js";

const AUTH_RATE_LIMIT_SCOPE = "multi-user-auth";
const MAX_BODY_BYTES = 4096;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const USERNAME_RE = /^[a-zA-Z0-9_-]{3,32}$/;
const MIN_PASSWORD_LENGTH = 8;

export type AuthHttpOptions = {
  userDb: UserDb;
  rateLimiter?: AuthRateLimiter;
  allowSignup?: boolean;
  trustedProxies?: string[];
};

/**
 * Handle `/api/auth/*` requests.
 * Returns `true` if the request was handled (even on error), `false` if path didn't match.
 */
export async function handleAuthHttpRequest(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
): Promise<boolean> {
  const url = new URL(req.url ?? "/", "http://localhost");
  const pathname = url.pathname;

  if (pathname === "/api/auth/signup" && req.method === "POST") {
    await handleSignup(req, res, opts);
    return true;
  }
  if (pathname === "/api/auth/login" && req.method === "POST") {
    await handleLogin(req, res, opts);
    return true;
  }
  if (pathname === "/api/auth/logout" && req.method === "POST") {
    await handleLogout(req, res, opts);
    return true;
  }
  if (pathname === "/api/auth/me" && req.method === "GET") {
    handleMe(req, res, opts);
    return true;
  }

  // Self-service: update profile
  if (pathname === "/api/auth/me" && req.method === "PATCH") {
    await handleUpdateProfile(req, res, opts);
    return true;
  }
  // Self-service: change password
  if (pathname === "/api/auth/me/password" && req.method === "PUT") {
    await handleChangePassword(req, res, opts);
    return true;
  }

  // Admin: list users
  if (pathname === "/api/auth/users" && req.method === "GET") {
    handleListUsers(req, res, opts);
    return true;
  }
  // Admin: update user role/status
  const userPatchMatch = pathname.match(/^\/api\/auth\/users\/([^/]+)$/);
  if (userPatchMatch && req.method === "PATCH") {
    await handleAdminUpdateUser(req, res, opts, userPatchMatch[1]);
    return true;
  }
  // Admin: revoke all sessions for a user
  const userSessionsRevokeMatch = pathname.match(/^\/api\/auth\/users\/([^/]+)\/sessions$/);
  if (userSessionsRevokeMatch && req.method === "DELETE") {
    handleAdminRevokeUserSessions(req, res, opts, userSessionsRevokeMatch[1]);
    return true;
  }

  // Handle wrong method on known paths
  if (pathname === "/api/auth/signup" || pathname === "/api/auth/login") {
    sendMethodNotAllowed(res, "POST");
    return true;
  }
  if (pathname === "/api/auth/logout") {
    sendMethodNotAllowed(res, "POST");
    return true;
  }
  if (pathname === "/api/auth/me") {
    sendMethodNotAllowed(res, "GET, PATCH");
    return true;
  }
  if (pathname === "/api/auth/me/password") {
    sendMethodNotAllowed(res, "PUT");
    return true;
  }
  if (pathname === "/api/auth/users") {
    sendMethodNotAllowed(res, "GET");
    return true;
  }

  return false;
}

async function handleSignup(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
): Promise<void> {
  if (opts.allowSignup === false) {
    sendJson(res, 403, {
      error: { message: "Signup is disabled", type: "forbidden" },
    });
    return;
  }

  const clientIp = resolveClientIp({
    remoteAddr: req.socket?.remoteAddress ?? "",
    trustedProxies: opts.trustedProxies,
  });
  if (opts.rateLimiter) {
    const check = opts.rateLimiter.check(clientIp, AUTH_RATE_LIMIT_SCOPE);
    if (!check.allowed) {
      sendRateLimited(res, check.retryAfterMs);
      return;
    }
  }

  const body = (await readJsonBodyOrError(req, res, MAX_BODY_BYTES)) as
    | { username?: string; email?: string; password?: string; displayName?: string }
    | undefined;
  if (!body) {
    return;
  }

  const { username, email, password, displayName } = body;
  if (typeof username !== "string" || !USERNAME_RE.test(username.trim())) {
    sendInvalidRequest(
      res,
      "Username must be 3-32 characters and contain only letters, numbers, hyphens, and underscores",
    );
    return;
  }
  if (typeof email !== "string" || !EMAIL_RE.test(email.trim())) {
    sendInvalidRequest(res, "Valid email is required");
    return;
  }
  if (typeof password !== "string" || password.length < MIN_PASSWORD_LENGTH) {
    sendInvalidRequest(res, `Password must be at least ${MIN_PASSWORD_LENGTH} characters`);
    return;
  }
  if (typeof displayName !== "string" || displayName.trim().length === 0) {
    sendInvalidRequest(res, "Display name is required");
    return;
  }

  const existingEmail = opts.userDb.findByEmail(email);
  if (existingEmail) {
    sendJson(res, 409, {
      error: { message: "An account with this email already exists", type: "conflict" },
    });
    return;
  }
  const existingUsername = opts.userDb.findByUsername(username);
  if (existingUsername) {
    sendJson(res, 409, {
      error: { message: "This username is already taken", type: "conflict" },
    });
    return;
  }

  const user = opts.userDb.createUser({
    username: username.trim(),
    email: email.trim(),
    password,
    displayName: displayName.trim(),
  });
  const session = opts.userDb.createSession(user.id, {
    userAgent: req.headers["user-agent"] ?? undefined,
    ipAddress: clientIp ?? undefined,
  });

  sendJson(res, 201, {
    token: session.token,
    expiresAt: session.expiresAt,
    user,
  });
}

async function handleLogin(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
): Promise<void> {
  const clientIp = resolveClientIp({
    remoteAddr: req.socket?.remoteAddress ?? "",
    trustedProxies: opts.trustedProxies,
  });
  if (opts.rateLimiter) {
    const check = opts.rateLimiter.check(clientIp, AUTH_RATE_LIMIT_SCOPE);
    if (!check.allowed) {
      sendRateLimited(res, check.retryAfterMs);
      return;
    }
  }

  const body = (await readJsonBodyOrError(req, res, MAX_BODY_BYTES)) as
    | { username?: string; email?: string; password?: string }
    | undefined;
  if (!body) {
    return;
  }

  const identifier = body.username ?? body.email ?? "";
  const { password } = body;
  if (typeof identifier !== "string" || !identifier.trim()) {
    sendInvalidRequest(res, "Username or email is required");
    return;
  }
  if (typeof password !== "string" || !password) {
    sendInvalidRequest(res, "Password is required");
    return;
  }

  const user = opts.userDb.authenticateUser(identifier, password);
  if (!user) {
    opts.rateLimiter?.recordFailure(clientIp, AUTH_RATE_LIMIT_SCOPE);
    sendJson(res, 401, {
      error: { message: "Invalid email or password", type: "unauthorized" },
    });
    return;
  }

  opts.rateLimiter?.reset(clientIp, AUTH_RATE_LIMIT_SCOPE);

  const session = opts.userDb.createSession(user.id, {
    userAgent: req.headers["user-agent"] ?? undefined,
    ipAddress: clientIp ?? undefined,
  });

  sendJson(res, 200, {
    token: session.token,
    expiresAt: session.expiresAt,
    user,
  });
}

async function handleLogout(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
): Promise<void> {
  const token = extractBearerToken(req);
  if (!token) {
    sendUnauthorized(res);
    return;
  }

  opts.userDb.revokeSession(token);
  sendJson(res, 200, { ok: true });
}

function handleMe(req: IncomingMessage, res: ServerResponse, opts: AuthHttpOptions): void {
  const token = extractBearerToken(req);
  if (!token) {
    sendUnauthorized(res);
    return;
  }

  const user = opts.userDb.validateSession(token);
  if (!user) {
    sendUnauthorized(res);
    return;
  }

  sendJson(res, 200, { user });
}

function extractBearerToken(req: IncomingMessage): string | null {
  const authHeader = req.headers.authorization;
  if (!authHeader) {
    return null;
  }
  const parts = authHeader.split(" ");
  if (parts.length !== 2 || parts[0].toLowerCase() !== "bearer") {
    return null;
  }
  const token = parts[1].trim();
  return token.length > 0 ? token : null;
}

/** Validate token and return authenticated user, or send 401 and return null. */
function requireAuth(req: IncomingMessage, res: ServerResponse, opts: AuthHttpOptions) {
  const token = extractBearerToken(req);
  if (!token) {
    sendUnauthorized(res);
    return null;
  }
  const user = opts.userDb.validateSession(token);
  if (!user) {
    sendUnauthorized(res);
    return null;
  }
  return user;
}

// ── Self-service endpoints ──────────────────────────────────────────

async function handleUpdateProfile(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
): Promise<void> {
  const user = requireAuth(req, res, opts);
  if (!user) {
    return;
  }

  const body = (await readJsonBodyOrError(req, res, MAX_BODY_BYTES)) as
    | { displayName?: string; email?: string }
    | undefined;
  if (!body) {
    return;
  }

  const fields: { displayName?: string; email?: string } = {};
  if (typeof body.displayName === "string" && body.displayName.trim()) {
    fields.displayName = body.displayName.trim();
  }
  if (typeof body.email === "string" && body.email.trim()) {
    if (!EMAIL_RE.test(body.email.trim())) {
      sendInvalidRequest(res, "Valid email is required");
      return;
    }
    // Check for email uniqueness
    const existing = opts.userDb.findByEmail(body.email.trim());
    if (existing && existing.id !== user.id) {
      sendJson(res, 409, {
        error: { message: "An account with this email already exists", type: "conflict" },
      });
      return;
    }
    fields.email = body.email.trim();
  }

  if (!fields.displayName && !fields.email) {
    sendInvalidRequest(res, "At least one field (displayName, email) required");
    return;
  }

  opts.userDb.updateProfile(user.id, fields);
  sendJson(res, 200, { ok: true });
}

async function handleChangePassword(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
): Promise<void> {
  const user = requireAuth(req, res, opts);
  if (!user) {
    return;
  }

  const body = (await readJsonBodyOrError(req, res, MAX_BODY_BYTES)) as
    | { currentPassword?: string; newPassword?: string }
    | undefined;
  if (!body) {
    return;
  }

  if (typeof body.currentPassword !== "string" || !body.currentPassword) {
    sendInvalidRequest(res, "Current password is required");
    return;
  }
  if (typeof body.newPassword !== "string" || body.newPassword.length < MIN_PASSWORD_LENGTH) {
    sendInvalidRequest(res, `New password must be at least ${MIN_PASSWORD_LENGTH} characters`);
    return;
  }

  // Verify current password
  const verified = opts.userDb.authenticateUser(user.email, body.currentPassword);
  if (!verified) {
    sendJson(res, 401, {
      error: { message: "Current password is incorrect", type: "unauthorized" },
    });
    return;
  }

  opts.userDb.changePassword(user.id, body.newPassword);
  sendJson(res, 200, { ok: true });
}

// ── Admin endpoints ─────────────────────────────────────────────────

function handleListUsers(req: IncomingMessage, res: ServerResponse, opts: AuthHttpOptions): void {
  const user = requireAuth(req, res, opts);
  if (!user) {
    return;
  }
  if (user.role !== "admin") {
    sendJson(res, 403, {
      error: { message: "Admin access required", type: "forbidden" },
    });
    return;
  }
  const users = opts.userDb.listUsers();
  sendJson(res, 200, { users });
}

async function handleAdminUpdateUser(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
  targetUserId: string,
): Promise<void> {
  const user = requireAuth(req, res, opts);
  if (!user) {
    return;
  }
  if (user.role !== "admin") {
    sendJson(res, 403, {
      error: { message: "Admin access required", type: "forbidden" },
    });
    return;
  }

  const body = (await readJsonBodyOrError(req, res, MAX_BODY_BYTES)) as
    | { role?: string }
    | undefined;
  if (!body) {
    return;
  }

  if (body.role !== undefined) {
    if (body.role !== "admin" && body.role !== "user") {
      sendInvalidRequest(res, 'role must be "admin" or "user"');
      return;
    }
    // Prevent admins from demoting themselves
    if (targetUserId === user.id && body.role !== "admin") {
      sendInvalidRequest(res, "Cannot demote yourself");
      return;
    }
    const updated = opts.userDb.updateUserRole(targetUserId, body.role);
    if (!updated) {
      sendJson(res, 404, {
        error: { message: "User not found", type: "not_found" },
      });
      return;
    }
  }

  sendJson(res, 200, { ok: true });
}

function handleAdminRevokeUserSessions(
  req: IncomingMessage,
  res: ServerResponse,
  opts: AuthHttpOptions,
  targetUserId: string,
): void {
  const user = requireAuth(req, res, opts);
  if (!user) {
    return;
  }
  if (user.role !== "admin") {
    sendJson(res, 403, {
      error: { message: "Admin access required", type: "forbidden" },
    });
    return;
  }

  const revoked = opts.userDb.revokeAllUserSessions(targetUserId);
  sendJson(res, 200, { ok: true, revokedCount: revoked });
}
