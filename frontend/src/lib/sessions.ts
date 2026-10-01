import type { CartSessionDetail, CartSessionSummary } from "../types";

export function toSessionSummary(detail: CartSessionDetail): CartSessionSummary {
  const { lines, ...summary } = detail;
  return summary;
}

export function upsertSessionSummary(
  sessions: CartSessionSummary[],
  summary: CartSessionSummary,
): CartSessionSummary[] {
  const remaining = sessions.filter((session) => session.session_id !== summary.session_id);
  return [summary, ...remaining];
}

export function selectSessionId(
  sessions: CartSessionSummary[],
  selectedSessionId: string,
  preferredSessionId?: string,
): string {
  for (const sessionId of [preferredSessionId, selectedSessionId]) {
    if (sessionId && sessions.some((session) => session.session_id === sessionId)) {
      return sessionId;
    }
  }
  return sessions[0].session_id;
}
