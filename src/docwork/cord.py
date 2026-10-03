"""Explicit pinned CORD download/import; labels remain outside extraction inputs."""

from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.request
from pathlib import Path

from .invoice_run import _write_new
from .model_runtime import file_hash

MAPPING_VERSION = "cord-released-receipt-fields-v1"
HEADERS = {"subtotal": ("sub_total", "subtotal_price"), "discount": ("sub_total", "discount_price"),
           "service": ("sub_total", "service_price"), "tax": ("sub_total", "tax_price"),
           "total": ("total", "total_price")}
ROWS = {"description": "nm", "quantity": "cnt", "unit_price": "unitprice", "line_total": "price"}


def amount(value: str | None) -> str | None:
    """Integer rupiah convention: grouped dot/comma triples; .00 decimal suffix.

    Ambiguous or unparseable labels are masked, not guessed. This convention is
    independent of invoice Decimal/locale parsing and frozen before test scoring.
    """
    if value is None:
        return None
    clean = re.sub(r"^(?:rp\.?\s*|idr\s*|@\s*)", "", value.strip(), flags=re.I).replace(" ", "")
    clean = clean.removesuffix(",- ").removesuffix(",-").removesuffix(".-")
    if re.fullmatch(r"-?\d+", clean):
        return str(int(clean))
    if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+(?:[.,]00)?", clean):
        if re.search(r"[.,]00$", clean):
            clean = clean[:-3]
        return str(int(re.sub(r"[.,]", "", clean)))
    if re.fullmatch(r"-?\d+[.,]00", clean):
        return str(int(clean[:-3]))
    return None


def quantity(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip().casefold().replace(" ", "")
    clean = re.sub(r"^x|x$", "", clean)
    if re.fullmatch(r"\d+(?:\.\d+)?", clean):
        from decimal import Decimal
        return format(Decimal(clean).normalize(), "f")
    return None


def map_labels(ground_truth: dict, identifier: str) -> dict:
    parsed = ground_truth["gt_parse"]
    fields, masks = {}, {}
    for name, (group, key) in HEADERS.items():
        raw = parsed.get(group, {}).get(key)
        if isinstance(raw, list):
            raw = " ".join(raw)
        fields[name] = amount(raw)
        if fields[name] is None:
            masks[name] = "unreleased_or_absent_label" if raw is None else "ambiguous_amount_label"
    menu = parsed.get("menu", [])
    if isinstance(menu, dict):
        menu = [menu]
    rows = []
    for item in menu:
        mapped, excluded = {}, {}
        for name, key in ROWS.items():
            raw = item.get(key)
            if isinstance(raw, list):
                raw = " ".join(raw)
            mapped[name] = (" ".join(raw.split()) if raw else None) if name == "description" else quantity(raw) if name == "quantity" else amount(raw)
            if mapped[name] is None:
                excluded[name] = "unreleased_or_absent_label" if raw is None else "ambiguous_label"
        rows.append({**mapped, "exclusions": excluded})
    return {"id": identifier, "fields": fields, "field_exclusions": masks, "line_items": rows,
            "scope": "Top-level menu only; excludes submenu/void items and all store/payment fields."}


def fetch_cord(profile_path: Path, cache: Path) -> dict:
    profile = json.loads(profile_path.read_text())
    cache.mkdir(parents=True, exist_ok=True)
    for file in profile["files"]:
        target = cache / Path(file["path"]).name
        if target.is_symlink():
            raise ValueError("CORD cache cannot contain symlinks")
        if not target.exists():
            temporary = target.with_suffix(".download")
            if temporary.exists():
                raise ValueError("Incomplete download exists; remove it explicitly before retrying")
            url = f"https://huggingface.co/datasets/{profile['dataset_id']}/resolve/{profile['revision']}/{file['path']}"
            print(f"Downloading pinned CORD {file['split']}", flush=True)
            try:
                with urllib.request.urlopen(url, timeout=60) as src, temporary.open("xb") as dst:
                    size = 0
                    while block := src.read(1024 * 1024):
                        size += len(block)
                        if size > file["size_bytes"]:
                            raise ValueError("CORD download exceeds pinned size")
                        dst.write(block)
                if temporary.stat().st_size != file["size_bytes"] or file_hash(temporary) != file["sha256"]:
                    raise ValueError("CORD download failed checksum verification")
                temporary.replace(target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        if target.stat().st_size != file["size_bytes"] or file_hash(target) != file["sha256"]:
            raise ValueError("Existing CORD shard differs from its pin")
    return {"status": "verified", "revision": profile["revision"], "files": len(profile["files"])}


def prepare_cord(profile_path: Path, cache: Path, output: Path) -> dict:
    # Optional dependencies are confined to dataset preparation, never the app.
    try:
        import pyarrow.parquet as parquet
        from PIL import Image
    except ImportError as exc:
        raise ValueError("CORD preparation needs the pinned evaluation dependencies; see the receipt runbook") from exc
    profile = json.loads(profile_path.read_text())
    if output.exists():
        raise ValueError("Prepared CORD output must be a new directory")
    shards = []
    for file in profile["files"]:
        path = cache / Path(file["path"]).name
        if path.is_symlink() or file_hash(path) != file["sha256"] or path.stat().st_size != file["size_bytes"]:
            raise ValueError("CORD shard differs from its pin")
        shards.append((file, path))
    output.mkdir(parents=True)
    docs, seen = [], {}
    for file, shard in shards:
        table = parquet.read_table(shard, columns=["image", "ground_truth"])
        if table.num_rows != file["rows"]:
            raise ValueError("CORD split count differs from the official pin")
        for index, row in enumerate(table.to_pylist()):
            truth = json.loads(row["ground_truth"])
            expected = ("dev", "valid", "validation") if file["split"] == "validation" else ("test",)
            if truth["meta"]["split"] not in expected:
                raise ValueError("CORD metadata split does not match its shard")
            id = f"cord-{file['split']}-{index:03d}"
            encoded = row["image"]["bytes"]
            original_hash = hashlib.sha256(encoded).hexdigest()
            if original_hash in seen and seen[original_hash] != file["split"]:
                raise ValueError("Byte-identical CORD images cross official splits")
            seen[original_hash] = file["split"]
            asset = output / "images" / f"{id}.png"
            asset.parent.mkdir(exist_ok=True)
            with Image.open(io.BytesIO(encoded)) as image:
                if image.width * image.height > 20_000_000:
                    raise ValueError("CORD image exceeds the pixel budget")
                image.convert("RGB").save(asset, format="PNG")
            labels = map_labels(truth, id)
            docs.append({**labels, "split": file["split"], "source_sha256": original_hash,
                         "asset": {"path": str(asset.relative_to(output)), "sha256": file_hash(asset)},
                         "ground_truth_sha256": hashlib.sha256(row["ground_truth"].encode()).hexdigest()})
    manifest = {"manifest_version": MAPPING_VERSION, "dataset_id": profile["dataset_id"],
                "revision": profile["revision"], "license": profile["license"],
                "attribution": profile["attribution"], "profile_sha256": file_hash(profile_path),
                "documents": docs, "preparation": {"pyarrow": __import__("pyarrow").__version__, "pillow": Image.__version__}}
    _write_new(output / "manifest.json", manifest)
    return {"status": "prepared", "documents": len(docs), "manifest": str(output / "manifest.json")}


def verify_cord(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("manifest_version") != MAPPING_VERSION:
        raise ValueError("Unsupported receipt label mapping")
    counts, hashes, ids = {}, {}, set()
    root = manifest_path.resolve(strict=True).parent
    for doc in manifest["documents"]:
        if doc["id"] in ids or doc["split"] not in ("validation", "test"):
            raise ValueError("Duplicate CORD ID or invalid official split")
        ids.add(doc["id"])
        relative = Path(doc["asset"]["path"])
        path = root / relative
        if (relative.is_absolute() or ".." in relative.parts or not path.resolve().is_relative_to(root) or
                any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)) or
                file_hash(path) != doc["asset"]["sha256"]):
            raise ValueError("CORD image path or checksum differs")
        for digest in (doc["source_sha256"], doc["asset"]["sha256"]):
            if digest in hashes and hashes[digest] != doc["split"]:
                raise ValueError("CORD content crosses official splits")
            hashes[digest] = doc["split"]
        counts[doc["split"]] = counts.get(doc["split"], 0) + 1
    if counts != {"validation": 100, "test": 100}:
        raise ValueError("CORD evaluation must retain all 100 validation and 100 test receipts")
    return {"status": "verified", "counts": counts, "manifest_sha256": file_hash(manifest_path)}
