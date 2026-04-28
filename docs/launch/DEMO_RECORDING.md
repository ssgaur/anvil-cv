# Demo Recording Script

Goal: produce a 60-90 second GIF or short MP4 for the README that immediately
shows why Anvil is different: label with SAM2, fine-tune, compare, export.

## Setup

1. Start the stack:

   ```bash
   cp .env.example .env
   docker compose up -d --build
   ```

2. Load sample data:

   ```bash
   python3 scripts/load_sample_dataset.py
   ```

3. Open <http://localhost:5173> and sign in with:

   - Email: `admin@anvil.dev`
   - Password: `admin`

4. Use a 1440x900 browser window, zoom 100%, dark mode default. Hide bookmarks.

## Shot List

### 0-8s — Landing in the product

- Start on Projects.
- Open the project named `Anvil demo - leaves`.
- Open dataset `sample leaves`.
- Show the dataset grid with sample images and annotation counts.

Voiceover/caption:
`Self-hosted image annotation with SAM2 built in.`

### 8-25s — SAM2 click-to-segment

- Open the first image.
- Pick class `leaf`.
- Press `A`.
- Click once inside a leaf.
- Wait for mask preview.
- Press `Enter` to save.

Caption:
`Click once. SAM2 proposes the mask. Press Enter to save.`

### 25-38s — Manual tools

- Press `P`, place a quick polygon on a visible object.
- Press `L`, place a short open polyline and press `Enter`.
- Press `O`, drag an OBB.
- Press `K`, if a keypoints class exists, drop two keypoints.

Caption:
`Polygon, bbox, brush, polyline, OBB, keypoints, and comments.`

### 38-55s — Review + active learning

- Click `Next uncertain` in the workview header.
- Show it jumps to another unlabeled/uncertain image.
- Submit or approve one image.

Caption:
`Active learning sends labelers to the highest-value images first.`

### 55-75s — Fine-tune loop

- Go back to the project.
- Open Models / training panel if visible.
- Start fine-tune or show an existing model version.
- Show A/B compare if an existing version is available.

Caption:
`Fine-tune a SAM2 LoRA adapter from your labels, then compare models.`

### 75-90s — Export

- Return to dataset.
- Click `Export COCO`.
- Show the downloaded ZIP in the browser download shelf.

Caption:
`Export COCO when you are done. No lock-in. No telemetry.`

## Recording Tips

- Record at 2x resolution and downscale to 1280px wide for crisp text.
- Keep the mouse moving slowly and deliberately.
- Cut long waits (image embedding, checkpoint download, training) rather than
  showing spinners.
- Final README asset path recommendation:
  `docs/assets/anvil-demo.gif` for GIF or `docs/assets/anvil-demo.mp4` for video.

## README Embed Placeholder

Until the asset is recorded, keep the README placeholder:

```md
> Demo GIF coming here: `docs/assets/anvil-demo.gif`
```

