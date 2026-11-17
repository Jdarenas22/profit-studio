#!/usr/bin/env bash
# Compila Tailwind (v3.4, versión fija) a static/css/app.css.
# Uso: scripts/build-css.sh [--watch]
# Requiere Node/npx solo en el equipo local; Railway usa el CSS ya compilado que está en git.
set -euo pipefail
TAILWIND_VERSION="3.4.19"
cd "$(dirname "$0")/.."

args=(-c tailwind.config.js -i frontend/tailwind.css -o static/css/app.css --minify)
if [[ "${1:-}" == "--watch" ]]; then
  args+=(--watch)
fi

npx --yes "tailwindcss@${TAILWIND_VERSION}" "${args[@]}"
