# Assisted source parsing

CompX supports two **text-based, SKU-specific Lenovo PSREF model-detail PDF** layouts: Phase 7 ThinkPad laptops and Phase 8 ThinkCentre M70s Gen 5 desktops. A user supplies the local PDF, its official `https://psref.lenovo.com` PDF download URL, `--sku`, and an explicit `--device-type laptop` or `--device-type desktop`. The URL may be Lenovo's `/api/model/pdfexport/singleModel?model_code=...&country_code=...` endpoint or a SKU-suffixed `/syspool/TempFile/cache/...pdf` link. Cache links can expire. The adapter checks URL structure and model code but does not fetch the URL, authenticate the PDF, or compare its bytes with the remote document.

We inspected Lenovo's [PSREF model page for ThinkPad P14s Gen 5 (Intel), model 21G20006GR](https://psref.lenovo.com/Detail/ThinkPad_P14s_Gen_5_Intel?M=21G20006GR) and its [official model-detail PDF export](https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=21G20006GR&country_code=GR) during development. The PDF contains a ThinkPad title with the model code and labeled `Processor`, `Graphics`, `Memory`, `Storage`, `Battery`, `Display`, and `Weight` entries. It separately lists `Max Memory`, `Max Storage Support`, and `Power Adapter`, demonstrating why platform limits and accessories must not be mapped as installed fields. The extracted `Weight` entry says “Starting at,” and Lenovo notes that weight varies; CompX leaves `weight_g` blank. The manufacturer PDF is not stored in this repository.

We also validated the desktop parser with Lenovo's [official ThinkCentre M70s Gen 5 model-detail PDF for 12U8004HGR](https://psref.lenovo.com/api/model/pdfexport/singleModel?model_code=12U8004HGR&country_code=GR). Its title names exactly one model code, and the specification page states installed processor, graphics, memory, storage, form factor, and PSU wattage. `Max Memory` and `Max Storage Support` describe capabilities, not the installed configuration. The PDF includes approximate weight and a PSU footnote, which need human review. The downloaded document was used only for development validation and is not in the repository.

## ThinkPad laptop mapping

| PSREF label | CompX CSV field | Rule |
| --- | --- | --- |
| ThinkPad model heading and code | `manufacturer`, `product_family`, `model_name`, `sku` | Require title code to match `--sku`; use `Lenovo`, `ThinkPad`, and the remaining model title. |
| `Processor` | `cpu` | Take one stated Intel or AMD processor model before performance details. Reject options. |
| `Graphics` | `gpu` | Copy one stated Intel, AMD, or NVIDIA description. Reject lists or alternatives. |
| `Memory` | `ram_gb` | Accept one explicit installed GB amount, including `2x 16GB`; never use `Max Memory`. |
| `Storage` | `storage_gb` | Accept one installed SSD/HDD amount; convert `1TB` to `1000` GB. Never use storage support limits. |
| `Battery` | `battery_wh` | Accept one exact Wh amount. |
| `Display` | `display_size_in` | Accept one stated diagonal size in inches. |
| `Weight` | `weight_g` | Leave blank because PSREF model weights may be approximate or minimum values. |

## ThinkCentre M70s Gen 5 desktop mapping

Only a single-model, text-based **ThinkCentre M70s Gen 5 model-detail PDF** with the exact 10-character code in its heading is supported. Other ThinkCentre families and platform specification PDFs are rejected. The desktop parser has separate label and mapping rules from the laptop parser.

| PSREF label | CompX CSV field | Rule |
| --- | --- | --- |
| M70s Gen 5 heading and model code | `manufacturer`, `product_family`, `model_name`, `sku`, `device_type` | Require the code to match `--sku`; use `Lenovo`, `ThinkCentre`, `M70s Gen 5`, and `desktop`. |
| `Processor` | `cpu` | Accept one stated Intel Core processor model; reject alternatives. |
| `Graphics` | `gpu` | Copy one exact stated Intel, AMD, or NVIDIA description. |
| `Memory` | `ram_gb` | Accept installed GB, including `2x 16GB`; never use `Max Memory`. |
| `Storage` | `storage_gb` | Accept one installed SSD/HDD amount; convert `1TB` to `1000` GB. Never use support limits. |
| `Form Factor` | `form_factor` | Accept `SFF` with an optional stated chassis volume, such as `SFF (8.2L)`. |
| `Power Supply` | `psu_watts` | Accept one stated wattage; flag any source footnote for review. |
| `Weight` | `weight_g` | Leave blank because this source gives an approximate weight. |

The current CSV has no fields for dimensions, expansion slots, or ports. These and other recognized unsupported labels appear in the review rather than being placed in unrelated fields.

## Review and limits

The adapter does not infer `region` from a country code or SKU suffix, or `model_year` from a generation number or announce date. It does not put a laptop power adapter into the desktop-only `psu_watts` field. Recognized PSREF labels without a matching CSV field are shown as `unsupported` in the preview. Absent and ambiguous values remain blank, with warnings. The CSV retains the exact import template header; “human review required” is displayed by the command and documented here because an extra CSV marker would violate the existing contract.

The PDF reader uses **pypdf**, one pure-Python parsing dependency. It extracts embedded text only. Scanned, password-protected, malformed, platform-level, and unsupported-family documents are rejected. PDF text order can vary across Lenovo exports, so even a successful preview must be checked against the original PDF before saving or applying the CSV. Parsing and candidate writing never access SQLite. `compx sync candidate.csv` supplies the existing read-only change preview, and `compx sync candidate.csv --apply` is a separate user action that writes to the catalog.
