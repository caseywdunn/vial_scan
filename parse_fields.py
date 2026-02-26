"""
parse_fields.py — Parse structured metadata from transcribed vial label text.

Performs a second LLM call (text-only) to extract structured fields from raw
transcribed text produced by extract.py.
"""

import json
import re

from openai import AsyncOpenAI

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
- Nanomia / Nano / N bijuga / Nanomia SGZ / NANOMIA → Nanomia bijuga
- B elongata / Bargmannia elong → Bargmannia elongata
- Agalma / A elegans → Agalma elegans
- Muggiaea / M atlantica → Muggiaea atlantica
- Physo / Physophora → Physophora hydrostatica
- Rosacea / R cymbiformis → Rosacea cymbiformis
- Cordagalma → Cordagalma ordinatum
- Apolemia → Apolemia sp.

Tissue corrections:
- necto / nectos / N followed by number → nectophore (record qualifier if present, e.g. "young nectophore", "mature nectophore", "nectophore 1")
- young nectos / young necto → young nectophore
- mature necto / mature nectos → mature nectophore
- GZ / SGZ / siphosomal growth zone / Siphosomal GZ → siphosomal growth zone
- gastro → gastrozooid
- gonzo → gonozooid
- pneu / pneum / pneumatophore → pneumatophore
- palpon / palpons / young palpons → palpons
- sipho (if tissue, not species) → whole siphonophore
- stem → stem
- young male / young female / mature male / mature female → record as-is in tissue field (denotes developmental stage/sex)

Return only the JSON object.
"""


async def parse_transcription(
    client: AsyncOpenAI,
    model: str,
    transcribed_text: str,
) -> dict:
    """
    Call the LLM to parse structured fields from transcribed label text.
    Returns a dict with sample_number, date, sampling_event, species, tissue,
    notes, parse_confidence, parse_comments.
    """
    prompt = PARSE_PROMPT.format(transcribed_text=transcribed_text)

    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=512,
        temperature=0.1,
    )

    raw = response.choices[0].message.content.strip()
    raw = re.sub(r'^```(?:json)?\n?', '', raw)
    raw = re.sub(r'\n?```$', '', raw)
    return json.loads(raw)
