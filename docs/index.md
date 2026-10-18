# Anvil

Anvil is a self-hosted, source-available computer vision annotation platform with built-in **SAM2 interactive segmentation**, **SAM3 open-vocabulary concept segmentation**, and **in-loop SAM2 LoRA fine-tuning**.

It is built for engineering teams that require modern foundation-model-assisted labeling and domain adaptation without sending proprietary imagery or model weights to a third-party cloud SaaS.

## Quick start

```bash
git clone https://github.com/ssgaur/anvil-cv.git
cd anvil-cv
cp .env.example .env
docker compose up -d --build
python3 scripts/load_sample_dataset.py
```

Open <http://localhost:5173> and sign in with the default development admin:

| Email | Password |
| --- | --- |
| `admin@anvil.dev` | `admin` |

### Enabling SAM3 (optional)

SAM2 (`sam2.1_hiera_base_plus.pt`) downloads automatically on first start. To also enable **SAM3 (`facebook/sam3`)**, accept the gated model license at <https://huggingface.co/facebook/sam3>, authenticate on your host, and pull the Transformers safetensors weights into your shared cache:

```bash
hf auth login
make sam3-setup
```

## Privacy stance

No telemetry, no product analytics, no phone-home. Your images, labels, model
versions, and users stay in your own deployment.

## Licensing

Noncommercial use is available under the
[PolyForm Noncommercial License 1.0.0](https://github.com/ssgaur/anvil-cv/blob/main/LICENSE).
Commercial use requires a separate written paid license; see the
[commercial licensing overview](https://github.com/ssgaur/anvil-cv/blob/main/COMMERCIAL_LICENSE.md).

## Learn more

- [Features](FEATURES.md)
- [Architecture](ARCHITECTURE.md)
- [Fine-tuning & Model Adaptation](FINE_TUNING.md)
- [SAM3 Integration](SAM3_INTEGRATION.md)
- [Observability](OBSERVABILITY.md)
- [API](API.md)
- [Dev setup](DEV_SETUP.md)
- [Roadmap](ROADMAP.md)
- [Paid product roadmap](PAID_PRODUCT_ROADMAP.md)
