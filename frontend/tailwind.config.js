/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 950: '#07080c', 900: '#0c0e14', 850: '#11141c', 800: '#161a24', 700: '#222838', 600: '#323a4f' },
        brand: { 300: '#9ad8ff', 400: '#5cc2ff', 500: '#2fa8f5', 600: '#1786d6' },
        accent: { 400: '#b79cff', 500: '#9a76ff' },
      },
      fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'] },
      keyframes: { flash: { '0%': { backgroundColor: 'rgba(92,194,255,0.18)' }, '100%': { backgroundColor: 'transparent' } } },
      animation: { flash: 'flash 1.2s ease-out' },
    },
  },
  plugins: [],
};
