#!/bin/sh
# OFM Engine container entrypoint: migrate, then serve (or run the worker / any `ofm` command).
#   serve   (default) apply Alembic migrations, optionally seed the demo tenant, start uvicorn
#   worker            start the background job worker (run `serve` first so the schema exists)
#   <other>           passed straight to the `ofm` CLI, e.g. `docker run IMAGE gen-key`
set -eu

cmd="${1:-serve}"
if [ "$#" -gt 0 ]; then shift; fi

case "$cmd" in
  serve)
    if [ "${MIGRATE_ON_START:-true}" = "true" ]; then
      ofm migrate
    fi
    if [ "${SEED_DEMO:-false}" = "true" ]; then
      # demo only: `--known-passwords` is refused by the CLI when ENV=production
      # shellcheck disable=SC2086
      ofm seed-demo ${SEED_DEMO_ARGS:-}
    fi
    exec ofm serve --host 0.0.0.0 --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}" "$@"
    ;;
  worker)
    exec ofm worker "$@"
    ;;
  *)
    exec ofm "$cmd" "$@"
    ;;
esac
