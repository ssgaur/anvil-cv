/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Re-map the `neutral-*` scale to an INVERTED slate-blue palette.
        // bg-neutral-950 (was darkest) -> lightest near-white blue (page bg)
        // bg-neutral-900 / 800        -> light bluish surfaces (cards, inputs)
        // text-neutral-100 / 200 / 300 (was light-on-dark) -> dark slate text
        // muted 400/500 stay muted but cooler
        neutral: {
          50:  "#0b1220", // darkest text
          100: "#0f172a",
          200: "#1e293b",
          300: "#334155",
          400: "#475569",
          500: "#64748b", // muted (mid)
          600: "#94a3b8",
          700: "#cbd5e1", // borders
          800: "#e2ebf7", // raised surface (inputs, hover)
          900: "#eff4ff", // cards / sidebars
          950: "#f6f9ff", // page background
        },
        // Re-map accent: was emerald-green, now bright sky/indigo blue.
        emerald: {
          400: "#3b82f6", // hover (blue-500)
          500: "#2563eb", // primary (blue-600)
          600: "#1d4ed8",
          950: "#ffffff", // text-on-accent (was near-black)
        },
      },
    },
  },
  plugins: [],
};
