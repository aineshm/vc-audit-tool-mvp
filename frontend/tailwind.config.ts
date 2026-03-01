import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        sand: {
          50: "#faf8f2",
          100: "#f2efe5",
          200: "#e8e3d2",
          300: "#d8d4c3",
        },
        ink: {
          DEFAULT: "#142321",
          light: "#4d615e",
        },
        teal: {
          50: "#e8f4f3",
          100: "#b3dedd",
          300: "#4db8b6",
          500: "#0d7a78",
          700: "#0a5e5d",
          900: "#073d3c",
        },
        warm: {
          50: "#fef4ee",
          100: "#f5e3d9",
          300: "#e8b894",
          500: "#c85e28",
          700: "#8f3f1a",
        },
      },
      fontFamily: {
        sans: ["Space Grotesk", "Avenir Next", "Segoe UI", "sans-serif"],
        mono: ["IBM Plex Mono", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
