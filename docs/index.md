# Anvil

Anvil is a source-available image annotation platform with built-in SAM2
click-to-segment and SAM2 LoRA fine-tuning.

It is designed for teams that want the V7/CVAT/Roboflow workflow without
shipping private images to a hosted SaaS product.

## Quick start

```bash
git clone https://github.com/ssgaur/anvil-cv.git
cd anvil-cv
cp .env.example .env
docker compose up -d --build
python3 scripts/load_sample_dataset.py
```

Open <http://localhost:5173> and sign in with the dev admin:

| Email | Password |
| --- | --- |
| `admin@anvil.dev` | `admin` |

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
- [Fine-tuning](FINE_TUNING.md)
- [API](API.md)
- [Dev setup](DEV_SETUP.md)
- [Roadmap](ROADMAP.md)
- [Paid product roadmap](PAID_PRODUCT_ROADMAP.md)
