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

### After a laptop restart

Use the one-command launcher:

```bash
./scripts/start_personal_video_editor.sh
```

It starts Docker, waits for API/ML readiness, then runs the native worker under
`caffeinate`. Heavy jobs never auto-resume after a worker or laptop restart.
Open <http://localhost:5173/model>; the job appears as:

> **Job interrupted — checkpoints are safe**

Choose **Resume job** explicitly. The page may be reloaded or closed at any
time; state, active compute time, stage progress and output history are loaded
from the API/worker rather than browser memory.

Running jobs provide both controls:

- **Pause job** — stops at the current operation, keeps every committed tracking
  and inpainting chunk, and releases model memory.
- **Cancel** — abandons the job and deletes its native-worker artifacts.

The stage dashboard reports:

1. Prepare frames
2. Track objects
3. Stabilize masks
4. Reconstruct background
5. Encode final MP4

The elapsed timer counts active compute only. Pauses, shutdown time and laptop
downtime are excluded.

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

## Fault tolerance and resume

Long jobs are checkpointed by stage:

- source download and extracted working frames are reused;
- SAM2 tracking commits masks and per-object boundary state after every bounded
  chunk (60 frames maximum; dynamically reduced to 30 frames for 14
  identities), then releases the inference state;
- ProPainter commits each temporal output chunk independently;
- final encoding can restart from completed inpainted frames.

Checkpoint files and manifests use atomic rename ordering. A crash can lose only
the currently active chunk, not hours of completed chunks. On worker startup,
jobs in `queued`, `running`, or `interrupted` state are automatically requeued.
The UI job UUID remains the same.

Fault-injection verification:

1. interrupted SAM2 after a forward checkpoint at frame 48 of 61;
2. restarted and verified it continued past frame 48 rather than resetting;
3. interrupted ProPainter after chunk 1 of 7;
4. restarted and verified chunk 1 was skipped (mtime unchanged) while chunk 2
   was generated;
5. resumed through a valid derived MP4.

The original uncheckpointed 14-identity benchmark reached frame 2,219 before a
forced Mac restart, but that in-memory progress cannot be recovered. Its
selection and shadow input remain durable. New/restarted jobs use bounded
checkpointed tracking.

### Memory safety on M1 16GB

- PyTorch MPS is limited to 60% of recommended device memory.
- MPS high/low watermarks are 70%/50%, preferring a resumable job interruption
  over system-wide memory exhaustion.
- Only two decoded 1024×1024 SAM2 frames are cached.
- Active tracking state is bounded by object×frame budget; 14 identities use
  30-frame chunks.
- Every chunk releases inference state and empties the MPS allocator cache.
