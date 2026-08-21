mod folded;
mod vtab;

use duckdb::{Connection, Result, duckdb_entrypoint_c_api};
use std::error::Error;
use vtab::ReadFoldedVTab;

#[duckdb_entrypoint_c_api]
pub unsafe fn extension_entrypoint(con: Connection) -> Result<(), Box<dyn Error>> {
    con.register_table_function::<ReadFoldedVTab>("read_folded")?;
    con.execute_batch(
        r#"
        CREATE OR REPLACE MACRO flamegraph_exclusive(path) AS TABLE (
            SELECT list_extract(frames, -1) AS leaf, SUM(samples)::BIGINT AS exclusive
            FROM read_folded(path)
            GROUP BY 1
        );
        CREATE OR REPLACE MACRO flamegraph_inclusive(path) AS TABLE (
            SELECT frame, SUM(samples)::BIGINT AS inclusive
            FROM (
                SELECT unnest(frames) AS frame, samples
                FROM read_folded(path)
            )
            GROUP BY 1
        );
        CREATE OR REPLACE MACRO flamegraph_coverage(path) AS TABLE (
            SELECT frame,
                   SUM(samples)::BIGINT AS coverage,
                   COUNT(*)::BIGINT AS n_stacks
            FROM (
                SELECT samples, unnest(list_distinct(frames)) AS frame
                FROM read_folded(path)
            )
            GROUP BY 1
        );
        CREATE OR REPLACE MACRO flamegraph_edges(path) AS TABLE (
            SELECT frames[i] AS parent,
                   frames[i + 1] AS child,
                   SUM(samples)::BIGINT AS samples
            FROM read_folded(path),
                 generate_series(1, len(frames) - 1) t(i)
            GROUP BY 1, 2
        );
        CREATE OR REPLACE MACRO flamegraph_hot_stacks(path) AS TABLE (
            SELECT samples,
                   len(frames) AS depth,
                   list_extract(frames, -1) AS leaf,
                   frames
            FROM read_folded(path)
        );
        "#,
    )?;

    Ok(())
}
