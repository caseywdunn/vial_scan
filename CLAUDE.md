# Vial Label Extraction Pipeline

## Project Overview

Extract structured metadata from cryovial label images collected by a rotating imaging rig (Dunn Lab). Each vial is photographed multiple times from different angles, producing a collage image. The rig captures the vial **before** adding a DataMatrix barcode label (left side of collage) and **after** (right side of collage). We extract:

- **Handwritten label text** from the pre-label images (left side)
- **DataMatrix integer ID** from the post-label images (right side, readable as printed text below the barcode)
- **Parsed metadata** from the transcribed text: date, sampling event, species, tissue, notes

Output is a spreadsheet with one row per collage image file.

---

## Image Format

Each input image is a **collage of 10 sub-images** arranged in a 2-column grid:
- **Left column** (5 images): Pre-label views — show handwritten text on the vial
- **Right column** (5 images): Post-label views — show DataMatrix barcode + printed integer below it, plus handwritten text

Sub-image filenames are printed below each tile in the collage (e.g. `IMG_1437.JPG`). The collage filename encodes the DataMatrix integer (e.g. `DunnLab000199.png` → integer `199`).

The most useful sub-images are typically:
- **Top-left** (pre-label): clearest handwritten label view
- **Top-right** (post-label): clearest DataMatrix + printed number view
- **Bottom-right tiles**: often show both handwritten text and the full label together

---

## Label Content Conventions

Labels are handwritten and may include any combination of the following fields, often on separate lines:

### Sample Number
- Format: `#187`, `q187`, `# 187` — a hash/pound sign followed by an integer
- This is the vial's internal collection number

### Sampling Event
Abbreviations for dive vehicle, dive number, and sampler:
- `V401-SS2` → Ventana dive 401, suction sampler 2
- `D1041-D4` → Doc Ricketts dive 1041, detritus sampler 4
- `BW2` → Blue water dive 2
- Vehicle prefixes: `V`=Ventana, `D`=Doc Ricketts, `T`=Tiburon, `W`=Western Flyer, `BW`=blue water
- Sampler codes: `SS`=suction sampler, `D`=detritus sampler, `N`=net, `MC`=midwater collection

### Species
Common abbreviations and corrections (applied in `parse_fields.py`):
- `Nanomia` or `Nano` → *Nanomia bijuga*
- `B elongata` → *Bargmannia elongata*
- `Agalma` → *Agalma elegans*
- `Muggiaea` → *Muggiaea atlantica*
- `Physo` → *Physophora hydrostatica*
- `Rosacea` → *Rosacea cymbiformis*
- `Cordagalma` → *Cordagalma ordinatum*
- `Apolemia` → *Apolemia* sp.
- When species is ambiguous or partially legible, record best guess and note uncertainty in comments

### Tissue Type
Common abbreviations (applied in `parse_fields.py`):
- `necto 1`, `N1` → nectophore 1
- `GZ` → growth zone
- `gastro` → gastrozooid
- `gonzo` → gonozooid
- `pneu` → pneumatophore
- `sipho` → whole siphonophore
- `stem` → stem
- `neck` → nectophore (generic)
- Tissue field is optional; many samples are whole animals

### Date
- Format varies: `20150801`, `8/1/15`, `Aug 1 2015` — normalize to `YYYYMMDD`
- Dates are often absent from labels

### Notes
- Anything on the label not fitting the above fields
- Collector initials, condition notes, replicate markers, etc.

---

## Code Structure

```
vial_scan/
├── CLAUDE.md           # This file — domain knowledge and conventions
├── README.md           # Setup and usage instructions
├── config.yaml         # Paths and model settings
├── requirements.txt
├── .gitignore
├── preprocess.py       # resize_image() — JPEG bytes for API
├── extract.py          # Main script: crawl images, call vLLM, write output
├── parse_fields.py     # parse_transcription() — 2nd LLM pass, text-only
├── spreadsheet.py      # write_spreadsheet() — openpyxl with QC color coding
└── output/
    └── results.xlsx    # Final spreadsheet (gitignored)
```

### Key architectural decisions

- **Two-pass LLM pipeline**: `extract.py` makes two calls per image — one vision call (image + prompt → raw transcription) and one text-only call (transcription → structured fields). Both calls use the same Qwen2.5-VL model served by vLLM and run concurrently under the same semaphore.
- **`spreadsheet.py` is a separate module**: not part of `extract.py`, imported explicitly. Keeps output logic isolated.
- **`datamatrix_match`** is computed in `extract.py` by comparing `filename_integer` (from the filename) to `datamatrix_integer` (returned by the model). Mismatches are flagged red in the spreadsheet.
- **Async concurrency**: `batch_size` in config controls how many images are in-flight simultaneously via `asyncio.Semaphore`.

### Resolution note
Start at `max_image_size: 1500`. At 1500px longest edge, each of the 10 sub-image tiles is roughly 300–400px — adequate for printed text, possibly marginal for small handwriting. Increase to 2000px if accuracy on difficult labels is poor.

---

## QC and Review

The spreadsheet uses color coding:
- **Red rows**: transcription or parse confidence ≤ 3 — needs manual review
- **Yellow rows**: confidence 4–6 — spot check recommended
- **White rows**: confidence ≥ 7 — likely good

The `datamatrix_match` column flags any case where the integer extracted from the image differs from the integer in the filename — always investigate these.

The `transcription_comments` and `parse_comments` columns flag:
- Images with unexpected layouts
- Labels that are partially obscured or rotated
- Cases where the DataMatrix printed integer was not legible
- Conflicting information across collage tiles
- Corrected spellings and ambiguous abbreviations

---

## Known Challenges

- **Handwriting legibility**: Cursive or rushed writing will reduce confidence. Common ambiguities: `0` vs `O`, `1` vs `I`, `5` vs `S`, `SS` vs `55`.
- **Collage layout**: The model must correctly identify left vs right columns. The prompt explicitly describes this; verify on a sample that it's doing so correctly.
- **Species abbreviations**: Many non-standard abbreviations exist. Extend the species correction table in `parse_fields.py` as new species are encountered.
- **Curved labels**: Vials are cylindrical; some angles may show text wrapped around the curve. The multi-view collage mitigates this.
- **Pre-label vs post-label confusion**: Some right-column images also show handwritten text (visible above or below the barcode label). The model should prioritize left-column handwritten text but can supplement from right-column if clearer.

---

## Version Control

Raw images and pipeline output are gitignored (see `.gitignore`). Large image data lives on scratch (`/nfs/roberts/scratch/pi_cwd7/cwd7/vial_scan/images/`) or project storage — not in the repo. Scratch is purged after 60 days.
