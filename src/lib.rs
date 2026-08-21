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
        "#,
    )?;

    Ok(())
}
