#!/usr/bin/env bash
# Run the full 4-config SAM2 fine-tune sweep on this machine and write
# JSON receipts to benchmarks/plantvillage/results/.
#
# Safe to run unattended (sleep mode): every config is best-effort, a
# failure on one does NOT abort the rest. Per-run stdout/stderr is teed
# to logs/ for post-mortem.
#
# Usage:
#   bash benchmarks/plantvillage/run_sweep.sh                 # default 1000 steps
#   STEPS=2000 bash benchmarks/plantvillage/run_sweep.sh      # override step count
#   VARIANT=tiny bash benchmarks/plantvillage/run_sweep.sh    # use hiera_tiny
#   COUNT=2000 bash benchmarks/plantvillage/run_sweep.sh      # 2000 pairs/class
#
# Requirements:
#   - .venv exists with deps installed (uv pip install -e apps/ml -e vendor/sam2)
#   - checkpoints/sam2.1_hiera_base_plus.pt present (and tiny if VARIANT=tiny)

set -u  # unset vars are errors; but DO NOT set -e (we want to keep going)

cd "$(dirname "$0")/../.."  # repo root
REPO_ROOT="$PWD"

STEPS="${STEPS:-1000}"
VARIANT="${VARIANT:-base_plus}"
COUNT="${COUNT:-1000}"
TRAIN_IMAGES="${TRAIN_IMAGES:-500}"
EVAL_IMAGES="${EVAL_IMAGES:-100}"

LOG_DIR="benchmarks/plantvillage/logs"
RESULTS_DIR="benchmarks/plantvillage/results"
DATASET_DIR="benchmarks/plantvillage/dataset"
mkdir -p "$LOG_DIR" "$RESULTS_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SUMMARY="$LOG_DIR/sweep_${STAMP}.summary.txt"

echo "==> sweep starting $STAMP" | tee "$SUMMARY"
echo "    repo:    $REPO_ROOT" | tee -a "$SUMMARY"
echo "    variant: $VARIANT" | tee -a "$SUMMARY"
echo "    steps:   $STEPS" | tee -a "$SUMMARY"
echo "    train:   $TRAIN_IMAGES  eval: $EVAL_IMAGES" | tee -a "$SUMMARY"

# ---- 0. activate venv ----
if [[ ! -d .venv ]]; then
  echo "FATAL: .venv missing. Run: uv venv --python 3.11 && uv pip install -e apps/ml -e vendor/sam2" | tee -a "$SUMMARY"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# ---- 1. build dataset if missing ----
existing=$(ls "$DATASET_DIR/images" 2>/dev/null | wc -l | tr -d ' ')
need=$((COUNT * 5))
if [[ "$existing" -lt "$need" ]]; then
  echo "==> building dataset ($COUNT/class -> $need pairs)" | tee -a "$SUMMARY"
  python benchmarks/plantvillage/seed_data.py --count "$COUNT" --workers 32 \
      2>&1 | tee "$LOG_DIR/seed_${STAMP}.log"
else
  echo "==> dataset already has $existing pairs (>= $need), skipping seed" | tee -a "$SUMMARY"
fi

# ---- 2. configs to run ----
# Note: skip --config full with base_plus on M1 Pro (16 GB) -> OOM expected.
# If you really want it, set VARIANT=tiny.
CONFIGS=(vanilla lora_r8 lora_r16 full_decoder)
if [[ "$VARIANT" == "tiny" ]]; then
  CONFIGS+=(full)
fi

run_one() {
  local cfg="$1"
  local steps="$2"
  local log="$LOG_DIR/${cfg}_${VARIANT}_${STAMP}.log"
  local t0 t1
  t0=$(date +%s)
  echo "" | tee -a "$SUMMARY"
  echo "==> [$(date -u +%H:%M:%SZ)] config=$cfg steps=$steps -> $log" | tee -a "$SUMMARY"
  if python benchmarks/plantvillage/benchmark_train.py \
        --config "$cfg" --variant "$VARIANT" \
        --max-steps "$steps" \
        --train-images "$TRAIN_IMAGES" --eval-images "$EVAL_IMAGES" \
        > "$log" 2>&1; then
    t1=$(date +%s)
    tail -3 "$log" | sed 's/^/    /' | tee -a "$SUMMARY"
    echo "    OK  ($((t1 - t0))s)" | tee -a "$SUMMARY"
  else
    t1=$(date +%s)
    echo "    FAIL ($((t1 - t0))s) - last 15 lines of $log:" | tee -a "$SUMMARY"
    tail -15 "$log" | sed 's/^/      /' | tee -a "$SUMMARY"
  fi
}

# ---- 3. run sweep ----
SWEEP_T0=$(date +%s)
run_one vanilla       0       # eval-only, no training
run_one lora_r8       "$STEPS"
run_one lora_r16      "$STEPS"
run_one full_decoder  "$STEPS"
if [[ "$VARIANT" == "tiny" ]]; then
  run_one full        "$STEPS"
fi
SWEEP_T1=$(date +%s)

echo "" | tee -a "$SUMMARY"
echo "==> sweep done in $((SWEEP_T1 - SWEEP_T0))s" | tee -a "$SUMMARY"

# ---- 4. list result JSONs ----
echo "" | tee -a "$SUMMARY"
echo "==> result JSONs in $RESULTS_DIR:" | tee -a "$SUMMARY"
ls -lh "$RESULTS_DIR"/*.json 2>/dev/null | awk '{print "    "$NF}' | tee -a "$SUMMARY"

echo "" | tee -a "$SUMMARY"
echo "summary written to $SUMMARY"
