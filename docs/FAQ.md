# FAQ

### What is Anvil?

An open-source image annotation platform with built-in SAM2 fine-tuning. Think V7 Darwin / CVAT / Roboflow, but the model gets better at **your** classes as your team labels.

### Who is this for?

Computer-vision teams labeling image data — retail (defect/SKU), manufacturing inspection, document layout, security, agriculture. Especially teams who can't justify $500/seat/month for V7 Darwin and don't want to roll their own labeling stack on top of CVAT.

### Why does fine-tuning matter? V7 Darwin already auto-annotates with SAM2.

Base SAM2 is great at "obvious" objects — a clear cucumber on a white background. It struggles with your domain-specific edge cases: bruises on limes, deformed strawberries, partial occlusion, your store's specific lighting. **Fine-tuning teaches SAM2 your domain.** V7 doesn't expose this; we make it a button.

### Will it run on my MacBook?

Yes. The whole stack — API + ML + web + Postgres — runs on an M1 16 GB MacBook. Inference uses MPS, training uses CPU (MPS backward is flaky for some attention ops in PyTorch 2.x). LoRA training on a few dozen images takes seconds to a couple of minutes.

### How is fine-tuning so fast?

Two reasons: (1) we **cache image embeddings on disk** at upload time so the heavy SAM2 encoder never runs during training, and (2) we use **LoRA** to only train ~47K parameters (the mask decoder's attention adapters), not the whole 100M+ model. Full deep-dive: [FINE_TUNING.md](FINE_TUNING.md).

### What about GPU servers / on-prem deployments?

The same code runs unchanged on CUDA. `ML_DEVICE=auto` in `.env` picks the best available. For multi-tenant production we recommend running the ML service on a GPU box and the API on a small CPU box — they communicate over HTTP.

### What annotation types are supported?

Polygon, Bbox, Brush, Click-to-segment (multi-prompt SAM2). Polylines, keypoints, directional vectors, instance IDs are out of scope for v0. Video is also out of scope.

### How do I export to my training pipeline?

COCO instance-segmentation export ships today (`/api/datasets/{id}/export/coco`). YOLO and HuggingFace exports are on the roadmap.

### Can I import existing labels from another tool?

Yes — drag-drop a COCO ZIP onto a dataset and we bootstrap classes + annotations. CVAT and V7 export to COCO, so this covers most migrations.

### How do you handle review / QA?

Per-image review status (approved / rejected / needs-changes), predefined reject reasons, and **spatial pin comments** anchored to canvas coordinates. Per-annotator quality dashboard is in progress.

### Is there team / RBAC support?

Single-user today. Org / members / roles is on the roadmap (Phase 4-ish).

### How do I self-host in production?

Easiest path is `make up` from a server with Docker installed — that gives you the full stack on one box (Postgres + Redis + MinIO + API + ML + worker + web). For real production, swap MinIO for S3, point `DATABASE_URL` at managed Postgres, and run the ML service on a GPU box. See [ARCHITECTURE.md](ARCHITECTURE.md) for the deployment topology.

### What's the license?

Apache 2.0. Use it commercially, fork it, no restrictions.

### How does Anvil compare to CVAT / Label Studio?

Both are great open-source labelers. Neither has built-in fine-tuning. Anvil's bet is that the model-loop is the differentiator — labeling tools are commoditized, but a labeling tool that **gets faster as you label** changes the unit economics of every CV team.

### Can I contribute?

Yes — see [CONTRIBUTING.md](../CONTRIBUTING.md). The fastest help right now: try it, file issues. The next-fastest: pick something off [ROADMAP.md](ROADMAP.md).

### How do I report a bug?

Open an issue with the bug template. Include `logs/api.log` and `logs/ml.log` tails plus reproduction steps.

### What's "Anvil"? Final name?

Working title. `finalize-name-domain` is on the roadmap. Suggestions welcome.
