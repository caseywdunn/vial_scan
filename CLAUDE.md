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
- `DS00-SS12` → (interpret based on vehicle prefix: D=Doc Ricketts, V=Ventana, T=Tiburon, W=Western Flyer, BW=blue water)
- Vehicle prefixes: `V`=Ventana, `D`=Doc Ricketts, `T`=Tiburon, `W`=Western Flyer

### Species
Common abbreviations and corrections:
- `Nanomia` or `Nano` → *Nanomia bijuga*
- `Nanomia bijuga` → *Nanomia bijuga*
- `B elongata` → *Bargmannia elongata*
- `Agalma` → *Agalma elegans*
- `Muggiaea` → *Muggiaea atlantica*
- `Physo` → *Physophora hydrostatica*
- `Rosacea` → *Rosacea cymbiformis*
- `Cordagalma` → *Cordagalma ordinatum*
- `Apolemia` → *Apolemia* sp.
- When species is ambiguous or partially legible, record best guess in species field and note uncertainty in comments

### Tissue Type
Common abbreviations:
- `necto 1`, `N1` → nectophore 1
- `GZ` → growth zone
- `gastro` → gastrozooid
- `gonzo` → gonozooid
- `pneu` → pneumatophore
- `sipho` → siphonophore (whole)
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

## Directory Structure

```
project/
├── CLAUDE.md                  # This file
├── extract.py                 # Main extraction script
├── preprocess.py              # Image resizing utility
├── parse_fields.py            # Post-extraction field parser
├── requirements.txt
├── config.yaml                # Paths and model settings
└── output/
    └── results.xlsx           # Final spreadsheet
```

---

## Implementation Plan

### 1. `config.yaml`

```yaml
input_dir: /path/to/images          # Root directory; script recurses into subdirectories
output_file: output/results.xlsx
model: Qwen/Qwen2.5-VL-72B-Instruct
vllm_base_url: http://localhost:8000/v1
max_image_size: 1500                # Resize longest edge to this in pixels before sending
batch_size: 8                       # Concurrent requests to vLLM
log_file: output/extraction.log
```

### 2. `preprocess.py`

Resize images before sending to the model to reduce token cost while preserving legibility of small handwritten text.

```python
from PIL import Image
import io

def resize_image(image_path: str, max_size: int = 1500) -> bytes:
    """
    Resize image so longest edge <= max_size.
    Returns JPEG bytes. Preserves aspect ratio.
    """
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    if max(w, h) > max_size:
        scale = max_size / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
```

**Note on resolution**: Start at 1500px longest edge. If transcription accuracy is poor on small text, retry failing images at full resolution. The collage images are large (10 sub-images tiled), so 1500px gives roughly 300-400px per sub-image tile — adequate for printed text, possibly marginal for small handwriting. Consider testing at 2000px if accuracy is insufficient.

### 3. `extract.py` — Main Extraction Script

#### Directory Crawl

```python
import os
from pathlib import Path

EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}

def find_images(root_dir: str) -> list[Path]:
    paths = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in EXTENSIONS:
                paths.append(p)
    return sorted(paths)
```

#### DataMatrix Integer from Filename

```python
import re

def extract_integer_from_filename(filename: str) -> str | None:
    """
    Extract integer from filenames like DunnLab000199.png → '199'
    Also handles plain integers embedded in filename.
    """
    stem = Path(filename).stem
    # Try DunnLabXXXXXX pattern first
    m = re.search(r'DunnLab0*(\d+)', stem, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fallback: any integer in filename
    m = re.search(r'(\d+)', stem)
    if m:
        return m.group(1)
    return None
```

#### Prompt Design

The prompt is sent along with the image to Qwen2.5-VL. It must be precise about the collage layout and extraction targets.

```python
EXTRACTION_PROMPT = """
This image is a collage of cryovial photographs from the Dunn Lab.
The collage has two columns:
- LEFT column: photos taken BEFORE a barcode label was added. These show handwritten text on the vial.
- RIGHT column: photos taken AFTER a barcode label was added. These show a DataMatrix 2D barcode with a printed integer number below it.

Your tasks:
1. Find the printed integer below the DataMatrix barcode in the RIGHT column images. This is the DataMatrix ID.
2. Transcribe ALL handwritten text visible on the vial from the LEFT column images. Use the clearest view. Combine information across multiple left-column views if needed.

Return a JSON object with exactly these fields:
{
  "datamatrix_integer": "<integer as string, or null if not found>",
  "transcribed_text": "<exact transcription of all handwritten text, preserving line breaks as \\n>",
  "transcription_confidence": <integer 0-10, where 10=perfectly legible, 0=completely illegible>,
  "transcription_comments": "<note anything unusual: label obscured, unexpected layout, conflicting info across views, DataMatrix integer not matching filename, text partially cut off, etc. Empty string if nothing to note.>"
}

Be conservative with confidence scores. A score of 8+ means you are highly certain of every character. Ambiguous letters or digits should lower the score. If you cannot read a word at all, transcribe it as [illegible].

Return only the JSON object, no other text.
"""
```

#### vLLM API Call

Use the OpenAI-compatible endpoint that vLLM exposes:

```python
import asyncio
import base64
import json
from openai import AsyncOpenAI

async def extract_from_image(
    client: AsyncOpenAI,
    image_path: Path,
    model: str,
    max_image_size: int
) -> dict:
    img_bytes = resize_image(str(image_path), max_image_size)
    b64 = base64.b64encode(img_bytes).decode()

    response = await client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                },
                {
                    "type": "text",
                    "text": EXTRACTION_PROMPT
                }
            ]
        }],
        max_tokens=512,
        temperature=0.1  # Low temperature for deterministic extraction
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown code fences if model adds them
    raw = re.sub(r'^```(?:json)?\n?', '', raw)
    raw = re.sub(r'\n?```$', '', raw)
    return json.loads(raw)
```

#### Concurrent Batch Processing

```python
from asyncio import Semaphore

async def process_all(image_paths: list[Path], config: dict) -> list[dict]:
    client = AsyncOpenAI(
        base_url=config["vllm_base_url"],
        api_key="not-needed"  # vLLM doesn't require a real key
    )
    sem = Semaphore(config["batch_size"])
    results = []

    async def process_one(path: Path) -> dict:
        async with sem:
            filename_integer = extract_integer_from_filename(path.name)
            try:
                extracted = await extract_from_image(
                    client, path, config["model"], config["max_image_size"]
                )
            except Exception as e:
                extracted = {
                    "datamatrix_integer": None,
                    "transcribed_text": None,
                    "transcription_confidence": 0,
                    "transcription_comments": f"Extraction failed: {e}"
                }
            return {
                "image_file": str(path),
                "filename_integer": filename_integer,
                **extracted
            }

    tasks = [process_one(p) for p in image_paths]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        # Log progress
        print(f"Processed {len(results)}/{len(tasks)}: {result['image_file']}")

    return results
```

### 4. `parse_fields.py` — Structured Field Extraction

After transcription, parse the free text into structured columns. This is a second LLM call (or rule-based) step.

This can be done with a second call to Qwen2.5-VL (text-only, no image needed) or with Claude via API for higher accuracy on the parsing step — parsing is cheap since it's text-only.

```python
PARSE_PROMPT = """
You are parsing a handwritten label from a cryovial containing frozen siphonophore tissue (Dunn Lab, MBARI collections).

Transcribed label text:
{transcribed_text}

Extract the following fields. Return a JSON object:
{{
  "sample_number": "<e.g. 187 — integer only, no # symbol, or null>",
  "date": "<YYYYMMDD format, or null if not present>",
  "sampling_event": "<standardized event code, e.g. V401-SS2, D1041-D4, BW2, or null>",
  "species": "<corrected full species name, e.g. Nanomia bijuga, Bargmannia elongata, or null if absent/illegible>",
  "tissue": "<standardized tissue name, e.g. nectophore, gastrozooid, growth zone, whole, or null if not specified>",
  "notes": "<anything on the label not fitting other fields, or empty string>",
  "parse_confidence": <integer 0-10>,
  "parse_comments": "<note ambiguities, corrected spellings, fields that could not be parsed, etc.>"
}}

Sampling event vehicle prefixes: V=Ventana, D=Doc Ricketts, T=Tiburon, W=Western Flyer, BW=blue water.
Sampler codes: SS=suction sampler, D=detritus sampler, N=net, MC=midwater collection.

Species corrections (apply these):
- Nanomia / Nano / N bijuga → Nanomia bijuga
- B elongata / Bargmannia elong → Bargmannia elongata
- Agalma / A elegans → Agalma elegans
- Muggiaea / M atlantica → Muggiaea atlantica
- Physo / Physophora → Physophora hydrostatica
- Rosacea / R cymbiformis → Rosacea cymbiformis
- Cordagalma → Cordagalma ordinatum
- Apolemia → Apolemia sp.

Tissue corrections:
- necto / N followed by number → nectophore (record number if present, e.g. nectophore 1)
- GZ → growth zone
- gastro → gastrozooid
- gonzo → gonozooid
- pneu → pneumatophore
- sipho (if tissue, not species) → whole siphonophore
- stem → stem

Return only the JSON object.
"""
```

### 5. Spreadsheet Output

Use `openpyxl` to write results. One row per image file.

```python
import openpyxl
from openpyxl.styles import PatternFill, Font

COLUMNS = [
    "image_file",
    "filename_integer",
    "datamatrix_integer",
    "datamatrix_match",        # True/False: filename_integer == datamatrix_integer
    "transcribed_text",
    "transcription_confidence",
    "transcription_comments",
    "sample_number",
    "date",
    "sampling_event",
    "species",
    "tissue",
    "notes",
    "parse_confidence",
    "parse_comments",
]

def write_spreadsheet(rows: list[dict], output_path: str):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vial Labels"

    # Header row
    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")
    for col, name in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col, value=name)
        cell.fill = header_fill
        cell.font = header_font

    # Highlight fills for QC
    red_fill = PatternFill("solid", fgColor="FFCCCC")
    yellow_fill = PatternFill("solid", fgColor="FFFACC")

    for row_idx, row in enumerate(rows, 2):
        for col, name in enumerate(COLUMNS, 1):
            cell = ws.cell(row=row_idx, column=col, value=row.get(name, ""))

        # Color-code rows by confidence
        t_conf = row.get("transcription_confidence", 10)
        p_conf = row.get("parse_confidence", 10)
        min_conf = min(t_conf, p_conf)
        if min_conf <= 3:
            for col in range(1, len(COLUMNS) + 1):
                ws.cell(row=row_idx, column=col).fill = red_fill
        elif min_conf <= 6:
            for col in range(1, len(COLUMNS) + 1):
                ws.cell(row=row_idx, column=col).fill = yellow_fill

        # Flag DataMatrix mismatches
        if row.get("datamatrix_match") is False:
            ws.cell(row=row_idx, column=COLUMNS.index("datamatrix_match") + 1).fill = red_fill

    # Auto-width columns
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)

    wb.save(output_path)
    print(f"Saved {len(rows)} rows to {output_path}")
```

---

## Running the Pipeline

### Setup

```bash
# Install dependencies
pip install openai pillow openpyxl pyyaml

# Start vLLM server (requires Qwen2.5-VL-72B weights)
vllm serve Qwen/Qwen2.5-VL-72B-Instruct \
    --port 8000 \
    --max-model-len 8192 \
    --limit-mm-per-prompt image=1
```

### Run

```bash
python extract.py --config config.yaml
```

### Recommended Test Workflow

1. Run on 20-30 representative images first: `python extract.py --config config.yaml --limit 30`
2. Inspect the spreadsheet — look at low-confidence rows and check transcription_comments
3. Tune resolution (`max_image_size`) and prompt if needed
4. Run full batch

---

## QC and Review

The spreadsheet uses color coding:
- **Red rows**: transcription or parse confidence ≤ 3 — needs manual review
- **Yellow rows**: confidence 4–6 — spot check recommended
- **White rows**: confidence ≥ 7 — likely good

The `datamatrix_match` column flags any case where the integer extracted from the image differs from the integer in the filename — these should always be investigated.

The `transcription_comments` column will flag:
- Images with unexpected layouts
- Labels that are partially obscured or rotated
- Cases where the DataMatrix printed integer was not legible
- Conflicting information across collage tiles

---

## Known Challenges

- **Handwriting legibility**: Cursive or rushed writing will reduce confidence. Common ambiguities: `0` vs `O`, `1` vs `I`, `5` vs `S`, `SS` vs `55`.
- **Collage layout**: The model must correctly identify left vs right columns. The prompt explicitly describes this; verify on a sample that it's doing so correctly.
- **Species abbreviations**: Many non-standard abbreviations exist. Extend the species correction table in `parse_fields.py` as new species are encountered.
- **Curved labels**: Vials are cylindrical; some angles may show text wrapped around the curve. The multi-view collage mitigates this.
- **Pre-label vs post-label confusion**: Some right-column images also show handwritten text (visible above or below the barcode label). The model should prioritize left-column handwritten text but can supplement from right-column if clearer.

---

## Version Control

Raw images and pipeline output must never be committed. A `.gitignore` should include:

```
# Raw images
*.jpg
*.jpeg
*.png
*.tif
*.tiff

# Pipeline output
output/

# Misc
__pycache__/
*.pyc
.env
```

Large image data lives on scratch (`/nfs/roberts/scratch/pi_cwd7/cwd7/`) or project storage — not in the repo.

---

## Dependencies (`requirements.txt`)

```
openai>=1.0.0
pillow>=10.0.0
openpyxl>=3.1.0
pyyaml>=6.0
```
