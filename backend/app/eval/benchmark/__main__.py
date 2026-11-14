"""python -m app.eval.benchmark {download,import,run} [options]; see README.md."""

import sys

from app.eval.benchmark.database import use_benchmark_database

COMMANDS = ("download", "import", "run")

if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
    raise SystemExit(f"usage: python -m app.eval.benchmark {{{','.join(COMMANDS)}}} [options]")
command = sys.argv.pop(1)
if command == "download":
    from app.eval.benchmark.download import main
else:
    # Before app.config is imported, so every connection goes to the benchmark database.
    use_benchmark_database()
    if command == "import":
        from app.eval.benchmark.cases import main
    else:
        from app.eval.benchmark.run import main
main()
