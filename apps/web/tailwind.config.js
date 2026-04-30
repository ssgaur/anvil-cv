/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Re-map the `neutral-*` scale to an INVERTED warm-green palette.
        // bg-neutral-950 (normally darkest) -> soft garden page background.
        // bg-neutral-900 / 800          -> friendly raised surfaces.
        // text-neutral-100 / 200 / 300  -> deep readable green-gray text.
        neutral: {
          50:  "#07170f",
          100: "#10251a",
          200: "#1f2f27",
          300: "#31483b",
          400: "#52685b",
          500: "#728277",
          600: "#9fb3a6",
          700: "#cfe0d4",
          800: "#e8f5eb",
          900: "#f7fff8",
          950: "#f1fbf3",
        },
        // Bright but natural green: primary actions should feel friendly, not technical.
        emerald: {
          400: "#22c55e",
          500: "#16a34a",
          600: "#15803d",
          950: "#052e16",
        },
      },
    },
  },
  plugins: [],
};
