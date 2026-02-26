export type UserRole = "admin" | "user";

export type User = {
  id: string;
  username: string;
  email: string;
  displayName: string;
  passwordHash: string;
  role: UserRole;
  createdAt: number;
  updatedAt: number;
};

export type UserSession = {
  id: string;
  userId: string;
  tokenHash: string;
  createdAt: number;
  expiresAt: number;
  revokedAt: number | null;
  userAgent: string | null;
  ipAddress: string | null;
};

export type AuthUser = {
  id: string;
  username: string;
  email: string;
  displayName: string;
  role: UserRole;
};

export type CreateUserParams = {
  username: string;
  email: string;
  password: string;
  displayName: string;
};
