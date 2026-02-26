# Vial Label Extraction Pipeline

Extracts structured metadata from cryovial collage images using Qwen2.5-VL-72B served via vLLM on the Bouchet HPC cluster.

See [CLAUDE.md](CLAUDE.md) for domain conventions (label formats, species abbreviations, collage layout).

---

## GPU partition

Use **`gpu_h200`** (H200, 141 GB vRAM). Qwen2.5-VL-72B requires ~144 GB in BF16 or ~72 GB in FP8.

- **1× H200**: run in FP8 (`--quantization fp8`), fits with room for KV cache
- **2× H200**: run in BF16 (`--tensor-parallel-size 2`), best accuracy

The `gpu` partition (RTX 5000 Ada, 32 GB) would need 5 cards with tensor parallelism — not worth the complexity.

---

## Setup

```bash
# Load conda and create environment (run once)
module load miniconda
conda create -n vial_scan python=3.11 -y
conda activate vial_scan
pip install -r requirements.txt

# Also install vLLM (large; do this inside an interactive job)
# CUDA module must be loaded or the build process will fail with CUDA_HOME errors
module load CUDA/12.6.0
pip install vllm
```

---

## Interactive session (testing and development)

Use this when iterating on prompts or debugging. Wrap everything in `tmux` to protect against disconnection.

```bash
tmux new -s vial
```

### Step 1: Start the vLLM server (1 H200, FP8)

```bash
salloc -p gpu_h200 -t 6:00:00 --gpus=h200:1 --cpus-per-task=8 --mem=64G
module load miniconda/24.7.1 CUDA/12.6.0
conda activate vial_scan
vllm serve Qwen/Qwen2.5-VL-72B-Instruct \
    --port 8000 \
    --quantization fp8 \
    --max-model-len 8192 \
    --limit-mm-per-prompt image=1
```

The server is ready when you see `Application startup complete`.

### Step 2: Run the extraction (separate tmux window)

Open a new tmux window (`Ctrl-b c`) — the extraction script runs on the login node and sends requests to the vLLM server over localhost.

```bash
cd /nfs/roberts/scratch/pi_cwd7/cwd7/vial_scan
module load miniconda/24.7.1
conda activate vial_scan

# Test on a small subset first
python extract.py --config config.yaml --limit 10

# Inspect output/results.xlsx, then run full batch
python extract.py --config config.yaml
```

---

## Batch job (full production run)

For large batches, run vLLM and the extraction script as a single batch job. The script waits for vLLM to be ready before starting.

The batch script is [`run_pipeline.sh`](run_pipeline.sh). It starts vLLM in the background, polls until the server is ready, runs the extraction, then shuts vLLM down.

Submit with:

```bash
mkdir -p output
sbatch run_pipeline.sh
```

Monitor:

```bash
squeue --me
tail -f output/slurm_<jobid>.out
```

---

## Two-H200 variant (BF16, best accuracy)

If FP8 accuracy is insufficient, use two H200s with tensor parallelism:

```bash
# Interactive
salloc -p gpu_h200 -t 6:00:00 --gpus=h200:2 --cpus-per-task=16 --mem=128G

# vLLM command
vllm serve Qwen/Qwen2.5-VL-72B-Instruct \
    --port 8000 \
    --tensor-parallel-size 2 \
    --max-model-len 8192 \
    --limit-mm-per-prompt image=1
```

To use this in a batch job, edit [`run_pipeline.sh`](run_pipeline.sh): change `--gpus=h200:2`, `--cpus-per-task=16`, `--mem=128G`, and add `--tensor-parallel-size 2` to the `vllm serve` command.

---

## Configuration (`config.yaml`)

| Key | Default | Notes |
|-----|---------|-------|
| `input_dir` | `images` | Recursively searched for PNG/JPG/TIF |
| `output_file` | `output/results.xlsx` | Created automatically |
| `model` | `Qwen/Qwen2.5-VL-72B-Instruct` | Must match vLLM served model |
| `vllm_base_url` | `http://localhost:8000/v1` | Change if running on a different node |
| `max_image_size` | `1500` | Longest edge in pixels; try 2000 if accuracy is poor |
| `batch_size` | `8` | Concurrent requests to vLLM; reduce if OOM |
| `log_file` | `output/extraction.log` | Append-mode log |

---

## Output spreadsheet columns

| Column | Description |
|--------|-------------|
| `image_file` | Path to collage image |
| `filename_integer` | Integer parsed from filename (e.g. `DunnLab000199.png` → `199`) |
| `datamatrix_integer` | Integer read from printed text below barcode in image |
| `datamatrix_match` | `True`/`False`/`None` — mismatch flagged red |
| `transcribed_text` | Raw handwritten text from left-column images |
| `transcription_confidence` | 0–10 score from model |
| `transcription_comments` | Model notes on legibility issues |
| `sample_number` | Parsed vial number (integer, no `#`) |
| `date` | Normalized `YYYYMMDD` |
| `sampling_event` | Standardized code, e.g. `V401-SS2` |
| `species` | Corrected full species name |
| `tissue` | Standardized tissue name |
| `notes` | Remaining label text |
| `parse_confidence` | 0–10 score from parse step |
| `parse_comments` | Notes on ambiguities and corrections |

Row colors: red = confidence ≤ 3 (needs review), yellow = 4–6 (spot check), white = ≥ 7.

---

## Recommended test workflow

1. Run on 7 test images: `python extract.py --config config.yaml --limit 7`
2. Open `output/results.xlsx` — check red/yellow rows and `transcription_comments`
3. If handwriting is hard to read, increase `max_image_size` to `2000` in `config.yaml` and rerun
4. Once satisfied, run full batch (interactive or via `sbatch run_pipeline.sh`)
