# CompX

**Explore specs. Compare systems.**

CompX is a local-first command-line catalog for exact laptop and desktop hardware configurations. It is for students, hobbyists, and developers who want to inspect sourced configurations in a local SQLite database. The current CLI supports database setup, validated CSV import, manual CSV refresh with change history, search, device details, side-by-side comparison, CSV/JSON export, and a review-first adapter for one official Lenovo PDF format.

## Setup

Requires Python 3.10 or newer. From a source checkout, create a virtual environment and install the CLI:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
```

For development and tests, install `-e '.[dev]'` instead of `.`. On Windows, use `.venv\Scripts\python` and `.venv\Scripts\compx` in place of the `.venv/bin/...` commands. The runtime dependencies beyond Python's standard library are Typer and pypdf; pypdf reads text from local PDFs and does not perform OCR.

## Complete synthetic demo

Run these commands from the repository root. The demo CSV contains **invented test configurations** and reserved `example.test` URLs; it is not verified product data. The temporary database keeps the demo separate from your normal catalog.

```sh
DEMO_DIR="$(mktemp -d)"
DEMO_DB="$DEMO_DIR/catalog.sqlite3"
.venv/bin/compx init --database "$DEMO_DB"
.venv/bin/compx import data/synthetic_demo.csv --dry-run --database "$DEMO_DB"
.venv/bin/compx import data/synthetic_demo.csv --database "$DEMO_DB"
.venv/bin/compx sync data/synthetic_demo.csv --database "$DEMO_DB"
.venv/bin/compx search Demo --database "$DEMO_DB"
.venv/bin/compx show 1 --database "$DEMO_DB"
.venv/bin/compx compare 1 2 --database "$DEMO_DB"
.venv/bin/compx history 1 --database "$DEMO_DB"
.venv/bin/compx export --format csv --database "$DEMO_DB"
.venv/bin/compx export --format json --output "$DEMO_DIR/devices.json" --database "$DEMO_DB"
.venv/bin/compx init --database "$DEMO_DB"
```

In this fresh demo catalog, the two imported configurations receive IDs `1` and `2`. The final `init` leaves them intact. To preview a row-numbered validation error without changing the catalog, create an invalid copy and run a dry run; a nonzero exit code is expected:

```sh
sed 's/2026-09-27/2026-02-30/g' data/synthetic_demo.csv > "$DEMO_DIR/invalid.csv"
.venv/bin/compx import "$DEMO_DIR/invalid.csv" --dry-run --database "$DEMO_DB"
```

## Commands

```sh
.venv/bin/compx --help
.venv/bin/compx init
.venv/bin/compx init --database ./data/my-catalog.sqlite3
.venv/bin/compx import data/synthetic_demo.csv --dry-run --database ./data/my-catalog.sqlite3
.venv/bin/compx import data/synthetic_demo.csv --database ./data/my-catalog.sqlite3
.venv/bin/compx source parse --help
.venv/bin/compx sync data/synthetic_demo.csv --database ./data/my-catalog.sqlite3
.venv/bin/compx sync data/synthetic_demo.csv --apply --database ./data/my-catalog.sqlite3
.venv/bin/compx search --database ./data/my-catalog.sqlite3
.venv/bin/compx search Test --type laptop --ram-min 16 --database ./data/my-catalog.sqlite3
.venv/bin/compx show 1 --database ./data/my-catalog.sqlite3
.venv/bin/compx history 1 --database ./data/my-catalog.sqlite3
.venv/bin/compx compare 1 2 --database ./data/my-catalog.sqlite3
.venv/bin/compx export --format csv --database ./data/my-catalog.sqlite3
.venv/bin/compx export --format json --output ./data/devices.json --database ./data/my-catalog.sqlite3
```

The demo and example files contain **synthetic test data**, not real products. Use a separate database path for experiments. `init` creates the parent directory and schema if needed and preserves existing catalog rows. The default catalog is `~/.local/share/compx/catalog.sqlite3` on Unix-like systems (or `$XDG_DATA_HOME/compx/catalog.sqlite3` when set), and `%LOCALAPPDATA%\compx\catalog.sqlite3` on Windows. Every command accepts `--database PATH`. `import` creates or upgrades the schema only after its CSV validates. A dry run never creates or changes the database.

The dry run reports valid rows, row-numbered errors, and whether import would succeed. An invalid row, duplicate manufacturer/SKU, or write failure blocks the entire real import; no configuration or source rows from the file are saved. Re-importing a SKU does not overwrite its specifications or merge sources. A successful CSV row creates one exact configuration and one source record.

## Assisted import from a Lenovo PSREF PDF

`compx source parse` supports **text-based Lenovo PSREF ThinkPad laptop model-detail PDFs** and **ThinkCentre M70s Gen 5 desktop model-detail PDFs**, each for one exact 10-character model code. Download the PDF yourself from Lenovo PSREF, then give CompX its local path and official `psref.lenovo.com` PDF URL. This command makes no network request and never changes the catalog. Platform specification PDFs, other desktop families, scans, encrypted PDFs, and other manufacturers are outside this adapter.

For example, after downloading the [official model-detail PDF for 21G20006GR](https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=21G20006GR&country_code=GR) to the path below:

```sh
.venv/bin/compx source parse ~/Downloads/ThinkPad_P14s_Gen_5_Intel_21G20006GR.pdf \
  --source-url 'https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=21G20006GR&country_code=GR' \
  --sku 21G20006GR --device-type laptop
.venv/bin/compx source parse ~/Downloads/ThinkPad_P14s_Gen_5_Intel_21G20006GR.pdf \
  --source-url 'https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=21G20006GR&country_code=GR' \
  --sku 21G20006GR --device-type laptop --output ./candidate.csv
.venv/bin/compx sync ./candidate.csv --database ./data/my-catalog.sqlite3
# After inspecting the PDF, candidate.csv, and sync preview:
.venv/bin/compx sync ./candidate.csv --apply --database ./data/my-catalog.sqlite3
```

The first command previews mapped, missing, ambiguous, and unsupported fields. The second saves a **human-review-required candidate** with exactly the existing import CSV header; that header cannot include an extra review marker without breaking `import` and `sync`. An existing output file is protected unless `--overwrite` is supplied. If the PDF's model code does not match `--sku`, CompX shows an unresolved preview and refuses to save an import-ready CSV. The URL and local PDF cannot be cryptographically matched, so verify that they refer to the same document yourself.

The adapter maps the ThinkPad title, model code, exact processor and graphics descriptions, installed RAM, one installed storage drive, battery Wh, and display inches when unambiguous. It leaves region and model year blank. Approximate or “starting at” weight, platform maxima such as “up to 96GB,” choices between components, and other unsupported PSREF labels are left blank or reported as unsupported. Storage uses decimal units (`1TB` becomes `1000` GB). Successful extraction does **not** guarantee every value is correct; read the PDF and edit the candidate before applying it. The full label rules and limitations are in [source documentation](docs/sources.md).

For a ThinkCentre M70s Gen 5 desktop, download the [official model-detail PDF for 12U8004HGR](https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=12U8004HGR&country_code=GR) to your computer. Then preview, save, and review a candidate:

```sh
.venv/bin/compx source parse ~/Downloads/ThinkCentre_M70s_Gen_5_12U8004HGR.pdf \
  --source-url 'https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=12U8004HGR&country_code=GR' \
  --sku 12U8004HGR --device-type desktop
.venv/bin/compx source parse ~/Downloads/ThinkCentre_M70s_Gen_5_12U8004HGR.pdf \
  --source-url 'https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=12U8004HGR&country_code=GR' \
  --sku 12U8004HGR --device-type desktop --output ./desktop-candidate.csv
.venv/bin/compx sync ./desktop-candidate.csv --database ./data/my-catalog.sqlite3
# Only after checking the PDF, CSV, and sync preview:
.venv/bin/compx sync ./desktop-candidate.csv --apply --database ./data/my-catalog.sqlite3
```

For this desktop layout, CompX maps the model heading, exact processor and graphics descriptions, installed RAM and storage, form factor, and stated PSU wattage. It leaves approximate weight, region, and model year blank; dimensions, ports, and expansion slots are shown as unsupported because the CSV has no matching fields. The PSU entry in the official PDF has a footnote, so review that detail before accepting the wattage. We validated this parser against the official 12U8004HGR PDF during development; manufacturer PDFs are not stored in the repository.

## Manual catalog updates and history

Use `compx sync FILE.csv` to preview a refreshed, source-backed CSV against the existing catalog. It uses the same columns, validation, units, and required source URL and checked date as `import`. Preview opens an existing catalog read-only; an absent catalog is treated as empty and is not created. Nothing is fetched from the source URL. Verify the source and specifications yourself before applying.

```sh
.venv/bin/compx sync data/synthetic_demo.csv --database ./data/my-catalog.sqlite3
.venv/bin/compx sync data/synthetic_demo.csv --apply --database ./data/my-catalog.sqlite3
.venv/bin/compx history 1 --database ./data/my-catalog.sqlite3
```

Sync matches **manufacturer + SKU**, trimmed and compared without case, across all families and regions. It never matches by family or model name alone. An identity that matches multiple stored configurations is a conflict. Repeated identities within the input are also conflicts. Records absent from the refreshed CSV remain untouched, and `import` remains insert-only.

Each valid row is `New`, `Unchanged`, `Changed`, or `Source-only update`. `Changed` means one or more configuration values differ; the preview shows each old and new value with its unit. Text comparison ignores case and repeated whitespace, as `compare` does. A change involving an unknown value is marked `data update`, since it does not establish that hardware changed. A source-only update has the same configuration values but a new URL and/or checked date. A URL/date pair already stored for that configuration is unchanged. Invalid rows and conflicts are reported with row numbers and block `--apply` for the whole file. Applying writes all new records and updates in one transaction; a write failure rolls them all back. An unchanged row causes no write.

Sync preserves earlier source records and appends a new source when its URL/date pair has not been recorded. `history DEVICE_ID` lists revisions in recorded order, including initial records, known hardware changes, data updates, and source-only updates. It shows field changes and the associated source URL and checked date. `compx init` upgrades older catalogs with a one-time baseline revision for each existing configuration; its timestamp is the **upgrade time**, not the original import date. Existing IDs, specifications, and sources are preserved. Blank optional CSV fields remain unknown and can clear previously known values when a refreshed row is applied, so review the preview carefully.

## Search and device details

`compx search [QUERY]` searches manufacturer, product family, model name, and SKU for the supplied text, ignoring case. With no query or filters, it lists up to **20** configurations. Use `--limit N` (a positive integer) to change that count. Results are sorted by manufacturer, model, SKU, and stable configuration ID. The `ID` in the results is the value to pass to `compx show DEVICE_ID`. Use IDs from your own search results; `1` and `2` apply to the fresh demo catalog above.

Filters can be combined; **every supplied filter must match**. `--type` accepts `laptop` or `desktop`. `--manufacturer NAME` matches the full manufacturer name without regard to case. `--cpu TEXT` and `--gpu TEXT` match part of their descriptions without regard to case. `--ram-min GB` and `--storage-min GB` require at least that positive number of GB. Unknown RAM or storage does not match a minimum. Search and detail output display missing optional values as `Not provided`.

```sh
.venv/bin/compx search --type laptop --ram-min 16 --database ./data/my-catalog.sqlite3
.venv/bin/compx search --manufacturer "Synthetic Test Co" --gpu "RTX" --database ./data/my-catalog.sqlite3
.venv/bin/compx search "Test Family" --storage-min 512 --limit 10 --database ./data/my-catalog.sqlite3
```

`show` prints the selected configuration's fields and every stored source URL, title (when present), and checked date. `search` and `show` use the same `--database PATH` option as `init` and `import`; omit it to use the default catalog described above. They read the database without changing it. If the catalog is absent or still uses the Phase 1 schema, run `compx init --database PATH` first.

## Compare

Pass two IDs from `compx search` to `compx compare DEVICE_ID DEVICE_ID`. The command shows fields in a fixed order, with source URLs, titles, and checked dates below the comparison. It works across laptop and desktop configurations and does not change the catalog.

| Result | Meaning |
| --- | --- |
| `Same` | Both values are known and equal. Text ignores surrounding/repeated whitespace and case. |
| `Different` | Both values are known and unequal. |
| `Unknown` | One or both values are not provided; no equality claim is made. |
| `Not applicable` | A field does not apply to one or both device types. |

Display size, battery, and weight apply to laptops; form factor and power supply apply to desktops. Comparison does not score, rank, or recommend devices.

## Export

`compx export --format csv` and `compx export --format json` write UTF-8 data to **standard output**, with no status text mixed into it. Pipe or redirect the output as needed. Use `--output PATH` to write a file instead. With `--output`, existing files are protected unless `--overwrite` is supplied; shell redirection follows the shell's own overwrite rules. Output cannot be the catalog database file. File-write status goes to standard error.

```sh
.venv/bin/compx export --format csv --database ./data/my-catalog.sqlite3
.venv/bin/compx export --format json --output devices.json --database ./data/my-catalog.sqlite3
.venv/bin/compx export --format json --output devices.json --overwrite --database ./data/my-catalog.sqlite3
.venv/bin/compx export Test --format csv --type laptop --ram-min 16 --limit 10 --database ./data/my-catalog.sqlite3
```

Export accepts the same free-text query and `--type`, `--manufacturer`, `--cpu`, `--gpu`, `--ram-min`, and `--storage-min` filters as search. Filters combine with AND and use search's case-insensitive matching and stable ordering. An explicit `--limit N` applies the same positive limit as search. **With no `--limit`, export includes all matching configurations**, including an unfiltered full catalog; search itself defaults to 20 results.

CSV has one row per configuration. Its fixed column order is:

```text
id,manufacturer,product_family,model_name,device_type,configuration_key,sku,part_number,region,model_year,cpu,gpu,ram_gb,storage_gb,display_size_inches,battery_wh,weight_g,form_factor,power_supply_w,sources_json
```

The CSV header is present even for zero matches. Unknown optional values are empty cells. `sources_json` is a valid JSON array of **all** source objects for that configuration, each with `url`, `title`, and `checked_on`. An empty source list is `[]`. The export CSV is a data extract, not the Phase 2 import template.

JSON has a stable top-level object with `schema_version` set to `1` and a `configurations` array in search order. Each configuration has the fields above except `sources_json`; instead, it has a `sources` array of objects with `url`, `title`, and `checked_on`. Unknown values are JSON `null`. Numeric values use the units in their field names: GB, inches, Wh, grams, and watts. Neither export format adds a timestamp, so unchanged data produces the same serialized result.

## CSV format

Start with the [header-only template](data/devices_template.csv). The [synthetic example](data/synthetic_example.csv) shows a minimal one-record file, and the [synthetic demo](data/synthetic_demo.csv) supports the full workflow above. The header names are exact; unexpected columns are reported so typos are visible. Surrounding whitespace is trimmed. Required cells cannot be blank; blank optional cells become unknown (`NULL`), never zero.

| Column | Required | Meaning and format |
| --- | --- | --- |
| `manufacturer` | Yes | Manufacturer name. |
| `product_family` | Yes | Product line or family. |
| `model_name` | Yes | Model within that family. |
| `device_type` | Yes | `laptop` or `desktop`. |
| `sku` | Yes | Identifier for one exact configuration. |
| `source_url` | Yes | HTTP or HTTPS URL for the source. |
| `checked_on` | Yes | Date source was checked, `YYYY-MM-DD`. |
| `region` | No | Market or region, when known. |
| `model_year` | No | Positive integer year. |
| `cpu`, `gpu` | No | Hardware descriptions as stated by the source. |
| `ram_gb`, `storage_gb` | No | Positive integer GB. |
| `display_size_in` | No | Positive number of inches. |
| `battery_wh` | No | Positive number of Wh. |
| `weight_g` | No | Positive integer grams. |
| `form_factor` | No | Desktop form factor text. |
| `psu_watts` | No | Positive integer watts. |

The importer validates dates as real calendar dates and source URLs as HTTP(S) URLs. It does not fetch or independently verify a source. Every real configuration row needs a source URL and checked date. Enter only specifications supported by that source; leave unknown values blank. `manufacturer` plus `sku`, compared without case, is the import identity across all families and regions.

Phase 1's header-only template used different names. The Phase 2 template uses the columns above; see the [data model](docs/data-model.md) for the mapping to retained database fields and the non-destructive schema upgrade. Old CSV headers need to be updated, and `product_family` and `model_name` must be supplied explicitly; CompX does not infer them.

## Tests

```sh
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

To build source and wheel packages locally with `uv`, run `uv build --out-dir dist`. The built wheel installs the `compx` entry point and bundled SQLite schema; no package is published by this command.

CompX has no web interface, automatic source fetching, scraping, price tracking, AI extraction, recommendations, or cloud synchronization. Source URLs are syntax-checked but not visited. See the [architecture](docs/architecture.md), [data model](docs/data-model.md), and [source documentation](docs/sources.md) for implementation boundaries and units.
