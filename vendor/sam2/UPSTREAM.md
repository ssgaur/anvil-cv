# Upstream tracking

This directory is a vendored snapshot of [facebookresearch/sam2](https://github.com/facebookresearch/sam2).

## What we kept

- `sam2/` — the importable package (model definitions, predictor, configs)
- `training/` — Meta's full fine-tune recipe (used by the future paid "cloud full-FT" feature)
- `LICENSE`, `LICENSE_cctorch`, `MANIFEST.in`, `pyproject.toml`, `setup.py`, `README.md`, `INSTALL.md`, `RELEASE_NOTES.md`

## What we dropped to keep the repo lean

`notebooks/` (42 MB), `demo/` (12 MB), `sav_dataset/` (6.8 MB), `assets/` (904 KB), `tools/`, `checkpoints/`, `docker-compose.yaml`, `backend.Dockerfile`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, the upstream `.git/` directory.

## Upstream commit

This snapshot was taken from `main` of `facebookresearch/sam2`. To record / verify the exact commit the next time the snapshot is updated, run upstream and paste the SHA here:

```
SHA: <fill in next refresh>
Date: <YYYY-MM-DD>
```

## How to refresh

When you want to pull a newer SAM2:

```bash
cd /tmp
git clone --depth 1 https://github.com/facebookresearch/sam2.git sam2-upstream
cd sam2-upstream && git rev-parse HEAD   # record this in the SHA above
cd -
rm -rf vendor/sam2/sam2 vendor/sam2/training
cp -R /tmp/sam2-upstream/sam2     vendor/sam2/sam2
cp -R /tmp/sam2-upstream/training vendor/sam2/training
cp /tmp/sam2-upstream/{LICENSE,LICENSE_cctorch,setup.py,pyproject.toml,MANIFEST.in,README.md,INSTALL.md,RELEASE_NOTES.md} vendor/sam2/
# verify nothing imports broke:
python scripts/sam2_smoketest.py
git add vendor/sam2 && git commit -m "chore(vendor): refresh sam2 to <SHA>"
```

## License

Upstream is Apache 2.0 (see `vendor/sam2/LICENSE`). It remains Apache-licensed
third-party material and is not relicensed under Anvil's root PolyForm license.
Attribution and the exclusion are recorded in the repo-root `NOTICE` file.
