#!/usr/bin/env python3
"""Extract tidy parquet from a folded profile via quack_flamegraph (DuckDB 1.5.5)."""

from __future__ import annotations

import pathlib
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXT = ROOT / "build/debug/quack_flamegraph.duckdb_extension"
FOLDED = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else ROOT / "test/data/spur-turso-demo.folded"
)
OUT = pathlib.Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "build/turso-analysis")


def short_sql(col: str, alias: str, n: int = 48) -> str:
    return f"""
    CASE
      WHEN len(s_{alias}) > {n} THEN '...' || s_{alias}[-{n - 1}:]
      ELSE s_{alias}
    END AS {alias}
    """


def main() -> None:
    if not EXT.exists():
        sys.exit(f"missing extension: {EXT}")
    if not FOLDED.exists():
        sys.exit(f"missing folded profile: {FOLDED}")
    OUT.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    con.execute(f"LOAD '{EXT}'")
    total = con.execute(f"SELECT SUM(samples)::BIGINT FROM read_folded('{FOLDED}')").fetchone()[0]
    print("samples", total, "duckdb", duckdb.__version__)

    con.execute(f"""
    COPY (
      SELECT {short_sql('frame', 'label')}, coverage, n_stacks,
             round(100.0 * coverage / {total}, 2) AS pct
      FROM (
        SELECT regexp_replace(regexp_replace(frame, '^[^`]*`', ''), '^spur_graph::', '') AS s_label,
               coverage, n_stacks
        FROM flamegraph_coverage('{FOLDED}')
        WHERE coverage < {total} * 0.90
          AND frame NOT ILIKE '%lang_start%'
          AND frame NOT ILIKE '%dyld%'
          AND frame NOT ILIKE '%backtrace%'
      )
      ORDER BY coverage DESC LIMIT 16
    ) TO '{OUT}/coverage.parquet' (FORMAT PARQUET)
    """)

    con.execute(f"""
    COPY (
      SELECT {short_sql('leaf', 'label')}, exclusive,
             round(100.0 * exclusive / {total}, 2) AS pct
      FROM (
        SELECT regexp_replace(regexp_replace(leaf, '^[^`]*`', ''), '^spur_graph::', '') AS s_label,
               exclusive
        FROM flamegraph_exclusive('{FOLDED}')
      )
      ORDER BY exclusive DESC LIMIT 15
    ) TO '{OUT}/exclusive.parquet' (FORMAT PARQUET)
    """)

    con.execute(f"""
    COPY (
      SELECT {short_sql('p', 'parent', 36)}, {short_sql('c', 'child', 36)},
             samples, round(100.0 * samples / {total}, 2) AS pct
      FROM (
        SELECT regexp_replace(regexp_replace(parent, '^[^`]*`', ''), '^spur_graph::', '') AS s_parent,
               regexp_replace(regexp_replace(child, '^[^`]*`', ''), '^spur_graph::', '') AS s_child,
               samples
        FROM flamegraph_edges('{FOLDED}')
        WHERE samples < {total} * 0.50 AND parent <> child
      )
      ORDER BY samples DESC LIMIT 16
    ) TO '{OUT}/edges.parquet' (FORMAT PARQUET)
    """)

    con.execute(f"""
    COPY (
      WITH ranked AS (
        SELECT samples, leaf,
               ROW_NUMBER() OVER (ORDER BY samples DESC, leaf) AS rank,
               SUM(samples) OVER (ORDER BY samples DESC, leaf) AS cum
        FROM flamegraph_hot_stacks('{FOLDED}')
      )
      SELECT rank, round(100.0 * cum / {total}, 2) AS cum_pct, samples,
             {short_sql('leaf', 'leaf')}
      FROM ranked,
           LATERAL (SELECT regexp_replace(regexp_replace(ranked.leaf, '^[^`]*`', ''), '^spur_graph::', '') AS s_leaf)
      WHERE rank <= 400
    ) TO '{OUT}/pareto.parquet' (FORMAT PARQUET)
    """)

    con.execute(f"""
    COPY (
      SELECT
        CASE
          WHEN list_contains(frames, 'spur`spur_graph::extract::tree_sitter::run_query') THEN 'A_run_query'
          WHEN list_contains(frames, 'spur`spur_graph::store::build::buckets_from_facts') THEN 'B_buckets'
          WHEN list_contains(list_transform(frames, x -> x ILIKE '%ts_parser_parse%'), true) THEN 'C_parse'
          WHEN list_contains(list_transform(frames, x -> x ILIKE '%contained_capture_text%'), true) THEN 'E_capture_text'
          WHEN list_contains(list_transform(frames, x -> x ILIKE '%collect_symbol_tokens%'), true) THEN 'D_tokens'
          WHEN list_contains(list_transform(frames, x -> x ILIKE '%git_walk%'), true) THEN 'F_walk_git'
          ELSE 'G_other'
        END AS bucket,
        SUM(samples)::BIGINT AS samples,
        COUNT(*)::BIGINT AS n_stacks,
        round(100.0 * SUM(samples) / {total}, 2) AS pct
      FROM read_folded('{FOLDED}')
      GROUP BY 1
      ORDER BY samples DESC
    ) TO '{OUT}/buckets.parquet' (FORMAT PARQUET)
    """)

    con.execute(f"""
    COPY (
      SELECT samples, depth, {short_sql('leaf', 'leaf')},
             round(100.0 * samples / {total}, 2) AS pct
      FROM (
        SELECT samples, depth,
               regexp_replace(regexp_replace(leaf, '^[^`]*`', ''), '^spur_graph::', '') AS s_leaf
        FROM flamegraph_hot_stacks('{FOLDED}')
      )
      ORDER BY samples DESC LIMIT 16
    ) TO '{OUT}/hot_stacks.parquet' (FORMAT PARQUET)
    """)

    print("wrote", sorted(p.name for p in OUT.glob("*.parquet")))


if __name__ == "__main__":
    main()
