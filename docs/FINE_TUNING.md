# Fine-tuning SAM2 with LoRA on a laptop

This is Anvil's product wedge. V7 Darwin gives you base SAM2 auto-annotate. Anvil lets you click **Train** and get a SAM2 specialized to *your* classes — running on your MacBook.

## TL;DR

|  | Meta's `training/train.py` | **Anvil** |
| --- | --- | --- |
| Strategy | full fine-tune | **LoRA** on mask decoder only |
| Trainable params | 100M+ | **~47K** (rank 8) |
| Hardware | 8× A100/H100, FSDP | **M1 16 GB, CPU** |
| Task | video segmentation (MOSE) | image segmentation |
| Memory needed | 40–80 GB VRAM | a few hundred MB |
| Training time | hours–days | **seconds–minutes** |
| Output | new 700 MB checkpoint | adapter, ~200 KB |
| When to use | brand-new domain (medical, sat) + cluster | "specialize SAM2 on my classes" |

## How it works

### 1. Encoder cache (the prerequisite)

SAM2's image encoder (Hiera-B+, ~80M params, ViT) is the expensive part. We precompute it **once** at image upload time:

```
upload  ─►  worker  ─►  predictor.set_image(img)  ─►  embeddings/<id>.npz
```

At training time, we just `_restore_features(predictor, npz_path)`. **The encoder never runs in the training loop.** No encoder forward, no encoder backward, no encoder gradients in memory.

### 2. LoRA on mask decoder attention

`apps/ml/anvil_ml/lora.py` (~50 lines):

```python
# Walk model.sam_mask_decoder.* and find every nn.Linear inside attention modules.
# Wrap each with LoRALinear(W_orig, rank=r, alpha=α):
#     y = x @ W_orig + (x @ A @ B) * (α / r)
# Only A (r×d) and B (d×r) are trainable. W_orig is frozen.
```

Resulting setup:

- Image encoder: **frozen** (also never runs during training)
- Prompt encoder: **frozen**
- Memory attention: **frozen** (we don't fine-tune for video)
- Mask decoder base weights: **frozen**
- Mask decoder attention LoRA matrices: **trainable** (~47K params)

### 3. Loss + eval

- Loss: BCE-with-logits + Dice on rasterized GT mask (polygon → mask)
- Eval per epoch: held-out IoU per item, aggregated per class → `metrics.val_iou_per_class`
- Multi-mask output: take best by predicted score

### 4. Adapter persistence

`save_lora()` writes only the A/B matrices + a tiny meta blob (rank, alpha, list of wrapped modules) to a `.pt` file. Roughly **200 KB** per adapter.

`load_lora()` reads meta → calls `inject_lora()` with matching config → loads the matrices. **Don't call `inject_lora()` separately before `load_lora()` — it'll wrap every Linear twice.** (See gotcha #3 in `.github/copilot-instructions.md`.)

## Why this works (intuitively)

The image encoder learns generic visual features (edges, textures, parts) that transfer well across most natural-image domains. The **mask decoder** is where the model decides "given these features and this prompt, here's the precise mask boundary for *this kind of object*."

So if you want SAM2 to be better at **cucumbers, limes, strawberries** (a real Walmart use case), you need to nudge the decoder, not retrain the encoder. LoRA on the decoder is exactly that — a surgical adapter that biases mask predictions toward your class boundaries while keeping all the general visual knowledge intact.

## When LoRA isn't enough

Two cases where you genuinely need full fine-tune:

1. **Domain shift in the encoder's visual prior** — medical X-rays, SAR satellite imagery, microscopy. The Hiera encoder didn't see this distribution during pretraining. LoRA on the decoder can't compensate. Solution: full FT, or LoRA on the encoder too (we don't ship this yet).
2. **Video / tracking** — Meta's recipe trains the memory module across frames. Anvil is image-only.

For everything else — every retail, manufacturing, document, and inspection use case we've looked at — decoder LoRA is enough and runs on a laptop.

## Reproducing the original Walmart result

The owner's original Walmart side project: full SAM2 fine-tune on a multi-GPU box for produce defect segmentation. Anvil's LoRA version reaches comparable IoU on the same data with **~4000× fewer trainable parameters** and runs locally. That's the whole pitch.

## Reference

- `apps/ml/anvil_ml/training.py::train_lora` — main loop
- `apps/ml/anvil_ml/training.py::_evaluate` — per-class IoU
- `apps/ml/anvil_ml/training.py::evaluate_with_adapter` — used by `/compare`
- `apps/ml/anvil_ml/lora.py` — inject / save / load
- Meta SAM2 training README: <https://github.com/facebookresearch/sam2/blob/main/training/README.md>
