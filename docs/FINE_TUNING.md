# Fine-Tuning & Model Adaptation (SAM2 + SAM3)

Anvil combines two complementary segmentation architectures—**SAM3 (`facebook/sam3`)** for open-vocabulary discovery and **SAM2 (`sam2.1_hiera_b+`)** for cached-embedding interactive refinement and in-loop **LoRA fine-tuning**.

## Architecture Comparison

| Dimension | Meta `sam2/training` (Full FT) | Meta `sam3/train` (Full FT) | **Anvil In-Loop SAM2 LoRA** |
| --- | --- | --- | --- |
| **Strategy** | Full parameter fine-tuning | Full detector + tracker fine-tuning | **Mask-decoder attention LoRA** on cached encoder features |
| **Total / trainable params** | ~80M / ~80M | ~840M / ~840M | ~80M total / **~47K trainable** (rank 8) |
| **Hardware requirement** | Multi-GPU CUDA cluster (40–80 GB VRAM) | Multi-GPU CUDA cluster (24–32+ GB VRAM) | **Single laptop CPU / MPS** or single GPU |
| **Training latency** | Hours to days | Hours to days | **Seconds to minutes** |
| **Artifact size** | ~700 MB checkpoint | ~3.2 GB checkpoint | **~200 KB `.pt` adapter** |
| **Best suited for** | Large-scale video / backbone shift | Large-scale open-vocabulary retraining | **Rapid domain specialization inside an active labeling project** |

---

## The Production Loop: SAM3 Discovery → SAM2 LoRA Specialization

Rather than requiring a 32 GB GPU to fine-tune SAM3's 840M-parameter vision-language backbone on small project batches, Anvil supports a two-stage workflow that runs entirely on local hardware:

1. **Cold-start discovery with SAM3 (`Model: base SAM3`):**
   - Select **base SAM3** in Workview and run open-vocabulary concept segmentation (`Find all ✨` with a tunable confidence threshold) or point/box prompts to segment initial instances across unannotated images.
2. **Curate and specialize with SAM2 LoRA (`Train new model`):**
   - Review or adjust the polygon boundaries in Workview, then train a **SAM2 mask-decoder LoRA adapter** on the approved dataset.
   - Because SAM2's image embeddings (`embeddings/<image_id>.npz`) are pre-computed once on upload, training ~47K LoRA parameters completes in seconds to minutes on CPU and immediately powers both interactive clicks and the `Next uncertain ✨` active-learning queue.

---

## How SAM2 Mask-Decoder LoRA Works

### 1. Encoder Cache (Zero Encoder Overhead During Training)

SAM2's Hiera-B+ image encoder (~80M parameters) is the computationally expensive stage. Anvil pre-computes encoder features once when an image is uploaded:

```
upload ─► worker ─► predictor.set_image(img) ─► embeddings/<id>.npz
```

During `train_lora()`, Anvil restores the cached tensor via `_restore_features(predictor, npz_path)`. **The image encoder never runs in the training loop**—eliminating encoder forward passes, backward passes, and activation memory.

### 2. Low-Rank Adaptation on Mask-Decoder Attention

`apps/ml/anvil_ml/lora.py` injects trainable low-rank adapters into every `nn.Linear` attention projection (`q_proj`, `k_proj`, `v_proj`, `out_proj`) inside `model.sam_mask_decoder`:

```python
# y = x @ W_frozen + (x @ A @ B) * (alpha / r)
# Only lora_A (r x in_features) and lora_B (out_features x r) carry gradients.
```

- **Image encoder:** Frozen (and bypassed via `.npz` cache)
- **Prompt encoder:** Frozen
- **Mask decoder base weights:** Frozen
- **Mask decoder LoRA weights:** Trainable (~47K parameters at `rank=8, alpha=16`)

### 3. Objective & Per-Class Validation

- **Loss:** `BCEWithLogitsLoss + DiceLoss` between predicted mask logits and the rasterized ground-truth polygon/bbox mask.
- **Validation:** Held-out split (`eval_split=0.2` default) evaluated every epoch; records `base_val_iou`, `final_val_iou`, `val_iou_per_epoch`, and `val_iou_per_class`.
- **A/B Comparison:** `POST /api/projects/{id}/model-versions/compare` evaluates any two adapters (or base SAM2) on the exact same sampled annotations and reports per-class and per-image IoU deltas.

### 4. Portable Adapter Persistence

`save_lora()` serializes only the LoRA `A`/`B` tensors and `(rank, alpha)` metadata into an adapter file (~200 KB). `load_lora()` reads the metadata, injects matching `LoRALinear` wrappers, and copies the weights.

---

## When Decoder-Only LoRA Is Not Enough (and SAM3 Adaptation)

1. **Severe visual backbone shift (non-optical modalities):**
   For X-ray, SAR, or specialized microscopy where Hiera-B+ features lack edge boundaries, decoder-only LoRA cannot invent missing visual features; encoder LoRA or full fine-tuning on a GPU host is required.
2. **Domain-specific text concepts in SAM3:**
   When SAM3's zero-shot confidence is low on specialized terminology (for example, grading subtle defect categories), lower the **SAM3 threshold slider** (`0.10–0.30`) in Workview to surface candidate masks, or switch to SAM2 LoRA after bootstrapping 20–50 examples. Native SAM3 cross-attention LoRA / concept-grounding adaptation on GPU is tracked in [SAM3_INTEGRATION.md](SAM3_INTEGRATION.md).

## Reference Implementation

- `apps/ml/anvil_ml/training.py` — `train_lora()`, `_evaluate()`, `evaluate_with_adapter()`
- `apps/ml/anvil_ml/lora.py` — `LoRALinear`, `inject_lora()`, `save_lora()`, `load_lora()`
- `apps/ml/anvil_ml/providers/sam3_provider.py` — `SAM3Provider` (`predict` + `predict_concept`)
