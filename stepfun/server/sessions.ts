import crypto from "node:crypto";
import type { SessionState } from "./types.ts";

export class SessionStore {
  private readonly sessions = new Map<string, SessionState>();

  getOrCreate(sid: string | undefined): SessionState {
    if (sid && this.sessions.has(sid)) return this.sessions.get(sid)!;
    const id = sid && /^[a-f0-9-]{16,64}$/.test(sid) ? sid : crypto.randomUUID();
    const session: SessionState = { id, activeKbId: null, createdAt: Date.now() };
    this.sessions.set(id, session);
    return session;
  }

  setActive(sid: string, kbId: string | null): void {
    const session = this.sessions.get(sid);
    if (session) session.activeKbId = kbId;
  }

  clearKb(kbId: string): void {
    for (const session of this.sessions.values()) {
      if (session.activeKbId === kbId) session.activeKbId = null;
    }
  }

  get size(): number {
    return this.sessions.size;
  }
}
