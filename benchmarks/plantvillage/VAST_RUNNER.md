# Running the SAM2 Fine-Tuning Benchmark on Vast.ai

End-to-end recipe to benchmark Anvil's SAM2 fine-tuning across cloud GPUs
on a tight ($10) budget.

## 1. Pick GPUs

Sign in to https://vast.ai. From the search page, recommended cells for
the launch matrix:

| GPU | Typical $/hr | Min RAM | Notes |
|---|---|---|---|
| RTX 4090 24GB | $0.30-0.40 | 32GB | gamer/prosumer story |
| A10G 24GB | $0.40-0.50 | 32GB | production sweet spot |
| A100 40GB | $0.80-1.20 | 40GB | enterprise |
| H100 80GB | $2.50-3.50 | 64GB | premium |

Filter: CUDA >= 12.1, disk >= 30 GB, has internet, "interruptible: false".

## 2. Spin up an instance

1. Image template: `pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime` (or any
   recent PyTorch+CUDA image)
2. On-demand (not interruptible) so Vast can't preempt mid-benchmark
3. Open ports: 22 (SSH only)
4. Click "Rent"

Wait ~1 min for status to go green, then SSH in:

```bash
ssh root@ssh<N>.vast.ai -p <port>
```

## 3. Set up the repo + dataset (once per instance)

```bash
# 1. clone
cd /root && git clone https://github.com/ssgaur/anvil-cv.git && cd anvil-cv

# 2. install uv + python deps (~3 min)
pip install -U uv
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e apps/ml -e vendor/sam2

# 3. download SAM2 base_plus checkpoint (~700 MB, ~30 s)
mkdir -p checkpoints
curl -L -o checkpoints/sam2.1_hiera_base_plus.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt

# (Optional, for full fine-tune of the small variant)
curl -L -o checkpoints/sam2.1_hiera_tiny.pt \
  https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt

# 4. build dataset (~5 min for 5,000 pairs)
uv pip install requests pillow numpy
python benchmarks/plantvillage/seed_data.py --count 1000 --workers 32

# verify
ls benchmarks/plantvillage/dataset/images | wc -l   # expect 5000
ls benchmarks/plantvillage/dataset/masks  | wc -l   # expect 5000
```

## 4. Run the benchmark sweep

For each GPU you spin up, run the four configs back-to-back:

```bash
# Always run the no-train baseline first (fast, sets the IoU floor)
python benchmarks/plantvillage/benchmark_train.py --config vanilla --max-steps 0

# LoRA configs (~5-10 min each on a 4090, ~3 min on A100)
python benchmarks/plantvillage/benchmark_train.py --config lora_r8  --max-steps 1000
python benchmarks/plantvillage/benchmark_train.py --config lora_r16 --max-steps 1000

# Full fine-tune of mask decoder only (~10-20 min)
python benchmarks/plantvillage/benchmark_train.py --config full_decoder --max-steps 1000

# Full fine-tune of decoder + image encoder (~20-60 min, big VRAM)
# If OOM on a 24GB card, try --gradient-checkpointing or --variant tiny
python benchmarks/plantvillage/benchmark_train.py --config full --max-steps 1000
```

All results land in `benchmarks/plantvillage/results/<config>_<variant>_<device>_<timestamp>.json`.

## 5. Collect results

After all runs finish:

```bash
# tar the JSON results to upload back to your laptop / commit to repo
tar czf results-$(hostname).tgz benchmarks/plantvillage/results/

# from your laptop:
scp -P <port> root@ssh<N>.vast.ai:/root/anvil-cv/results-*.tgz .
tar xzf results-*.tgz
git add benchmarks/plantvillage/results/ && git commit -m "bench: add results from <gpu>"
```

## 6. Stop the instance (so billing stops)

In the Vast.ai console: hover the instance row -> click the trash icon.
Vast bills per second; stopping = billing stops immediately.

## Cost back-of-envelope

Per GPU (4 configs each):

| GPU | Setup | Sweep wall | Sweep cost |
|---|---|---|---|
| RTX 4090 | ~5 min | ~30-50 min | ~$0.40 |
| A10G | ~5 min | ~25-40 min | ~$0.50 |
| A100 | ~5 min | ~15-30 min | ~$0.80 |
| H100 | ~5 min | ~10-20 min | ~$1.50 |

Total for the 4-GPU matrix: ~$3-5. With M1 Pro local (free), the entire
benchmark fits well under $10.

## Tips

- **Use a tmux session** so disconnects don't kill long runs.
- **Streaming logs**: `python ... 2>&1 | tee runlog.txt` so you can grep
  later if something goes wrong.
- **OOM on 24GB cards** for `--config full` is expected with `base_plus`.
  Either drop to `--variant tiny` or add `--gradient-checkpointing`.
- **MPS-only flag** `--device mps` is for the M1 Pro local run; on Vast
  it auto-detects CUDA, no flag needed.
- **GPU memory plotting**: peak_memory_gb in the JSON is the reliable
  number. Don't trust nvidia-smi snapshot (transient).

## What to commit back to the repo

After all GPU runs:

```
benchmarks/plantvillage/results/
  vanilla_base_plus_mps_<ts>.json        (M1 Pro)
  vanilla_base_plus_cuda_<ts>.json       (any cloud GPU; identical numbers)
  lora_r8_base_plus_mps_<ts>.json
  lora_r8_base_plus_cuda_<ts>.json       (one per cloud GPU)
  ...
```

These are the **trust receipts** for the blog post. They get committed
to the repo so anyone can grep and verify the numbers we publish.
