#!/usr/bin/env python3
"""Load community quack_flamegraph and write parquet for the perf-demo notebook.

Default input is the live Spur→ade-bench folded capture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
FOLDED = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "test/data/spur-ade-bench.folded")
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "build/perf-demo")


def short_sql(src: str, alias: str, n: int = 48) -> str:
    return f"""
    CASE
      WHEN len({src}) > {n} THEN '...' || {src}[-{n - 1}:]
      ELSE {src}
    END AS {alias}
    """


def main() -> None:
    if not FOLDED.exists():
        sys.exit(f"missing folded profile: {FOLDED}")
    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL quack_flamegraph FROM community")
    con.execute("LOAD quack_flamegraph")
    path = str(FOLDED)
    total = con.execute(
        f"SELECT SUM(samples)::BIGINT FROM read_folded('{path}')"
    ).fetchone()[0]
    print("duckdb", duckdb.__version__, "samples", total, "folded", FOLDED.name)

    def copy(name: str, sql: str) -> None:
        con.execute(f"COPY ({sql}) TO '{OUT / name}' (FORMAT PARQUET)")

    tidy = (
        "regexp_replace(regexp_replace({col}, '^[^`]*`', ''), '^spur_graph::', '')"
    )

    copy(
        "overview.parquet",
        f"""
        SELECT SUM(samples)::BIGINT AS samples, COUNT(*)::BIGINT AS stacks,
               round(avg(len(frames)), 1) AS avg_depth, max(len(frames)) AS max_depth
        FROM read_folded('{path}')
        """,
    )
    copy(
        "coverage.parquet",
        f"""
        SELECT {short_sql('s_label', 'frame')}, coverage, n_stacks,
               round(100.0 * coverage / {total}, 2) AS pct
        FROM (
          SELECT {tidy.format(col='frame')} AS s_label, coverage, n_stacks
          FROM flamegraph_coverage('{path}')
          WHERE coverage < {total} * 0.80
            AND frame NOT ILIKE '%lang_start%'
            AND frame NOT ILIKE '%dyld%'
            AND frame NOT ILIKE '%backtrace%'
            AND frame NOT ILIKE 'main'
            AND frame NOT ILIKE 'spur'
            AND frame NOT ILIKE 'start'
        )
        ORDER BY coverage DESC LIMIT 14
        """,
    )
    copy(
        "inflate.parquet",
        f"""
        SELECT {short_sql('s_label', 'frame', 56)}, coverage, inclusive, inflate,
               coverage_pct, inclusive_pct
        FROM (
          SELECT {tidy.format(col='c.frame')} AS s_label,
                 c.coverage, i.inclusive,
                 round(i.inclusive * 1.0 / nullif(c.coverage, 0), 2) AS inflate,
                 round(100.0 * c.coverage / {total}, 2) AS coverage_pct,
                 round(100.0 * i.inclusive / {total}, 2) AS inclusive_pct
          FROM flamegraph_coverage('{path}') c
          JOIN flamegraph_inclusive('{path}') i USING (frame)
          WHERE c.coverage >= {total} * 0.01 AND c.coverage < {total} * 0.80
            AND i.inclusive * 1.0 / nullif(c.coverage, 0) >= 1.05
        )
        ORDER BY inflate DESC, coverage DESC LIMIT 10
        """,
    )
    copy(
        "exclusive.parquet",
        f"""
        SELECT {short_sql('s_label', 'leaf')}, exclusive,
               round(100.0 * exclusive / {total}, 2) AS pct
        FROM (
          SELECT {tidy.format(col='leaf')} AS s_label, exclusive
          FROM flamegraph_exclusive('{path}')
        )
        ORDER BY exclusive DESC LIMIT 12
        """,
    )
    copy(
        "edges.parquet",
        f"""
        SELECT {short_sql('s_parent', 'parent', 36)}, {short_sql('s_child', 'child', 36)},
               samples, round(100.0 * samples / {total}, 2) AS pct
        FROM (
          SELECT {tidy.format(col='parent')} AS s_parent,
                 {tidy.format(col='child')} AS s_child,
                 samples
          FROM flamegraph_edges('{path}')
          WHERE parent <> child AND samples < {total} * 0.40
            AND parent NOT ILIKE '%thread_start%'
            AND child NOT ILIKE '%thread_start%'
        )
        ORDER BY samples DESC LIMIT 12
        """,
    )
    copy(
        "buckets.parquet",
        f"""
        SELECT CASE
            WHEN list_contains(list_transform(frames, x ->
                  x ILIKE '%python_reexport%' OR x ILIKE '%reexport_source%'
                  OR x ILIKE '%module_file_matches_suffix%'), true)
              THEN 'A_python_reexport'
            WHEN list_contains(list_transform(frames, x -> x ILIKE '%ts_parser_parse%'), true)
              THEN 'C_extract_parse'
            WHEN list_contains(list_transform(frames, x -> x ILIKE '%buckets_from_facts%'), true)
              THEN 'D_buckets'
            WHEN list_contains(list_transform(frames, x ->
                  x ILIKE '%extract_files%' OR x ILIKE '%build_facts%'), true)
              THEN 'E_extract_other'
            WHEN list_contains(list_transform(frames, x ->
                  x ILIKE '%posix_spawn%' OR x ILIKE '%__open%'), true)
              THEN 'F_spawn_io'
            ELSE 'G_other'
          END AS bucket,
          SUM(samples)::BIGINT AS samples, COUNT(*)::BIGINT AS n_stacks,
          round(100.0 * SUM(samples) / {total}, 2) AS pct
        FROM read_folded('{path}')
        GROUP BY 1 ORDER BY samples DESC
        """,
    )
    copy(
        "fix.parquet",
        f"""
        SELECT CASE
                 WHEN list_contains(list_transform(frames, x ->
                       x ILIKE '%python_reexport%' OR x ILIKE '%reexport_source%'
                       OR x ILIKE '%module_file_matches_suffix%'), true)
                   THEN 'removed_if_reexport_capped'
                 ELSE 'remaining'
               END AS slice,
               SUM(samples)::BIGINT AS samples,
               round(100.0 * SUM(samples) / {total}, 2) AS pct
        FROM read_folded('{path}')
        GROUP BY 1 ORDER BY samples DESC
        """,
    )
    copy(
        "pareto.parquet",
        f"""
        SELECT rank, cum_pct, samples, {short_sql('s_leaf', 'leaf', 40)}
        FROM (
          SELECT row_number() OVER (ORDER BY samples DESC) AS rank,
                 round(100.0 * sum(samples) OVER (ORDER BY samples DESC) / {total}, 2) AS cum_pct,
                 samples,
                 {tidy.format(col='list_extract(frames, -1)')} AS s_leaf
          FROM read_folded('{path}')
        )
        WHERE rank <= 40
        ORDER BY rank
        """,
    )
    print("parquet", sorted(p.name for p in OUT.glob("*.parquet")))


if __name__ == "__main__":
    main()
