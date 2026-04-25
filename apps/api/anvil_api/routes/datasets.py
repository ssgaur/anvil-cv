from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from anvil_api.auth import CurrentUser, SessionDep
from anvil_api.models import Dataset, Image, ImageStatus, Project
from anvil_api.schemas import DatasetIn, DatasetOut
from anvil_api.utils import unique_slug

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


def _check(session, project_id: UUID, user) -> Project:
    p = session.get(Project, project_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return p


def _to_out(session, d: Dataset) -> DatasetOut:
    img_count = session.exec(
        select(func.count()).select_from(Image).where(Image.dataset_id == d.id)
    ).one()
    ready_count = session.exec(
        select(func.count())
        .select_from(Image)
        .where(Image.dataset_id == d.id, Image.status == ImageStatus.READY)
    ).one()
    return DatasetOut(
        id=d.id, project_id=d.project_id, name=d.name, slug=d.slug,
        created_at=d.created_at, image_count=img_count, ready_count=ready_count,
    )


@router.get("", response_model=list[DatasetOut])
def list_datasets(project_id: UUID, session: SessionDep, user: CurrentUser) -> list[DatasetOut]:
    _check(session, project_id, user)
    rows = session.exec(
        select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.created_at.desc())
    ).all()
    return [_to_out(session, d) for d in rows]


@router.post("", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
def create_dataset(project_id: UUID, body: DatasetIn, session: SessionDep, user: CurrentUser) -> DatasetOut:
    _check(session, project_id, user)
    existing = set(session.exec(
        select(Dataset.slug).where(Dataset.project_id == project_id)
    ).all())
    d = Dataset(project_id=project_id, name=body.name, slug=unique_slug(body.name, existing))
    session.add(d)
    session.commit()
    session.refresh(d)
    return _to_out(session, d)


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(project_id: UUID, dataset_id: UUID, session: SessionDep, user: CurrentUser) -> DatasetOut:
    _check(session, project_id, user)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    return _to_out(session, d)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(project_id: UUID, dataset_id: UUID, session: SessionDep, user: CurrentUser) -> None:
    _check(session, project_id, user)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    session.delete(d)
    session.commit()


# --------- COCO export ---------

import io
import json
import zipfile
from datetime import datetime
from fastapi import Response
from fastapi.responses import StreamingResponse

from anvil_api.models import Annotation, Class
from anvil_api.storage import storage


def _polygon_to_segmentation(points: list[list[float]]) -> list[list[float]]:
    flat = [coord for pt in points for coord in pt]
    return [flat]


def _polygon_bbox(points: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = min(xs), min(ys)
    return x0, y0, max(xs) - x0, max(ys) - y0


def _polygon_area(points: list[list[float]]) -> float:
    """Shoelace formula."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


@router.get("/{dataset_id}/export/coco")
def export_coco(
    project_id: UUID, dataset_id: UUID, session: SessionDep, user: CurrentUser,
    include_images: bool = True,
) -> Response:
    """Export the dataset as a COCO instance-segmentation ZIP.

    Layout:
      annotations.json
      images/<filename>     (only if include_images=true; default true)

    Annotations for `polygon` geometries become COCO `segmentation` lists
    plus derived bbox/area. `bbox`-only annotations are exported as bbox+area
    with a single rectangle segmentation (so tools that require segmentation
    still work).
    """
    p = _check(session, project_id, user)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(404, "dataset not found")

    classes = list(session.exec(select(Class).where(Class.project_id == project_id)).all())
    cls_id_to_idx = {c.id: i + 1 for i, c in enumerate(classes)}

    images = list(
        session.exec(
            select(Image)
            .where(Image.dataset_id == dataset_id, Image.status == ImageStatus.READY)
            .order_by(Image.uploaded_at)
        ).all()
    )
    img_id_to_idx = {im.id: i + 1 for i, im in enumerate(images)}
    img_ids = [im.id for im in images]

    annotations = []
    if img_ids:
        rows = list(
            session.exec(
                select(Annotation).where(Annotation.image_id.in_(img_ids))  # type: ignore[union-attr]
            ).all()
        )
        for k, a in enumerate(rows, start=1):
            cat = cls_id_to_idx.get(a.class_id)
            if cat is None:
                continue
            if a.geometry_type == "polygon" and a.geometry.get("points"):
                pts = a.geometry["points"]
                seg = _polygon_to_segmentation(pts)
                bx, by, bw, bh = _polygon_bbox(pts)
                area = _polygon_area(pts)
            elif a.geometry_type == "bbox" and "x" in a.geometry:
                bx = float(a.geometry["x"]); by = float(a.geometry["y"])
                bw = float(a.geometry["w"]); bh = float(a.geometry["h"])
                seg = [[bx, by, bx + bw, by, bx + bw, by + bh, bx, by + bh]]
                area = bw * bh
            else:
                continue
            annotations.append({
                "id": k,
                "image_id": img_id_to_idx[a.image_id],
                "category_id": cat,
                "segmentation": seg,
                "bbox": [bx, by, bw, bh],
                "area": area,
                "iscrowd": 0,
            })

    coco = {
        "info": {
            "description": f"Anvil export — project '{p.name}', dataset '{d.name}'",
            "url": "",
            "version": "1.0",
            "year": datetime.utcnow().year,
            "contributor": "anvil-cv",
            "date_created": datetime.utcnow().isoformat() + "Z",
        },
        "licenses": [],
        "images": [
            {
                "id": img_id_to_idx[im.id],
                "file_name": im.filename,
                "width": im.width,
                "height": im.height,
            }
            for im in images
        ],
        "annotations": annotations,
        "categories": [
            {"id": cls_id_to_idx[c.id], "name": c.name, "supercategory": ""}
            for c in classes
        ],
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("annotations.json", json.dumps(coco, indent=2))
        if include_images:
            for im in images:
                try:
                    data = storage.get(im.storage_key)
                    z.writestr(f"images/{im.filename}", data)
                except Exception as e:
                    print(f"[anvil-api] export skipped {im.filename}: {e}")
    buf.seek(0)

    safe_name = (d.name or "dataset").replace("/", "_").replace(" ", "_")
    fn = f"{safe_name}-coco-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )
