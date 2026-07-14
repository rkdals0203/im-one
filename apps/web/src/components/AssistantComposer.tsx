import { ArrowUp, LoaderCircle } from "lucide-react";
import { FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { useAppState } from "../app-state";
import type { Workspace } from "../types";

const routeByWorkspace: Record<Workspace, string> = {
  knowledge: "/knowledge",
  data: "/data",
  expense: "/expenses",
  clarification: "/",
};

export function AssistantComposer({
  hint,
  placeholder,
  initialValue = "",
  autoFocus = false,
  large = false,
}: {
  hint?: Exclude<Workspace, "clarification">;
  placeholder: string;
  initialValue?: string;
  autoFocus?: boolean;
  large?: boolean;
}) {
  const [value, setValue] = useState(initialValue);
  const { ask, loading } = useAppState();
  const navigate = useNavigate();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const message = value.trim();
    if (!message || loading) return;
    try {
      const result = await ask(message, hint);
      navigate(routeByWorkspace[result.workspace]);
      if (hint) setValue("");
    } catch {
      // The shared state displays the actionable error.
    }
  }

  return (
    <form className={`assistant-composer ${large ? "is-large" : ""}`} onSubmit={submit}>
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        rows={large ? 3 : 2}
        maxLength={2000}
        autoFocus={autoFocus}
        aria-label="질문 입력"
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
      />
      <button type="submit" className="composer-submit" disabled={!value.trim() || loading} aria-label="질문 실행">
        {loading ? <LoaderCircle className="spin" size={18} /> : <ArrowUp size={18} />}
      </button>
    </form>
  );
}
