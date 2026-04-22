"""Anvil ML service.

Stateless FastAPI app wrapping SAM2 for:
  POST /embed     - compute & return image embedding (cached by API/worker)
  POST /predict   - given (embedding_uri | image_uri) + prompts, return mask
  POST /finetune  - kick off LoRA fine-tune (long-running; status via job_id)
  GET  /jobs/{id} - training job status

v0.0.1 ships health + stubs only; SAM2 wiring lands in Phase 1.
"""

from fastapi import FastAPI
from pydantic import BaseModel

from anvil_ml import __version__

app = FastAPI(title="Anvil ML", version=__version__)


class PredictRequest(BaseModel):
    image_uri: str
    embedding_uri: str | None = None
    points: list[list[float]] | None = None
    point_labels: list[int] | None = None
    box: list[float] | None = None
    model_version_id: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__, "device": "stub"}


@app.post("/predict")
def predict(_req: PredictRequest) -> dict[str, str]:
    return {"status": "not_implemented", "msg": "SAM2 wiring coming in Phase 1"}
