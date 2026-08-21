# quack_flamegraph

DuckDB extension for querying [Brendan Gregg folded](https://www.brendangregg.com/FlameGraphs/cpuflamegraphs.html) CPU/allocation profiles as tables.

```sql
INSTALL quack_flamegraph FROM community;  -- after community listing
LOAD quack_flamegraph;

SELECT leaf, exclusive
FROM flamegraph_exclusive('cpu.folded')
ORDER BY exclusive DESC
LIMIT 10;
```

Until the [community-extensions](https://github.com/duckdb/community-extensions) listing exists, load a local build (DuckDB **v1.5.5**, unsigned):

```sql
LOAD './build/debug/extension/quack_flamegraph/quack_flamegraph.duckdb_extension';

SELECT * FROM read_folded('test/data/tiny.folded');
SELECT * FROM flamegraph_exclusive('test/data/tiny.folded');
SELECT * FROM flamegraph_inclusive('test/data/tiny.folded');
```

## Functions

| Function | Kind | Result |
|---|---|---|
| `read_folded(path)` | table | `frames VARCHAR[]`, `samples BIGINT`, `source VARCHAR` |
| `flamegraph_exclusive(path)` | table macro | `leaf`, `exclusive` |
| `flamegraph_inclusive(path)` | table macro | `frame`, `inclusive` (unnest; recursive names inflate) |
| `flamegraph_coverage(path)` | table macro | `frame`, `coverage`, `n_stacks` (once per stack) |
| `flamegraph_edges(path)` | table macro | `parent`, `child`, `samples` |
| `flamegraph_hot_stacks(path)` | table macro | `samples`, `depth`, `leaf`, `frames` |

Parser rules (DuckDB `read_*` ingest style): last space-separated field is the sample count; `samples = 0` lines are dropped; optional Instruments `Thread_…:` prefix is stripped; missing file → `IO Error`; malformed line → `Invalid Input Error`.

For recursive frames use `flamegraph_coverage`, not `flamegraph_inclusive`. On the Spur/Turso profile, `collect_symbol_tokens` is 51% unnest-inclusive and **4.5% coverage**.

## Build

Clone with submodules, then:

```shell
git clone --recurse-submodules https://github.com/kevintruong/quack-flamegraph.git
cd quack-flamegraph
make configure
make debug
make test_debug
```

Release binaries: `make release`.

Requires Python 3, Make, Git, and a Rust toolchain. The extension is a pure-Rust C Extension API module (no DuckDB C++ rebuild).

## Community listing

Canonical descriptor to copy into a PR on [duckdb/community-extensions](https://github.com/duckdb/community-extensions) (`extensions/quack_flamegraph/description.yml`):

[`community/description.yml`](community/description.yml)

Extension name is `quack_flamegraph` (lowercase `[a-z0-9_]`). Pin `repo.ref` to a git tag or SHA before opening that PR.

## License

MIT
