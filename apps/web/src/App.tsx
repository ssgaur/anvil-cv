import { useEffect, useState } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export default function App() {
  const [health, setHealth] = useState<string>("checking…");

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then((r) => r.json())
      .then((d) => setHealth(`${d.status} (v${d.version})`))
      .catch(() => setHealth("unreachable"));
  }, []);

  return (
    <main className="min-h-screen flex flex-col items-center justify-center bg-neutral-950 text-neutral-100 font-sans">
      <h1 className="text-5xl font-bold tracking-tight">⚒ Anvil</h1>
      <p className="mt-3 text-neutral-400">
        Open-source image annotation with built-in SAM2 fine-tuning.
      </p>
      <p className="mt-8 text-sm text-neutral-500">
        api: <code className="text-emerald-400">{health}</code>
      </p>
    </main>
  );
}
