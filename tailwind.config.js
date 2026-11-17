/**
 * Tailwind CSS v3.4. Se compila en local con scripts/build-css.(ps1|sh) y el resultado
 * (static/css/app.css) se guarda en git. No hay package.json a propósito: Railway (nixpacks)
 * detectaría Node y cambiaría el build.
 */
module.exports = {
  content: [
    './templates/**/*.html',
    // Vistas que devuelven fragmentos HTML con clases (p. ej. errores HTMX en apps/routines/views.py)
    './apps/**/*.py',
    './static/**/*.js',
  ],
  // Clases que solo existen al renderizar la plantilla, nunca completas en el código fuente.
  safelist: [
    // templates/public/plans.html: md:grid-cols-{% if ... %}1{% elif ... %}2{% else %}3{% endif %}
    'md:grid-cols-1',
    'md:grid-cols-2',
    'md:grid-cols-3',
  ],
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: '#FF6B00', dark: '#E05A00', light: '#FF8C33' },
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
