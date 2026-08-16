# Personal video object removal

Anvil can remove one selected object from a short video range using:

1. **SAM3** — highlights every instance of a named concept on the paused frame.
2. **SAM2 video propagation** — tracks the selected pixel mask through the clip.
3. **ProPainter** — reconstructs the hidden background across time.
4. **Source-quality compositor + FFmpeg** — changes only the tracked region,
   restores original audio, and writes a separate high-bitrate H.264/AAC MP4.

The original uploaded video and local source file are never modified. Every edit
is a derived MP4 under a separate storage key.

## License boundary

ProPainter is released under the **S-Lab License 1.0**, which permits
non-commercial use and requires separate permission for commercial use.
Therefore:

- ProPainter source and weights are **not committed**, vendored, put in Docker,
  or shipped with Anvil.
- It installs under gitignored `.anvil-personal/`.
- This backend is labeled personal-use-only in the API and UI.
- A commercial Anvil release must use a commercially licensed backend instead.

## Setup

```bash
python3 scripts/install_propainter_personal.py --accept-noncommercial-license
python3 scripts/personal_video_worker.py
docker compose up -d --build
python3 scripts/load_showcase_video.py IMG_4505.MOV
open http://localhost:5173/model
```

Keep the personal worker terminal open. It runs natively on macOS so PyTorch can
use MPS; Docker on macOS cannot access MPS.

## Workflow

1. Pause at a frame containing the target.
2. Enter a concrete concept such as `person` and choose **Highlight**.
3. Click one highlighted SAM3 mask.
4. Optionally enable **Remove subject + cast shadows** and paint over each
   shadow. Shadows are illumination, not semantic objects, so SAM3 does not
   discover them reliably.
5. Keep **Remove through full video** enabled (default), or disable it for a
   quick custom-range test.
6. Choose **Remove selected object(s)**.
6. Wait through tracking, inpainting and encoding.
7. Download the new MP4.

SAM3 is concept-based. It cannot segment literally every arbitrary thing from
the phrase `object`; use concrete phrases such as `person`, `flag`, `chair` or
`bag`.

## Full-duration quality

The final renderer does **not** upscale a low-resolution version of the entire
video. ProPainter works on bounded 432×240 overlapping chunks, but its generated
pixels are composited only inside a feathered version of the tracked mask. Every
pixel outside that region comes from the original-resolution playback frame.
The result is encoded at source dimensions and frame rate with H.264 CRF 16;
original audio is restored.

### Ghost/flicker prevention

- SAM2 tracking runs at 30 working fps.
- Neighboring masks are optical-flow aligned and temporally unioned.
- Small internal gaps are closed before inpainting.
- The compositing core uses the same four-pixel working-mask dilation as
  ProPainter and is always replaced at alpha 1.
- Feathering applies only outside that hard core. The original implementation
  blurred the core itself, which left translucent ghost silhouettes.

### Cast shadows

SAM3 returned no masks for `shadow`, `cast shadow`, or `ground shadow` on the
benchmark frame. The editor therefore does not pretend automatic shadow
detection works. Shadow brush strokes are stored as a transparent PNG,
split into connected components, tracked as additional SAM2 identities, then
unioned with subject masks before ProPainter.

For `IMG_4505.MOV`, full-video mode outputs 122.725 seconds at 1920×1080 and
30fps. On the M1 16GB, expect roughly **45–120 minutes** for the complete
two-minute video. Processing is durable and resumable after page reload or
personal-worker restart.

Measured quick tests:

| Test | Result |
|---|---|
| ProPainter, 14 frames, 432×240, MPS | 20.9 seconds |
| Peak memory footprint | ~5.3GB |
| Real source-quality 0.5-second removal E2E | 39.8 seconds |
| Derived format | 1920×1080, 30fps, AAC, ~23Mbps |
| Precision test: 2 subjects + painted shadow, 30fps | 60.2 seconds |

The generated background region cannot be identical to pixels that were hidden
behind the removed object—those pixels never existed in the source. ProPainter
synthesizes that region; everything else retains source detail. The first
release supports one selected object per derived export. Re-run from the
original for another alternative edit; the original remains preserved.
