#!/bin/bash
#SBATCH --job-name=vial_scan
#SBATCH --partition=gpu_h200
#SBATCH --gpus=h200:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --output=output/slurm_%j.out
#SBATCH --mail-type=END,FAIL

module reset
module load miniconda/24.7.1 CUDA/12.6.0
eval "$(conda shell.bash hook)"
conda activate vial_scan

# Start vLLM server in background
vllm serve Qwen/Qwen2.5-VL-72B-Instruct \
    --port 8000 \
    --quantization fp8 \
    --max-model-len 8192 \
    --limit-mm-per-prompt '{"image": 1}' &
VLLM_PID=$!

# Wait for server to be ready (poll up to 10 minutes)
echo "Waiting for vLLM server..."
SERVER_READY=0
for i in $(seq 1 60); do
    # Abort immediately if vLLM process has already died
    if ! kill -0 "$VLLM_PID" 2>/dev/null; then
        echo "ERROR: vLLM server process exited unexpectedly. Aborting job."
        exit 1
    fi
    if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "vLLM ready after ${i}x10s"
        SERVER_READY=1
        break
    fi
    sleep 10
done

if [ "$SERVER_READY" -eq 0 ]; then
    echo "ERROR: vLLM server did not become ready within 10 minutes. Aborting job."
    kill "$VLLM_PID" 2>/dev/null
    exit 1
fi

# Run extraction
python extract.py --config config.yaml

# Shut down vLLM
kill "$VLLM_PID"
