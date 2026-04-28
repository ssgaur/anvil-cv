import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";

/**
 * Forgot-password screen. Always shows the same "check your inbox" message —
 * never reveals whether the email maps to an account.
 */
export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await api.passwordResetRequest(email);
    } catch {
      // Intentionally ignore — the API returns 204 even on rate-limit so the
      // user always sees the same outcome.
    } finally {
      setBusy(false);
      setSent(true);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-neutral-950 text-neutral-100">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 p-6 bg-neutral-900 rounded">
        <h1 className="text-xl font-semibold">Forgot password</h1>
        {sent ? (
          <div className="text-sm text-neutral-300 space-y-3">
            <p>
              If <span className="font-mono">{email}</span> is registered, a
              reset link has been sent. The link expires in a few minutes.
            </p>
            <p className="text-neutral-500 text-xs">
              In dev, the link is printed to the API logs.
            </p>
            <Link to="/login" className="text-emerald-400 hover:underline">Back to sign in</Link>
          </div>
        ) : (
          <>
            <label className="block">
              <span className="text-sm text-neutral-400">Email</span>
              <input
                type="email" required value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded bg-neutral-800 px-3 py-2 text-sm"
              />
            </label>
            <button
              type="submit" disabled={busy}
              className="w-full rounded bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
            >
              {busy ? "Sending…" : "Send reset link"}
            </button>
            <Link to="/login" className="block text-sm text-neutral-400 hover:underline text-center">
              Back to sign in
            </Link>
          </>
        )}
      </form>
    </div>
  );
}
