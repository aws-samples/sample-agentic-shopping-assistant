import { COPY } from "~/utils/copy";

/** A row of `GET /v1/sessions`. `createdAt` is optional: the API sends it only when Memory reports it. */
export interface SessionSummary {
  session_id: string;
  createdAt?: string | number;
}

/** Keep the rows with a session ID; drop anything malformed. */
export function normalizeSessions(value: unknown): SessionSummary[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((row): SessionSummary[] => {
    if (typeof row !== "object" || row === null) return [];
    const { session_id: id, createdAt } = row as Record<string, unknown>;
    if (typeof id !== "string" || !id) return [];
    return [typeof createdAt === "string" || typeof createdAt === "number" ? { session_id: id, createdAt } : { session_id: id }];
  });
}

function parseDate(value: string | number | undefined): Date | undefined {
  if (value === undefined || value === "") return undefined;
  // Epoch seconds or milliseconds, or an ISO 8601 string.
  const date = typeof value === "number" ? new Date(value < 1e12 ? value * 1000 : value) : new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date;
}

/**
 * A row's title: when the conversation started, in Korean ("11월 19일 오후 3:05", with the
 * year if it isn't this year), or "대화 N" numbered from the oldest row when the API doesn't
 * say. IDs aren't titles: every ID of a shopper starts with the same actor ID.
 */
export function sessionTitle(session: SessionSummary, index: number, total: number, now = new Date()): string {
  const created = parseDate(session.createdAt);
  if (!created) return COPY.history.untitled(total - index);
  return new Intl.DateTimeFormat("ko-KR", {
    ...(created.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(created);
}
