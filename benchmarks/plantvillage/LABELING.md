# Labeling Guide: PlantVillage Lesions

For the SAM2 LoRA benchmark blog post.

## Goal

Produce 100 images with pixel-accurate **lesion masks** so we can:
1. Measure vanilla SAM2 IoU on the holdout
2. Fine-tune SAM2 LoRA on the training split
3. Measure LoRA SAM2 IoU on the same holdout
4. Publish the delta as our headline benchmark

## Setup

```bash
uv pip install datasets pillow
uv run python benchmarks/plantvillage/seed_data.py --seed 42
# produces 100 images at benchmarks/plantvillage/seed/images/
```

Then in Anvil:

1. Create a new project: **plantvillage-lesions**
2. Create one class: **lesion** (color: red)
3. Bulk upload `benchmarks/plantvillage/seed/images/*.jpg` (100 files)

## What to label

**Mark every visible disease lesion as a separate polygon.**

| Class | What a lesion looks like |
|---|---|
| `Apple___Apple_scab` | dark olive-green to brown circular spots, may have rough texture |
| `Grape___Black_rot` | small reddish-brown circular spots that enlarge with grey centers |
| `Tomato___Early_blight` | concentric ring "bullseye" brown spots |
| `Potato___Late_blight` | irregular dark brown patches, often water-soaked edges |
| `Strawberry___Leaf_scorch` | small purplish spots, may merge into larger brown patches |

### Rules (be strict, this is the ground truth)

- **One polygon per lesion.** If two lesions touch but are visually distinct, label as two.
- **Tight to the lesion edge.** Use SAM2 click-to-segment then refine the polygon. Aim for the edge of the discoloration, not the surrounding healthy tissue.
- **Skip lesions smaller than ~15px.** Too small to be meaningful for IoU.
- **Skip ambiguous cases** (shadow vs lesion, water droplet vs lesion). Better to drop than mislabel.
- **Healthy areas of the leaf are NOT labeled.** We're segmenting lesions, not the leaf.

### Time budget

- ~2 min/image average → ~3.5 hours total for 100 images
- Spread across two sessions if needed; quality > speed

## After labeling

In Anvil: Project settings -> Export -> COCO format -> download `plantvillage-lesions-coco.zip`.

Place at:
```
benchmarks/plantvillage/labeled/plantvillage-lesions-coco.zip
```

Then run:
```bash
uv run python benchmarks/plantvillage/run_benchmark.py
```

(The benchmark script will be added in the next commit.)

## Recording the screencast

While labeling, screen-record one **3-5 minute segment**:
- Show: bulk upload, click-to-segment with SAM2, polygon refine, save to review queue
- This becomes the embedded video in the launch blog post
- Tool: macOS built-in (Cmd+Shift+5) or OBS
- Output: `benchmarks/plantvillage/labeling-demo.mov` (will not be committed; we'll upload to YouTube/Loom)
