from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlmodel import func, select

from anvil_api.auth import (
    RequireAnyAuthed,
    RequireLabeler,
    RequireReviewer,
    SessionDep,
)
from anvil_api.models import Dataset, Image, ImageStatus, Project
from anvil_api.schemas import DatasetIn, DatasetOut
from anvil_api.utils import unique_slug

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


def _check(session, project_id: UUID) -> Project:
    """Resolve a project by id; 404 if missing.

    No ownership check — RBAC is enforced via role dependencies on the route.
    """
    p = session.get(Project, project_id)
    if not p:
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
def list_datasets(project_id: UUID, session: SessionDep, _user: RequireAnyAuthed) -> list[DatasetOut]:
    _check(session, project_id)
    rows = session.exec(
        select(Dataset).where(Dataset.project_id == project_id).order_by(Dataset.created_at.desc())
    ).all()
    return [_to_out(session, d) for d in rows]


@router.post("", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
def create_dataset(project_id: UUID, body: DatasetIn, session: SessionDep, _r: RequireReviewer) -> DatasetOut:
    _check(session, project_id)
    existing = set(session.exec(
        select(Dataset.slug).where(Dataset.project_id == project_id)
    ).all())
    d = Dataset(project_id=project_id, name=body.name, slug=unique_slug(body.name, existing))
    session.add(d)
    session.commit()
    session.refresh(d)
    return _to_out(session, d)


@router.get("/{dataset_id}", response_model=DatasetOut)
def get_dataset(project_id: UUID, dataset_id: UUID, session: SessionDep, _user: RequireAnyAuthed) -> DatasetOut:
    _check(session, project_id)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    return _to_out(session, d)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(project_id: UUID, dataset_id: UUID, session: SessionDep, _r: RequireReviewer) -> None:
    _check(session, project_id)
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


def _coco_category(c: Class, cls_id_to_idx: dict, classes: list[Class]) -> dict:
    by_id = {cc.id: cc for cc in classes}
    # Build a name path "root/.../leaf" for hierarchy.
    path: list[str] = []
    cur: Class | None = c
    seen: set = set()
    while cur is not None and cur.id not in seen:
        seen.add(cur.id)
        path.insert(0, cur.name)
        cur = by_id.get(cur.parent_id) if cur.parent_id else None
    parent = by_id.get(c.parent_id) if c.parent_id else None
    cat = {
        "id": cls_id_to_idx[c.id],
        "name": c.name,
        "supercategory": parent.name if parent else "",
        "anvil": {
            "geometry_type": str(c.geometry_type),
            "path": "/".join(path),
            "parent_id": cls_id_to_idx[c.parent_id] if c.parent_id and c.parent_id in cls_id_to_idx else None,
        },
    }
    if c.keypoint_schema:
        kps = c.keypoint_schema.get("keypoints", [])
        cat["keypoints"] = [k["name"] for k in kps]
        # COCO skeleton is 1-based pairs.
        cat["skeleton"] = [[i + 1, j + 1] for i, j in c.keypoint_schema.get("edges", [])]
    return cat


@router.get("/{dataset_id}/export/coco")
def export_coco(
    project_id: UUID, dataset_id: UUID, session: SessionDep, _r: RequireReviewer,
    include_images: bool = True,
    review_status: str | None = None,
) -> Response:
    """Export the dataset as a COCO instance-segmentation ZIP.

    Layout:
      annotations.json
      images/<filename>     (only if include_images=true; default true)

    Annotations for `polygon` geometries become COCO `segmentation` lists
    plus derived bbox/area. `bbox`-only annotations are exported as bbox+area
    with a single rectangle segmentation (so tools that require segmentation
    still work).

    Pass `?review_status=approved` to ship only reviewer-approved images
    (recommended for production exports).
    """
    p = _check(session, project_id)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(404, "dataset not found")

    classes = list(session.exec(select(Class).where(Class.project_id == project_id)).all())
    cls_id_to_idx = {c.id: i + 1 for i, c in enumerate(classes)}

    img_q = (
        select(Image)
        .where(Image.dataset_id == dataset_id, Image.status == ImageStatus.READY)
    )
    if review_status:
        img_q = img_q.where(Image.review_status == review_status)
    images = list(session.exec(img_q.order_by(Image.uploaded_at)).all())
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
            anvil_ext: dict = {"geometry_type": str(a.geometry_type)}
            extra: dict = {}
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
            elif a.geometry_type == "polyline" and a.geometry.get("points"):
                # Non-standard COCO; encoded as Anvil extension. Segmentation is
                # the open polyline points (consumers may treat as polygon).
                pts = a.geometry["points"]
                seg = _polygon_to_segmentation(pts)
                bx, by, bw, bh = _polygon_bbox(pts)
                area = 0.0
                anvil_ext["points"] = pts
            elif a.geometry_type == "obb" and "cx" in a.geometry:
                import math
                cx = float(a.geometry["cx"]); cy = float(a.geometry["cy"])
                w = float(a.geometry["w"]); h = float(a.geometry["h"])
                ang = float(a.geometry.get("angle_rad", 0.0))
                cos_a, sin_a = math.cos(ang), math.sin(ang)
                hw, hh = w / 2.0, h / 2.0
                corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
                rotated = [(cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a) for x, y in corners]
                pts = [[x, y] for x, y in rotated]
                seg = _polygon_to_segmentation(pts)
                bx, by, bw, bh = _polygon_bbox(pts)
                area = w * h
                anvil_ext["obb"] = {"cx": cx, "cy": cy, "w": w, "h": h, "angle_rad": ang}
            elif a.geometry_type == "keypoints" and a.geometry.get("points"):
                cls_obj = next((c for c in classes if c.id == a.class_id), None)
                kp_names = (
                    [k["name"] for k in cls_obj.keypoint_schema.get("keypoints", [])]
                    if cls_obj and cls_obj.keypoint_schema else []
                )
                kp_pts = a.geometry["points"]
                # COCO keypoints flat list [x,y,v,...]; v=0 → x,y forced 0.
                flat: list[float] = []
                num_lab = 0
                for p in kp_pts:
                    x, y, v = p
                    if v == 0:
                        flat.extend([0.0, 0.0, 0])
                    else:
                        flat.extend([float(x), float(y), int(v)])
                        num_lab += 1
                visible = [(x, y) for x, y, v in kp_pts if v != 0]
                if visible:
                    xs, ys = zip(*visible, strict=False)
                    bx, by = min(xs), min(ys)
                    bw, bh = max(xs) - bx, max(ys) - by
                else:
                    bx = by = bw = bh = 0.0
                seg = []
                area = bw * bh
                extra["keypoints"] = flat
                extra["num_keypoints"] = num_lab
                anvil_ext["keypoint_names"] = kp_names
            else:
                continue
            ann = {
                "id": k,
                "image_id": img_id_to_idx[a.image_id],
                "category_id": cat,
                "segmentation": seg,
                "bbox": [bx, by, bw, bh],
                "area": area,
                "iscrowd": 0,
                "anvil": anvil_ext,
            }
            ann.update(extra)
            annotations.append(ann)

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
            _coco_category(c, cls_id_to_idx, classes) for c in classes
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


# -------- COCO import --------
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import BackgroundTasks, File, UploadFile
from PIL import Image as PILImage

from anvil_api.models import (
    AnnotationSource,
    GeometryType,
)

# Same palette as ClassesTab default (kept tiny — only used when COCO
# brings in a category we've never seen before).
_FALLBACK_PALETTE = [
    "#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#a855f7",
    "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#14b8a6",
]


def _slug_color(name: str) -> str:
    # Stable colour from the class name so re-imports don't shuffle.
    h = sum(ord(c) for c in name) if name else 0
    return _FALLBACK_PALETTE[h % len(_FALLBACK_PALETTE)]


def _coco_segmentation_to_points(seg) -> list[list[float]] | None:
    """Take COCO `segmentation` and return a flat polygon point list.

    - If it's a list of polygons (list[list[float]]): use the LARGEST by
      perimeter (most COCO datasets only have one ring).
    - If it's a dict (RLE): unsupported for now → return None.
    """
    if not seg:
        return None
    if isinstance(seg, dict):
        return None  # RLE — skip (no mask decoder client-side yet)
    if isinstance(seg, list) and seg and isinstance(seg[0], (int, float)):
        # Some exporters give a flat list at the top level.
        seg = [seg]
    if not isinstance(seg, list):
        return None
    best: list[float] | None = None
    best_per = -1.0
    for ring in seg:
        if not isinstance(ring, list) or len(ring) < 6 or len(ring) % 2:
            continue
        # perimeter for tie-breaking when multiple rings
        per = 0.0
        for i in range(0, len(ring) - 2, 2):
            dx = ring[i + 2] - ring[i]
            dy = ring[i + 3] - ring[i + 1]
            per += (dx * dx + dy * dy) ** 0.5
        if per > best_per:
            best_per = per
            best = ring
    if not best:
        return None
    return [[float(best[i]), float(best[i + 1])] for i in range(0, len(best), 2)]


@router.post("/{dataset_id}/import/coco")
async def import_coco(
    project_id: UUID, dataset_id: UUID,
    session: SessionDep, _user: RequireLabeler, background: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    """Import a COCO instance-segmentation ZIP into this dataset.

    Expected zip layout (matches our /export/coco):
      annotations.json
      images/<file_name>      (any path containing the file_name works)

    Behaviour:
      - Categories: matched against existing project classes by lowercase
        name; new categories become new classes with a deterministic colour
      - Images: stored + queued for SAM2 embedding; original COCO image_id
        is mapped to a freshly-minted Anvil image id
      - Annotations: polygons preferred (segmentation → points); bbox-only
        annotations imported as bbox; RLE segmentations are skipped (not
        supported yet) and counted in the response
      - Filenames not present in the zip are skipped (counted)
    """
    _check(session, project_id)
    d = session.get(Dataset, dataset_id)
    if not d or d.project_id != project_id:
        raise HTTPException(404, "dataset not found")
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(415, "expected a .zip file")

    payload = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as e:
        raise HTTPException(400, f"invalid zip: {e}") from e

    # Locate annotations.json (any depth in the zip)
    ann_name = next((n for n in zf.namelist() if n.endswith("annotations.json")), None)
    if not ann_name:
        raise HTTPException(400, "annotations.json not found in zip")
    try:
        coco = json.loads(zf.read(ann_name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"annotations.json: {e}") from e

    if not isinstance(coco, dict) or "images" not in coco or "annotations" not in coco:
        raise HTTPException(400, "annotations.json missing 'images' or 'annotations'")

    # Build a map from base filename → zip member, for image lookup.
    # We look at any member whose final path component matches the COCO file_name.
    by_basename: dict[str, str] = {}
    for member in zf.namelist():
        if member.endswith("/"):
            continue
        base = PurePosixPath(member).name
        # Last write wins for duplicates — stable enough for our exports
        by_basename[base] = member

    # ---- Categories → classes (existing-by-lower-name match)
    existing_classes = list(session.exec(select(Class).where(Class.project_id == project_id)).all())
    by_lname = {c.name.lower(): c for c in existing_classes}
    coco_cat_to_class: dict[int, Class] = {}
    new_classes = 0
    for cat in coco.get("categories") or []:
        cname = (cat.get("name") or "").strip()
        if not cname:
            continue
        cid = int(cat["id"])
        existing = by_lname.get(cname.lower())
        if existing:
            coco_cat_to_class[cid] = existing
            continue
        new_cls = Class(
            project_id=project_id,
            name=cname,
            color=_slug_color(cname),
        )
        session.add(new_cls)
        session.flush()
        by_lname[cname.lower()] = new_cls
        coco_cat_to_class[cid] = new_cls
        new_classes += 1

    # ---- Images
    n_images_imported = 0
    n_images_skipped = 0
    coco_img_to_anvil: dict[int, Image] = {}
    new_image_records: list[Image] = []

    for ci in coco.get("images") or []:
        fname = ci.get("file_name") or ""
        base = PurePosixPath(fname).name
        member = by_basename.get(base)
        if not member:
            n_images_skipped += 1
            continue
        try:
            data = zf.read(member)
        except KeyError:
            n_images_skipped += 1
            continue
        ext = Path(base).suffix.lower() or ".jpg"
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            n_images_skipped += 1
            continue

        img_id = uuid4()
        key = f"datasets/{d.id}/images/{img_id}{ext}"
        storage.put(key, data)

        # Probe + thumbnail (re-using the same approach as /upload)
        try:
            with PILImage.open(io.BytesIO(data)) as im:
                w, h = im.size
                im.thumbnail((256, 256))
                tbuf = io.BytesIO()
                im.convert("RGB").save(tbuf, format="JPEG", quality=80)
                tkey = f"datasets/{d.id}/thumbs/{img_id}.jpg"
                storage.put(tkey, tbuf.getvalue())
        except Exception:
            n_images_skipped += 1
            continue

        rec = Image(
            id=img_id,
            dataset_id=d.id,
            filename=base,
            storage_key=key,
            thumbnail_key=tkey,
            width=w,
            height=h,
            bytes=len(data),
            status=ImageStatus.UPLOADED,
        )
        session.add(rec)
        coco_img_to_anvil[int(ci["id"])] = rec
        new_image_records.append(rec)
        n_images_imported += 1

    session.flush()  # so rec.id is final before annotations land

    # ---- Annotations
    n_polys = 0
    n_bboxes = 0
    n_rle_skipped = 0
    n_no_image = 0
    n_no_class = 0

    for a in coco.get("annotations") or []:
        cls = coco_cat_to_class.get(int(a.get("category_id", -1)))
        if not cls:
            n_no_class += 1
            continue
        img_rec = coco_img_to_anvil.get(int(a.get("image_id", -1)))
        if not img_rec:
            n_no_image += 1
            continue
        seg = a.get("segmentation")
        bbox = a.get("bbox")
        if isinstance(seg, dict):
            n_rle_skipped += 1
            continue
        points = _coco_segmentation_to_points(seg) if seg else None
        if points and len(points) >= 3:
            session.add(Annotation(
                image_id=img_rec.id,
                class_id=cls.id,
                geometry_type=GeometryType.POLYGON,
                geometry={"points": points},
                source=AnnotationSource.IMPORTED,
                confidence=None,
            ))
            n_polys += 1
        elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            bx, by_, bw, bh = (float(v) for v in bbox)
            session.add(Annotation(
                image_id=img_rec.id,
                class_id=cls.id,
                geometry_type=GeometryType.BBOX,
                geometry={"x": bx, "y": by_, "w": bw, "h": bh},
                source=AnnotationSource.IMPORTED,
                confidence=None,
            ))
            n_bboxes += 1
        else:
            continue

    session.commit()

    # Queue embeddings for the freshly-imported images
    for rec in new_image_records:
        background.add_task(_run_embed_for_import, rec.id)

    return {
        "project_id": str(project_id),
        "dataset_id": str(dataset_id),
        "new_classes": new_classes,
        "matched_existing_classes": len(coco_cat_to_class) - new_classes,
        "images_imported": n_images_imported,
        "images_skipped_missing_or_invalid": n_images_skipped,
        "polygons_imported": n_polys,
        "bboxes_imported": n_bboxes,
        "rle_segmentations_skipped": n_rle_skipped,
        "annotations_skipped_no_image": n_no_image,
        "annotations_skipped_no_class": n_no_class,
    }


def _run_embed_for_import(image_id: UUID) -> None:
    # Mirror images.py's _run_embed helper (kept local to avoid a circular import)
    from anvil_api.routes.images import _run_embed
    _run_embed(image_id)
