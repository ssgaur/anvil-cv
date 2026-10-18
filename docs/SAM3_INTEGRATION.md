# SAM3 Integration — Architecture, Setup & Benchmarks

Status: **Shipped in ML service, REST API, and Workview UI.**

Upstream reference commit: `facebookresearch/sam3@96914d2`.

## Quick Setup (Gated Hugging Face Weights)

Meta distributes `facebook/sam3` as a gated repository on Hugging Face. Anvil mounts the host's Hugging Face cache (`${HF_CACHE_DIR:-${HOME}/.cache/huggingface}` → `/hf-cache`) into the `ml` container so weights are downloaded once on the host and never baked into Docker images.

1. Accept the license on Hugging Face: **<https://huggingface.co/facebook/sam3>**
2. Authenticate on the host (or set `HF_TOKEN=hf_...` in `.env`) and download only the Transformers `model.safetensors` + tokenizer/config files (3.2 GB, skipping the duplicate 3.2 GB `sam3.pt` checkpoint):

```bash
hf auth login
make sam3-setup
```

If SAM3 weights are not present or not authenticated, Anvil starts normally with SAM2 active and only loads SAM3 on demand when selected in Workview.

## Verified Environment & Checkpoint Layout

| Item | State |
|---|---|
| Hugging Face repo | `facebook/sam3` (gated access required) |
| Inference runtime | `transformers` (`Sam3Model` + `Sam3Processor`, `torch.float32`) |
| Weights used for inference | `model.safetensors` (**3.2 GB**) + config/tokenizer files in `~/.cache/huggingface/hub/models--facebook--sam3` |
| Native training weights | `sam3.pt` (**3.2 GB**) — identical weights in native repo format; excluded by `make sam3-setup` to save 3.2 GB disk space |

## Implementation Status

**Complete across ML service, API, and Workview UI.** SAM2 behavior is unchanged.

| Layer | Component | File |
|---|---|---|
| ML | Provider protocol, `Capabilities`, `Instance` | `apps/ml/anvil_ml/providers/base.py` |
| ML | SAM2 wrapper (delegates to `engine.py`, no behavior change) | `apps/ml/anvil_ml/providers/sam2_provider.py` |
| ML | SAM3 via Transformers (text concepts + point/box prompts → polygons + RLE) | `apps/ml/anvil_ml/providers/sam3_provider.py` |
| ML | Memory-aware lazy loading + LRU eviction (`ANVIL_MODEL_MEMORY_BUDGET_GB`) | `apps/ml/anvil_ml/providers/registry.py` |
| ML | `GET /capabilities`, `POST /predict`, `POST /predict_concept`, `POST /models/{name}/release` | `apps/ml/anvil_ml/main.py` |
| API | `POST /api/images/{id}/predict` (`base_model="sam2" \| "sam3"`), `POST /api/images/{id}/predict-concept`, `GET /api/models/capabilities` | `apps/api/anvil_api/routes/images.py`, `routes/models.py` |
| Web | Top-bar `Model: base SAM3` selector, point/box auto-annotate with SAM3, and floating SAM3 concept bar (`Find all ✨` + threshold slider) | `apps/web/src/pages/WorkviewPage.tsx` |

Verified end-to-end in Docker:
- `wheel` on `truck.jpg` → 4 instances (`0.96, 0.96, 0.90, 0.91`) with RLE preserved; `motorcycle` → `0` (true negative).
- Point/box prompt and text-concept prompt in Workview (`Model: base SAM3`) both return polygon instances and commit directly to image annotations with full undo/redo support.
- Capability guard rejects `sam2` + text with HTTP 400 **without loading** the model (residency stays `false`).

### Memory behaviour on a 16GB Mac

Measured in-container: **3.77 GB** with SAM3 loaded; **~5.0 GB** with SAM2 and
SAM3 both resident. Budget defaults to `ANVIL_MODEL_MEMORY_BUDGET_GB=6.0`.

Caveat: `release()` drops references and runs `gc`, but Python/malloc do not
promptly return freed pages, so cgroup RSS may not fall immediately. The budget
still prevents loading past the limit, which is what avoids an OOM kill.

### Docker wiring

The ML container mounts the host HF cache
(`${HF_CACHE_DIR:-${HOME}/.cache/huggingface}` → `/hf-cache`, with
`HF_HOME=/hf-cache`), so SAM3 is not re-downloaded per machine. Weights are
gated: either `hf auth login` on the host, or set `HF_TOKEN` in `.env`. Because
SAM3 is lazy-loaded, a missing or unauthorised cache degrades to "SAM2 only"
instead of breaking startup.

Note: Docker on macOS has **no GPU passthrough**, so MPS is unavailable inside
containers — SAM3 runs CPU-bound at ~13s/query locally. This is the main
argument for the remote GPU execution backend.

## HF repository registry (Phase 26)


Sizes from the HF API; **datasets were NOT downloaded** (RULE 11).

| Repo | Kind | Size | Purpose | Use now? |
|---|---|---|---|---|
| `facebook/sam3` | model | 6.4 GB total / **3.2 GB needed** | SAM3 weights; ships both `sam3.pt` and `model.safetensors` | **Yes** — downloaded (safetensors only) |
| `facebook/sam3.1` | model | 3.3 GB (`sam3.1_multiplex.pt` only) | Newer variant; **no** `model.safetensors`, so native-repo format only | No — deferred per RULE/Phase 27 |
| `facebook/SACo-Gold` | dataset | 464 MB | Gold eval benchmark, **JSON annotations only** (media sourced separately) | Reference/eval only |
| `facebook/SACo-Silver` | dataset | 631 MB | Silver eval benchmark, JSON annotations | Reference/eval only |
| `facebook/SACo-VEval` | dataset | **30.0 GB** (29.7 GB media tarball) | Video eval benchmark | **Do not download** |
| `facebook/SA-FARI` | dataset | 1.7 GB | Wildlife annotations (train/test JSON) | Not needed |

Never mix these benchmark sets into the user's custom training data.

## Notable: `sam3.1` has no safetensors

`facebook/sam3.1` ships only `sam3.1_multiplex.pt` — there is **no**
`model.safetensors`. So the lightweight Transformers path currently applies to
**SAM3 only**; SAM3.1 would require the native repo. This reinforces doing the
SAM3 baseline first (Phase 27).

## Upstream facts verified from source

| Claim | Result |
|---|---|
| 848M params, detector+tracker, shared vision encoder | Verified (840.4M measured) |
| Text prompts on image inference | Verified |
| Exhaustive multi-instance segmentation | Verified |
| Data abstraction: `FindQuery`, `Object`, `Image`, `Datapoint` with `is_exhaustive` / `is_pixel_exhaustive` | Verified in `sam3/train/data/sam3_image_dataset.py` |
| Negative concept/image pairs supported | Verified — `include_negatives`, empty `object_ids_output` |
| COCO loader derives query text from **category names** | Verified — `query["query_text"] = self._cat_idx_to_text[cat_id]` |
| `noun_phrase` is **not** read by the image COCO loader | Verified — only in the video eval loader |
| Roboflow 100-image config has segmentation **disabled** | Verified — `scratch.enable_segmentation: False` |

### Enabling segmentation training requires

`scratch.enable_segmentation: True` propagates to `load_segmentation`,
`with_seg_masks`, and `model.enable_segmentation`, **plus** uncommenting the
`Masks` loss (`loss_mask: 200.0`, `loss_dice: 10.0`) and `loss_fn_semantic_seg`.
Note the commented block references `sam3.losses.loss_fns.SemanticSegCriterion`,
which looks like a typo for `sam3.train.loss.loss_fns` and would fail at runtime.

## Blocking risk before any training run

Upstream `main` raises `ValueError: Expected grad to be disabled` from
`addmm_act()` on any training forward pass (issues #610 / #613; fix unmerged).
Pin a pre-SAM3.1 commit or patch before spending Azure GPU hours.

## Unverified — do not treat as fact

- VRAM estimates (~8–12 GB inference, ~20–32 GB fine-tune) are **not** measured.
- Azure SKU/pricing not verified. At ~$1.20/hr for a 24 GB A10, $125/mo is
  roughly 100 GPU-hours, viable only with strict start/stop.
- Whether DDP `module.` prefixes appear in saved training checkpoints
  (`_load_checkpoint` strips `detector.`/`model.` but not `module.`).
