"""The benchmark's own database.

The benchmark writes EUvsDisinfo narratives, fetch jobs and pages, which must never reach the product
database. `use_benchmark_database` runs before anything imports `app.config`: it points `DATABASE_URL` at
`BENCHMARK_DATABASE_URL`, so the in-process API, the job queue and the migrations all use the benchmark
database, and it refuses a benchmark URL that names the product's database.
"""

import os
from collections.abc import MutableMapping

from psycopg.conninfo import conninfo_to_dict

# The product database's name when DATABASE_URL is unset, as in app.config.
DEFAULT_DATABASE = "postgres"


def _database_name(url: str) -> str:
    return conninfo_to_dict(url).get("dbname") or DEFAULT_DATABASE


def use_benchmark_database(environ: MutableMapping[str, str] = os.environ) -> str:
    """Point DATABASE_URL at BENCHMARK_DATABASE_URL and return it.

    Raises SystemExit when BENCHMARK_DATABASE_URL is unset or names the same database as DATABASE_URL, or
    the product default when DATABASE_URL is unset.
    """
    benchmark = environ.get("BENCHMARK_DATABASE_URL")
    if not benchmark:
        raise SystemExit("set BENCHMARK_DATABASE_URL to the benchmark's own database")
    product = environ.get("DATABASE_URL")
    product_name = _database_name(product) if product else DEFAULT_DATABASE
    if _database_name(benchmark) == product_name:
        raise SystemExit(
            f"refusing to run: BENCHMARK_DATABASE_URL names the product database {product_name!r}"
        )
    environ["DATABASE_URL"] = benchmark
    return benchmark


def require_benchmark_database(database_url: str) -> None:
    """Raise SystemExit unless the app's database is the benchmark database.

    Guards a benchmark command started without `python -m app.eval.benchmark`.
    """
    if not database_url or database_url != os.environ.get("BENCHMARK_DATABASE_URL"):
        raise SystemExit("run the benchmark through python -m app.eval.benchmark with BENCHMARK_DATABASE_URL")
