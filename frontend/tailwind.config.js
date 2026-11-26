/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        washi: '#f7f4ee',
        paper: '#fffdf9',
        ink: '#25312e',
        // Both secondary tones clear WCAG AA (4.5:1) on washi and paper:
        // ink-muted 6.45:1, ink-faint 4.65:1. ink-faint was #8b9490 (2.84:1).
        'ink-muted': '#4f5b57',
        'ink-faint': '#67706c',
        line: '#dedbd2',
        forest: '#315f53',
        'forest-soft': '#e5efeb',
        // Deep terracotta: 5.50:1 against white (buttons) and 5.01:1 on washi
        // (accent text). The previous #d76b4d failed both at 3.44:1 / 3.14:1.
        vermilion: '#ad4b2b',
        'vermilion-deep': '#8f3d22',
        'vermilion-soft': '#f9e9e2',
        danger: '#a8493d',
        'danger-soft': '#fbecea',
        gold: '#b8873d',
      },
      fontFamily: {
        sans: ['Inter', 'Pretendard', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'sans-serif'],
        'serif-jp': ['"Yu Mincho"', '"Hiragino Mincho ProN"', '"Noto Serif JP"', 'serif'],
      },
      boxShadow: {
        soft: '0 8px 24px rgba(37, 49, 46, 0.08)',
        card: '0 20px 60px rgba(37, 49, 46, 0.11)',
      },
    },
  },
  plugins: [],
}
