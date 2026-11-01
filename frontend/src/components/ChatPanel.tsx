import { useEffect, useRef, useState, type FormEvent } from "react";
import type { ChatMessage } from "../types";
import { Button } from "../ui";

const DEMO = "I just moved into a 2BHK apartment. My furnishing budget is ₹2 lakh. I prefer minimalist furniture. Help me furnish the entire apartment.";
const SUGGESTIONS_NO_PLAN = [DEMO];
const SUGGESTIONS_PLAN = ["Why this sofa?", "How much is left?", "Show bedroom 2", "I don't need a TV unit", "Increase the budget to 3 lakh"];

interface Props {
  history: ChatMessage[];
  hasPlan: boolean;
  busy: boolean;
  onSend: (text: string) => void;
}

export function ChatPanel({ history, hasPlan, busy, onSend }: Props) {
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [history.length, busy]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  };
  const suggestions = hasPlan ? SUGGESTIONS_PLAN : SUGGESTIONS_NO_PLAN;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="scroll-thin min-h-0 flex-1 space-y-3 overflow-y-auto p-4" role="log" aria-live="polite" aria-label="Conversation">
        {history.length === 0 && (
          <div className="rounded-xl bg-brand-soft p-4 text-sm text-brand-dark">
            <p className="font-medium">Hi, I'm SmartNest.</p>
            <p className="mt-1">Tell me about your home and your budget and I'll plan every room — with real products, honest scores and prices you can check.</p>
          </div>
        )}
        {history.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[92%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm leading-relaxed ${
              m.role === "user" ? "bg-brand text-white" : m.kind === "error" ? "bg-bad-soft text-bad" : "bg-white border border-line"}`}>
              {m.text}
            </div>
          </div>
        ))}
        {busy && <div className="text-sm text-muted" role="status">SmartNest is thinking…</div>}
        <div ref={end} />
      </div>
      <div className="border-t border-line p-3">
        <div className="mb-2 flex gap-1.5 overflow-x-auto pb-1">
          {suggestions.map((s) => (
            <button key={s} disabled={busy} onClick={() => onSend(s)}
              className="shrink-0 rounded-full border border-line bg-white px-3 py-1 text-xs text-muted hover:border-brand hover:text-brand-dark" title={s}>
              {s.length > 42 ? "Try the 2BHK example" : s}
            </button>
          ))}
        </div>
        <form onSubmit={submit} className="flex gap-2">
          <label htmlFor="chat-input" className="sr-only">Message SmartNest</label>
          <input id="chat-input" value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} autoComplete="off"
            placeholder={hasPlan ? "Ask or change something…" : "Describe your home and budget…"}
            className="min-w-0 flex-1 rounded-lg border border-line bg-white px-3 py-2 text-sm placeholder:text-stone-400" />
          <Button type="submit" variant="primary" disabled={busy || !text.trim()}>Send</Button>
        </form>
      </div>
    </div>
  );
}
