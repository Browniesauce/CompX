# Data model

The SQLite schema has five entities:

| Entity | Purpose | Relationship and identity |
| --- | --- | --- |
| `manufacturers` | Company name | SQLite `NOCASE` unique name; the importer also reuses names after Unicode case folding. |
| `device_families` | Product family, model, and `device_type` | Many per manufacturer; unique manufacturer, type, family, and model for new records. |
| `configurations` | Exact hardware configuration | Many per model; unique local `configuration_key` and known SKU within a model. |
| `sources` | Evidence for a configuration | Many per configuration; unique URL and checked date within a configuration. |
| `revisions` | Recorded state and provenance after each addition or update | Many per configuration, ordered by revision ID; JSON before/after snapshots retain NULL values and all sources. |

Phase 7 adds no tables or CSV columns. Its assisted PDF parser produces a one-row candidate with the existing import header, leaving unsupported, missing, or ambiguous values blank. It does not write to these tables; a user may later run `sync` after reviewing the candidate and its source. [Source documentation](sources.md) describes the supported PSREF labels.

For imports, **manufacturer + SKU** (trimmed, compared without case) identifies an exact configuration across all product families, models, and regions. A duplicate in the CSV or database blocks the entire import. The importer stores the CSV `sku` in both `configurations.sku` and the existing `configuration_key` column; it never derives missing specifications. The database still allows nullable SKU and a separate local key for older or manually staged rows. Phase 2 does not provide a command to create such rows.

Sync uses this same manufacturer/SKU identity. If an older or manually edited catalog has multiple matching configurations, sync reports a conflict and makes no changes. A refreshed CSV row is a complete value set: blank optional fields map to NULL, including when they replace known values. Family, model, and device type changes move only the matched configuration to an appropriate family row; other configurations keep their associations. Sync does not delete omitted configurations or earlier source rows. A new `(source_url, checked_on)` pair is appended; an existing pair is not duplicated. The CSV has no source-title column, so existing source titles remain intact.

The importer and read commands use Unicode case folding for names and text filters. SQLite's built-in `NOCASE` constraints remain in the schema for basic database-level protection; manually inserted Unicode case variants outside the CLI may still need curator review.

## CSV columns and database mapping

The [header-only template](../data/devices_template.csv) defines the accepted CSV column names. Required columns are `manufacturer`, `product_family`, `model_name`, `device_type`, `sku`, `source_url`, and `checked_on`. Optional columns are `region`, `model_year`, `cpu`, `gpu`, `ram_gb`, `storage_gb`, `display_size_in`, `battery_wh`, `weight_g`, `form_factor`, and `psu_watts`. See the [README](../README.md#csv-format) for each field's meaning and units.

| CSV field | Stored field |
| --- | --- |
| `manufacturer` | `manufacturers.name` |
| `product_family` | `device_families.product_family` |
| `model_name` | `device_families.model` (Phase 1 name retained) |
| `device_type` | `device_families.device_type` |
| `sku` | `configurations.sku` and `configurations.configuration_key` |
| `region`, `model_year`, `cpu`, `gpu`, `ram_gb`, `storage_gb`, `battery_wh`, `weight_g`, `form_factor` | Same-named `configurations` columns |
| `display_size_in` | `configurations.display_size_inches` (Phase 1 name retained) |
| `psu_watts` | `configurations.power_supply_w` (Phase 1 name retained) |
| `source_url`, `checked_on` | Same-named `sources` columns |

`checked_on` must be a real date in `YYYY-MM-DD` form; `source_url` must be an HTTP(S) URL. The importer stores one source record per CSV row and requires both fields. It checks syntax but does not fetch the URL or verify the claimed specifications. Optional blank values are stored as NULL. RAM and storage use positive integer GB; display uses positive inches; battery uses positive Wh; weight uses positive integer grams; PSU uses positive integer watts. `model_year` is a positive integer. Laptop and desktop fields may remain NULL when unknown or not applicable. The importer does not yet enforce subtype-specific fields.

## Phase 1 schema compatibility

Phase 1 stored one `device_families.model` field and no model year. Phase 2 adds nullable `product_family` and `model_year`. On `compx init` or a successful real import, existing Phase 1 family rows are copied into the new family table with the same IDs and `product_family = NULL`; configurations and sources keep their IDs and foreign keys. The new family uniqueness key includes `product_family`, allowing the same model name under two different families. The upgrade runs in a transaction and checks foreign keys before committing. Existing `model`, `configuration_key`, `part_number`, and `source_name` data remains in place. `part_number` and `source_name` are not in the Phase 2 CSV contract.

The Phase 1 CSV template was only an outline and is replaced with the accepted Phase 2 headers. Older CSV files require explicit `product_family` and `model_name` columns; CompX does not guess a family from an old `model` value. No catalog records are added by the template or synthetic example file until a user explicitly imports them.

## Phase 6 history migration

The forward-only Phase 6 schema adds `revisions` and an index by configuration and revision ID. `compx init`, a successful real import, or `compx sync --apply` creates the table if needed and adds one baseline revision for every existing configuration that has no history. The baseline captures the then-current fields and all stored sources, preserving unknown values as JSON `null`; its `recorded_at` is the migration time because earlier change times are unavailable. Re-running initialization does not duplicate baselines. Existing configuration IDs and source rows are not changed.

New imports and sync additions create an `initial` revision. Sync updates create `hardware`, `data_update`, or `source_only` revisions. `hardware` means at least one known hardware specification changed to another known value. `data_update` covers unknown-to-known, known-to-unknown, and descriptive-field-only changes without claiming a hardware change. Each revision stores UTC `recorded_at`, complete before/after snapshots, and the CSV source URL and checked date. The snapshots distinguish SQL NULL from empty text or zero. Source history is append-only. Sync validates and plans the whole CSV before writes, rechecks the plan under a write lock, and writes all planned changes and revisions in one transaction. Preview is read-only and does not migrate the schema.

## Phase 3 read identity

Search rows display `configurations.id`, SQLite's persistent primary key for an exact configuration. `compx show` uses that same ID to retrieve one configuration and all associated `sources` rows. Result position and sort order never act as identifiers. Phase 3 changes no tables or existing records; missing fields are rendered as `Not provided` rather than inferred.

## Phase 4 comparison and export

Comparison reads two `configurations.id` values and their source rows. It labels known equal values `Same`, known unequal values `Different`, missing values `Unknown`, and category-specific fields `Not applicable` for the other device type. Text equality uses case folding and collapsed whitespace. No comparison result is stored in the database.

CSV and JSON export use the stored canonical fields without schema changes. CSV places one configuration on each row and puts every associated source in its `sources_json` array. JSON uses `schema_version: 1` and a `configurations` array; each object includes a `sources` array. Sources have `url`, `title`, and `checked_on` keys. Missing stored values become empty CSV cells or JSON `null`; export does not infer specifications. The exact CSV column order and command syntax are in the [README](../README.md#export). The [two-record demo CSV](../data/synthetic_demo.csv) is explicitly synthetic and supports the complete local workflow.
