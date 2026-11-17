/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        washi: '#f7f4ee',
        paper: '#fffdf9',
        ink: '#25312e',
        'ink-muted': '#66716d',
        'ink-faint': '#8b9490',
        line: '#dedbd2',
        forest: '#315f53',
        'forest-soft': '#e5efeb',
        vermilion: '#d76b4d',
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
