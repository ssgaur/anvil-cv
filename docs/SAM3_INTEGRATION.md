# SAM3 Integration — Verified Findings (Phase 0)

Status: **audit only, no application code changed.** This records facts verified
on 2026-08-13 so a later session does not have to re-derive them.

Upstream reference commit: `facebookresearch/sam3@96914d2`.

## Local environment (verified)

| Item | State |
|---|---|
| HF CLI | logged in as `ssgaur` (org AVTTech); gated access to `facebook/sam3` + `sam3.1` resolves |
| `transformers` | **5.15.0** installed in `./.venv` — `Sam3Model` + `Sam3Processor` import OK |
| SAM3 weights | `~/.cache/huggingface/hub/models--facebook--sam3`, **3.2 GB** |
| SAM3 GitHub source | **NOT checked out locally.** Only `vendor/sam2` exists |
| ML container | `torch 2.13.0+cu130`, `cuda False`, no `transformers` |

### Downloaded deliberately minimal

`sam3.pt` (3.2 GB, native-repo format) and `model.safetensors` (3.2 GB,
Transformers format) are the **same weights**. On the Transformers path only
`model.safetensors` + config/tokenizer files are needed, so `sam3.pt` was
excluded — saving 3.2 GB.

## SAM3 smoke test on Mac M1 (16 GB, CPU)

Real result, not projected. Model reports **840.4M parameters** (matches the
~848M claim). Load ~5 s; inference 6–12 s per prompt on CPU.

Official `assets/images/truck.jpg`:

| Prompt | Instances | Scores |
|---|---|---|
| `truck` | 1 | 0.93 |
| `wheel` | **4** | 0.96, 0.96, 0.90, 0.91 |
| `motorcycle` | **0** | — (correct true negative) |

Masks return at full image resolution (`(4, 1200, 1800)`), boxes in absolute
xyxy pixels.

This validates three things at once: text-prompted inference, **multi-instance**
exhaustive segmentation, and a correct **negative** result (Phase 7) — all on a
machine with no CUDA.

### Threshold behaviour worth knowing

On the cluttered `assets/images/groceries.jpg`, the same pipeline returns **0**
instances at `threshold=0.5`, and at `threshold=0.05` returns many weak
candidates (77 for "food", all scores < 0.25). This is a domain/clutter gap, not
an integration bug — `truck.jpg` proves the path is sound.

Product implications:
- The detection threshold **must be user-tunable**, not hardcoded at 0.5.
- "Many candidates, all low confidence" is a strong active-learning signal
  (Phase 21) and a good argument for fine-tuning.

## Implementation status

**Done — ML layer (STEP 4/5).** SAM2 behaviour unchanged.

| Piece | File |
|---|---|
| Provider protocol, `Capabilities`, `Instance` | `apps/ml/anvil_ml/providers/base.py` |
| SAM2 wrapper (delegates to `engine.py`, no behaviour change) | `providers/sam2_provider.py` |
| SAM3 via Transformers (text → N instances + RLE) | `providers/sam3_provider.py` |
| Memory-aware lazy loading + LRU eviction | `providers/registry.py` |
| `GET /capabilities`, `POST /predict_concept`, `POST /models/{name}/release` | `apps/ml/anvil_ml/main.py` |

Verified in Docker: `wheel` → 4 instances (0.96/0.96/0.90/0.91) with RLE
preserved; `motorcycle` → 0 (true negative); SAM2 e2e smoketest + COCO
round-trip + undo/redo + bbox-resize all still pass.

Capability guard rejects `sam2` + text with HTTP 400 **without loading** the
model (residency stays `false`), so an unsupported request costs no memory.

**Not yet wired:** API routes, canonical Concept/Query schema, and the
annotation UI. SAM3 is reachable on the ML service but not yet from the app.

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
