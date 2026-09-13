/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        pilot: {
          bg: "#0b1120",
          panel: "#111827",
          border: "#1f2937",
          accent: "#38bdf8",
          success: "#34d399",
          warning: "#fbbf24",
          danger: "#f87171",
        },
      },
    },
  },
  plugins: [],
};
