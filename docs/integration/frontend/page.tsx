"use client";

/**
 * paper_rag workspace page (M10.3 — promoted from P3-14 placeholder).
 *
 * Three tabs:
 *   - Inbox        — daily digest / sub_match / stale / auto_ingest cards
 *   - Subscriptions — keyword subs (low / normal / high strength)
 *   - Ask          — quick QA input that POSTs /api/paper_rag/qa/sync
 *
 * Wired purely with fetch — no extra client-side deps. Auth cookie is
 * propagated via `credentials: "include"`.
 */

import { useEffect, useState } from "react";

type InboxItem = {
  id: number;
  kind: string;
  title: string;
  body_md?: string;
  created_at?: number;
  read_at?: number | null;
};

type Subscription = {
  id: number;
  kind: string;
  value: string;
  strength: string;
  enabled?: number;
};

type QASyncResponse = {
  answer: string;
  citations: string[];
  abstain: { decision?: string };
  trace_id?: string;
  n_chunks?: number;
};

const GATEWAY = process.env.NEXT_PUBLIC_GATEWAY_URL ?? "";
const TABS = ["inbox", "subscriptions", "ask"] as const;
type Tab = (typeof TABS)[number];

async function fetchJson<T>(
  path: string,
  init?: RequestInit,
): Promise<T | null> {
  try {
    const r = await fetch(`${GATEWAY}${path}`, {
      credentials: "include",
      ...init,
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export default function PaperRagPage() {
  const [tab, setTab] = useState<Tab>("inbox");
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [subs, setSubs] = useState<Subscription[]>([]);
  const [unread, setUnread] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Ask tab state
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<QASyncResponse | null>(null);
  const [asking, setAsking] = useState(false);

  // New subscription input
  const [newSub, setNewSub] = useState("");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [ix, ss] = await Promise.all([
        fetchJson<{ items: InboxItem[]; unread_count: number }>(
          "/api/paper_rag/inbox?unread_only=false&limit=50",
        ),
        fetchJson<Subscription[]>("/api/paper_rag/subscriptions"),
      ]);
      if (cancelled) return;
      if (!ix) setError("Failed to load inbox (auth required?)");
      else {
        setInbox(ix.items ?? []);
        setUnread(ix.unread_count ?? 0);
      }
      if (ss) setSubs(ss);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleAsk() {
    if (!question.trim()) return;
    setAsking(true);
    setAnswer(null);
    const r = await fetchJson<QASyncResponse>("/api/paper_rag/qa/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    setAsking(false);
    if (r) setAnswer(r);
    else setError("QA call failed");
  }

  async function addSub() {
    if (!newSub.trim()) return;
    const r = await fetchJson<Subscription>("/api/paper_rag/subscriptions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: "keyword",
        value: newSub.trim(),
        strength: "normal",
      }),
    });
    if (r) {
      setSubs((s) => [...s, r]);
      setNewSub("");
    }
  }

  return (
    <div
      style={{
        maxWidth: 920,
        margin: "32px auto",
        padding: "0 16px",
        fontFamily: "system-ui",
      }}
    >
      <h1 style={{ fontSize: 28, marginBottom: 4 }}>paper_rag</h1>
      <p style={{ color: "#666", marginBottom: 16 }}>
        Agentic RAG over your indexed papers · {unread} unread
      </p>

      <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: "6px 14px",
              border: "1px solid #ddd",
              background: tab === t ? "#4a90e2" : "white",
              color: tab === t ? "white" : "#333",
              cursor: "pointer",
              borderRadius: 4,
              textTransform: "capitalize",
              fontSize: 14,
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {error && (
        <div
          style={{
            padding: 12,
            background: "#fee",
            border: "1px solid #f99",
            marginBottom: 16,
          }}
        >
          {error}
        </div>
      )}

      {tab === "inbox" && (
        <section>
          {inbox.length === 0 ? (
            <p style={{ color: "#999" }}>
              No items. Subscribe to a topic and the daily digest will land here.
            </p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0 }}>
              {inbox.map((it) => (
                <li
                  key={it.id}
                  style={{
                    border: "1px solid #ddd",
                    borderLeft: it.read_at
                      ? "1px solid #ddd"
                      : "4px solid #4a90e2",
                    padding: 12,
                    marginBottom: 8,
                    borderRadius: 4,
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      color: "#999",
                      textTransform: "uppercase",
                    }}
                  >
                    {it.kind}
                  </div>
                  <div style={{ fontWeight: 600 }}>{it.title}</div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {tab === "subscriptions" && (
        <section>
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <input
              type="text"
              placeholder="Add keyword (e.g. retrieval-augmented generation)"
              value={newSub}
              onChange={(e) => setNewSub(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && addSub()}
              style={{
                flex: 1,
                padding: 8,
                border: "1px solid #ddd",
                borderRadius: 4,
              }}
            />
            <button
              onClick={addSub}
              style={{
                padding: "8px 16px",
                background: "#4a90e2",
                color: "white",
                border: "none",
                borderRadius: 4,
                cursor: "pointer",
              }}
            >
              Subscribe
            </button>
          </div>
          {subs.length === 0 ? (
            <p style={{ color: "#999" }}>No subscriptions yet.</p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0 }}>
              {subs.map((s) => (
                <li
                  key={s.id}
                  style={{
                    border: "1px solid #ddd",
                    padding: 8,
                    marginBottom: 6,
                    borderRadius: 4,
                    display: "flex",
                    justifyContent: "space-between",
                  }}
                >
                  <span>
                    <code
                      style={{
                        background: "#f4f4f4",
                        padding: "1px 4px",
                      }}
                    >
                      {s.kind}
                    </code>{" "}
                    · <strong>{s.value}</strong>
                  </span>
                  <span style={{ color: "#666", fontSize: 12 }}>
                    strength: {s.strength}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {tab === "ask" && (
        <section>
          <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <input
              type="text"
              placeholder="Ask anything about your indexed papers"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAsk()}
              style={{
                flex: 1,
                padding: 8,
                border: "1px solid #ddd",
                borderRadius: 4,
              }}
            />
            <button
              onClick={handleAsk}
              disabled={asking}
              style={{
                padding: "8px 16px",
                background: asking ? "#999" : "#4a90e2",
                color: "white",
                border: "none",
                borderRadius: 4,
                cursor: asking ? "not-allowed" : "pointer",
              }}
            >
              {asking ? "Thinking…" : "Ask"}
            </button>
          </div>
          {answer && (
            <div
              style={{
                border: "1px solid #ddd",
                padding: 16,
                borderRadius: 4,
              }}
            >
              <div
                style={{
                  fontSize: 12,
                  color: "#999",
                  marginBottom: 8,
                  textTransform: "uppercase",
                }}
              >
                abstain decision: {answer.abstain?.decision ?? "n/a"} · chunks:{" "}
                {answer.n_chunks ?? 0} · trace: {answer.trace_id?.slice(0, 8)}
              </div>
              <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.6 }}>
                {answer.answer}
              </div>
              {answer.citations.length > 0 && (
                <div style={{ marginTop: 12, fontSize: 12, color: "#666" }}>
                  Citations: {answer.citations.join(", ")}
                </div>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
