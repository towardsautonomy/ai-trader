import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#050807",
        panel: "#090d0b",
        raised: "#0e1411",
        line: "#1c2a22",
        "line-hi": "#2f4a3b",
        fg: "#b9d8c4",
        dim: "#6f8f7c",
        faint: "#47604f",
        phos: "#3dff8b",
        amber: "#ffb627",
        danger: "#ff4d4d",
        cyan: "#4fd6ff",
      },
      fontFamily: {
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "2xs": ["10px", "14px"],
        xs: ["11px", "16px"],
        sm: ["12px", "18px"],
        base: ["13px", "20px"],
      },
      keyframes: {
        blink: { "0%, 49%": { opacity: "1" }, "50%, 100%": { opacity: "0" } },
      },
      animation: {
        blink: "blink 1.1s step-end infinite",
      },
    },
  },
  plugins: [],
};

export default config;
