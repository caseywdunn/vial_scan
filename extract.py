"""
extract.py — Main extraction script for vial label pipeline.

Usage:
    python extract.py --config config.yaml
    python extract.py --config config.yaml --limit 30
"""

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import sys
from asyncio import Semaphore
from pathlib import Path

import yaml
from openai import AsyncOpenAI

from preprocess import resize_image
from parse_fields import parse_transcription
from spreadsheet import write_spreadsheet

EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}

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


def find_images(root_dir: str) -> list[Path]:
    paths = []
    for dirpath, _, filenames in os.walk(root_dir):
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in EXTENSIONS:
                paths.append(p)
    return sorted(paths)


def extract_integer_from_filename(filename: str) -> str | None:
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


async def extract_from_image(
    client: AsyncOpenAI,
    image_path: Path,
    model: str,
    max_image_size: int,
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
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()
    # Strip markdown code fences if model adds them
    raw = re.sub(r'^```(?:json)?\n?', '', raw)
    raw = re.sub(r'\n?```$', '', raw)
    return json.loads(raw)


async def process_all(image_paths: list[Path], config: dict) -> list[dict]:
    client = AsyncOpenAI(
        base_url=config["vllm_base_url"],
        api_key="not-needed",
    )
    sem = Semaphore(config["batch_size"])
    results = []
    total = len(image_paths)

    async def process_one(path: Path) -> dict:
        async with sem:
            filename_integer = extract_integer_from_filename(path.name)
            try:
                extracted = await extract_from_image(
                    client, path, config["model"], config["max_image_size"]
                )
            except Exception as e:
                logging.error(f"Extraction failed for {path}: {e}")
                extracted = {
                    "datamatrix_integer": None,
                    "transcribed_text": None,
                    "transcription_confidence": 0,
                    "transcription_comments": f"Extraction failed: {e}",
                }

            # Parse transcribed text into structured fields
            transcribed = extracted.get("transcribed_text")
            if transcribed:
                try:
                    parsed = await parse_transcription(client, config["model"], transcribed)
                except Exception as e:
                    logging.error(f"Parsing failed for {path}: {e}")
                    parsed = {
                        "sample_number": None,
                        "date": None,
                        "sampling_event": None,
                        "species": None,
                        "tissue": None,
                        "notes": "",
                        "parse_confidence": 0,
                        "parse_comments": f"Parsing failed: {e}",
                    }
            else:
                parsed = {
                    "sample_number": None,
                    "date": None,
                    "sampling_event": None,
                    "species": None,
                    "tissue": None,
                    "notes": "",
                    "parse_confidence": 0,
                    "parse_comments": "No transcribed text to parse.",
                }

            dm_from_image = extracted.get("datamatrix_integer")
            datamatrix_match = (
                dm_from_image == filename_integer
                if dm_from_image is not None and filename_integer is not None
                else None
            )

            return {
                "image_file": str(path),
                "filename_integer": filename_integer,
                "datamatrix_match": datamatrix_match,
                **extracted,
                **parsed,
            }

    tasks = [process_one(p) for p in image_paths]
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
        logging.info(f"Processed {len(results)}/{total}: {result['image_file']}")
        print(f"[{len(results)}/{total}] {result['image_file']}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Extract vial label data from collage images.")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML file.")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N images (for testing).")
    args = parser.parse_args()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    os.makedirs(os.path.dirname(config["output_file"]), exist_ok=True)
    os.makedirs(os.path.dirname(config["log_file"]), exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(config["log_file"]),
            logging.StreamHandler(sys.stdout),
        ],
    )

    image_paths = find_images(config["input_dir"])
    if not image_paths:
        print(f"No images found in {config['input_dir']}")
        sys.exit(1)

    if args.limit:
        image_paths = image_paths[: args.limit]

    print(f"Found {len(image_paths)} images. Starting extraction...")
    results = asyncio.run(process_all(image_paths, config))

    write_spreadsheet(results, config["output_file"])


if __name__ == "__main__":
    main()
