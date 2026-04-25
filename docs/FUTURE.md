# Future Work

Beyond the 8 remaining v0 todos in [ROADMAP.md](ROADMAP.md), here's the longer-term backlog. None of these are scoped or scheduled — they're directional.

## Near-term (post-v0)

### Annotation
- **Polyline tool** (lanes, pipes, cracks)
- **Keypoint tool** with skeleton templates (pose / parts)
- **Instance ID** linking same object across images (re-ID groundwork)
- **Multi-class polygon** (one geometry, multiple labels with per-class confidence)
- **Auto-bbox from class prompt** (text → CLIP → SAM2)

### Model loop
- **Encoder LoRA** for domain-shift cases (medical, satellite)
- **Class-conditional prompts** — train a learnable text prompt per class
- **Continuous training** — new approved annotations auto-trigger an incremental LoRA step
- **Export trained model** as a portable bundle (adapter + config + base weights pinning)
- **Try other base models** — DINOv2 + lightweight head for classification, Florence-2 for grounded captioning

### Quality & ops
- **Inter-annotator agreement matrix** with drill-down per class / per pair
- **Time-tracking** per annotator per image (already half-built; wire up the timer)
- **Audit log** for every annotation edit
- **Webhooks** on review-approved → push to your CV training pipeline
- **API tokens** with scopes (read-only / write / admin)

### Team & access
- **Orgs + members + roles** (Owner / Admin / Annotator / Reviewer / Viewer)
- **SSO / SAML** for enterprise
- **Audit & compliance** mode (immutable annotation history)

### Data & integrations
- **HuggingFace datasets** export
- **YOLO** export
- **Pascal VOC** export
- **S3 / GCS / Azure** storage backends end-to-end tested
- **Direct connectors** to S3 buckets / Drobox / Drive — discover images without uploading

## Mid-term

### Video
- Multi-frame annotation with SAM2 memory module (the original SAM2 strength)
- Mask propagation across frames
- Per-frame review

### Performance
- WebGPU mask rendering for huge images
- Tiled annotation for satellite / pathology
- Distributed embedding pre-compute pool

### Marketplace
- Pre-trained adapters per domain (retail produce, manufacturing defects, document layout) shareable across users
- Dataset marketplace (community-contributed labeled datasets)

## Far-term / experimental

### Beyond segmentation
- **Click-to-3D** — combine SAM2 mask + depth (Depth-Anything) for instant 3D bounding box
- **Search by example** — embed all images, "find me images that look like this one"
- **Active mining** — given my model, which unlabeled images would change my decision boundaries the most?
- **Synthetic augmentation** — "I have 20 lime images, generate 200 with controlled lighting/angle/occlusion"

### Edge & deployment
- **One-click deploy trained adapter to a Coral / Jetson** for in-store inference
- **WASM inference** in the browser for client-side pre-labeling
- **Mobile capture app** — phone camera → review queue → train

## Business model experiments
- Hosted SaaS for SMBs (per-image pricing instead of per-seat)
- On-prem appliance for enterprise (preconfigured GPU box + OTA updates)
- Adapter marketplace revenue share (community trains adapters, customers buy them)

---

If any of these resonate, [open an issue](https://github.com/ssgaur/anvil-cv/issues/new) tagged `discussion` and let's talk before you build.
