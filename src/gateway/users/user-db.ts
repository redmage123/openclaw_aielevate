import { randomUUID } from "node:crypto";
import { requireNodeSqlite } from "../../memory/sqlite.js";
import { hashPassword, verifyPassword } from "./password.js";
import { generateSessionToken, hashSessionToken } from "./session-token.js";
import type { AuthUser, CreateUserParams, User } from "./types.js";
import { ensureUserDbSchema } from "./user-schema.js";

const DEFAULT_SESSION_LIFETIME_HOURS = 720; // 30 days
const DEFAULT_MAX_SESSIONS_PER_USER = 10;

export type UserDbOptions = {
  dbPath: string;
  sessionLifetimeHours?: number;
  maxSessionsPerUser?: number;
  adminEmails?: string[];
};

export class UserDb {
  private db: import("node:sqlite").DatabaseSync;
  private sessionLifetimeHours: number;
  private maxSessionsPerUser: number;
  private adminEmails: Set<string>;

  constructor(opts: UserDbOptions) {
    const { DatabaseSync } = requireNodeSqlite();
    this.db = new DatabaseSync(opts.dbPath);
    this.sessionLifetimeHours = opts.sessionLifetimeHours ?? DEFAULT_SESSION_LIFETIME_HOURS;
    this.maxSessionsPerUser = opts.maxSessionsPerUser ?? DEFAULT_MAX_SESSIONS_PER_USER;
    this.adminEmails = new Set((opts.adminEmails ?? []).map((e) => e.toLowerCase().trim()));
    ensureUserDbSchema(this.db);
  }

  /** Create a new user. First user gets admin role. */
  createUser(params: CreateUserParams): AuthUser {
    const username = params.username.trim();
    const email = params.email.toLowerCase().trim();
    const now = Date.now();
    const id = randomUUID();
    const passwordHashValue = hashPassword(params.password);

    const isFirstUser = this.countUsers() === 0;
    const isAdminEmail = this.adminEmails.has(email);
    const role = isFirstUser || isAdminEmail ? "admin" : "user";

    this.db
      .prepare(
        `INSERT INTO users (id, username, email, display_name, password_hash, role, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(id, username, email, params.displayName.trim(), passwordHashValue, role, now, now);

    return { id, username, email, displayName: params.displayName.trim(), role };
  }

  /** Find a user by email (case-insensitive). */
  findByEmail(email: string): User | null {
    const row = this.db
      .prepare(`SELECT * FROM users WHERE email = ?`)
      .get(email.toLowerCase().trim()) as Record<string, unknown> | undefined;
    if (!row) {
      return null;
    }
    return rowToUser(row);
  }

  /** Find a user by username (case-insensitive). */
  findByUsername(username: string): User | null {
    const row = this.db.prepare(`SELECT * FROM users WHERE username = ?`).get(username.trim()) as
      | Record<string, unknown>
      | undefined;
    if (!row) {
      return null;
    }
    return rowToUser(row);
  }

  /** Find a user by username or email (case-insensitive). */
  findByUsernameOrEmail(identifier: string): User | null {
    const trimmed = identifier.trim();
    // If it looks like an email, try email first
    if (trimmed.includes("@")) {
      return this.findByEmail(trimmed) ?? this.findByUsername(trimmed);
    }
    return this.findByUsername(trimmed) ?? this.findByEmail(trimmed);
  }

  /** Verify password and return user, or null on mismatch. Accepts username or email. */
  authenticateUser(identifier: string, password: string): AuthUser | null {
    const user = this.findByUsernameOrEmail(identifier);
    if (!user) {
      return null;
    }
    if (!verifyPassword(password, user.passwordHash)) {
      return null;
    }
    return {
      id: user.id,
      username: user.username,
      email: user.email,
      displayName: user.displayName,
      role: user.role,
    };
  }

  /**
   * Create a session for a user. Returns the raw token (stored hashed).
   * Enforces max sessions per user by revoking the oldest active session.
   */
  createSession(
    userId: string,
    opts?: { userAgent?: string; ipAddress?: string },
  ): { token: string; expiresAt: number } {
    this.enforceMaxSessions(userId);

    const token = generateSessionToken();
    const tokenHash = hashSessionToken(token);
    const now = Date.now();
    const expiresAt = now + this.sessionLifetimeHours * 60 * 60 * 1000;
    const id = randomUUID();

    this.db
      .prepare(
        `INSERT INTO user_sessions (id, user_id, token_hash, created_at, expires_at, user_agent, ip_address)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(id, userId, tokenHash, now, expiresAt, opts?.userAgent ?? null, opts?.ipAddress ?? null);

    return { token, expiresAt };
  }

  /** Validate a session token. Returns the user if valid. */
  validateSession(token: string): AuthUser | null {
    const tokenHash = hashSessionToken(token);
    const now = Date.now();
    const row = this.db
      .prepare(
        `SELECT u.id, u.username, u.email, u.display_name, u.role
         FROM user_sessions s
         JOIN users u ON s.user_id = u.id
         WHERE s.token_hash = ? AND s.expires_at > ? AND s.revoked_at IS NULL`,
      )
      .get(tokenHash, now) as Record<string, unknown> | undefined;
    if (!row) {
      return null;
    }
    return {
      id: row.id as string,
      username: row.username as string,
      email: row.email as string,
      displayName: row.display_name as string,
      role: row.role as string,
    } as AuthUser;
  }

  /** Revoke a session by raw token. */
  revokeSession(token: string): boolean {
    const tokenHash = hashSessionToken(token);
    const now = Date.now();
    const result = this.db
      .prepare(
        `UPDATE user_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL`,
      )
      .run(tokenHash, now);
    return (result as unknown as { changes: number }).changes > 0;
  }

  /** Count total users. */
  countUsers(): number {
    const row = this.db.prepare(`SELECT COUNT(*) as count FROM users`).get() as {
      count: number;
    };
    return row.count;
  }

  /** Remove expired sessions. */
  pruneExpiredSessions(): number {
    const now = Date.now();
    const result = this.db
      .prepare(`DELETE FROM user_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL`)
      .run(now);
    return (result as unknown as { changes: number }).changes;
  }

  close(): void {
    this.db.close();
  }

  private enforceMaxSessions(userId: string): void {
    const activeSessions = this.db
      .prepare(
        `SELECT id, created_at FROM user_sessions
         WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
         ORDER BY created_at ASC`,
      )
      .all(userId, Date.now()) as Array<{ id: string; created_at: number }>;

    // Revoke oldest sessions to make room (keep max - 1 to leave room for the new one)
    const excess = activeSessions.length - (this.maxSessionsPerUser - 1);
    if (excess > 0) {
      const now = Date.now();
      for (let i = 0; i < excess; i++) {
        this.db
          .prepare(`UPDATE user_sessions SET revoked_at = ? WHERE id = ?`)
          .run(now, activeSessions[i].id);
      }
    }
  }
}

function rowToUser(row: Record<string, unknown>): User {
  return {
    id: row.id as string,
    username: row.username as string,
    email: row.email as string,
    displayName: row.display_name as string,
    passwordHash: row.password_hash as string,
    role: row.role as string,
    createdAt: row.created_at as number,
    updatedAt: row.updated_at as number,
  } as User;
}
