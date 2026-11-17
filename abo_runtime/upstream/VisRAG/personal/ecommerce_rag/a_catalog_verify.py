"""Read-only verification of the complete ABO database, FTS5 and E5 index."""
import argparse
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
import time
from pathlib import Path

import numpy as np

from full_catalog import DB, ROOT, EXPECTED, connect
from full_encoder import DIMENSION, VECTOR_FILE, MANIFEST_FILE, MODEL_REVISION
from common import WORKSPACE, file_hash, operation, write_json


def verify():
    started = time.perf_counter()
    manifest = json.loads((ROOT / "manifest.json").read_text("utf-8"))
    dense = json.loads(MANIFEST_FILE.read_text("utf-8"))
    product_types, domains = Counter(), Counter()
    bad = Counter()
    vectors = np.load(VECTOR_FILE, mmap_mode="r", allow_pickle=False)
    if vectors.shape != (EXPECTED, DIMENSION) or vectors.dtype != np.float32:
        raise ValueError("Unexpected E5 index size or dtype")
    if (dense["revision"] != MODEL_REVISION or not dense["complete"]
        or dense["source_database_sha256"] != manifest["database_sha256"]
        or file_hash(DB) != manifest["database_sha256"] or file_hash(VECTOR_FILE) != dense["vector_sha256"]):
        raise ValueError("Catalog or E5 digest/revision mismatch")
    with closing(connect()) as read:
        read.execute("BEGIN")
        indexed = read.execute("SELECT count(*) FROM products_fts").fetchone()[0]
        for number, row in enumerate(read.execute(
            "SELECT p.id,p.sku,p.domain,p.listing_key,p.product_type,p.raw_json,p.raw_sha256,f.sku fts_sku "
            "FROM products p LEFT JOIN products_fts f ON f.rowid=p.id ORDER BY p.id"), 1):
            product_types[row["product_type"]] += 1
            domains[row["domain"]] += 1
            row_sha = sha256(row["raw_json"].encode()).hexdigest()
            bad["source_sha"] += row_sha != row["raw_sha256"]
            bad["identity"] += row["listing_key"] != row["sku"] + "@" + row["domain"]
            bad["fts_alignment"] += row["fts_sku"] != row["sku"]
            bad["vector_row_id"] += row["id"] != number
    for offset in range(0, EXPECTED, 4096):
        batch = vectors[offset:offset+4096]
        bad["nonfinite_vectors"] += int((~np.isfinite(batch).all(axis=1)).sum())
        bad["unnormalized_vectors"] += int((np.abs(np.linalg.norm(batch, axis=1) - 1) > 1e-4).sum())
    total = sum(product_types.values())
    if total != EXPECTED or indexed != EXPECTED or product_types != manifest["product_types"] or domains != manifest["domains"]:
        raise ValueError("Catalog/FTS population or category/site counts changed")
    if any(bad.values()):
        raise ValueError(f"ABO consistency errors: {dict(bad)}")
    return {"catalog_rows": total, "fts_rows": indexed, "vector_shape": list(vectors.shape),
            "categories": len(product_types), "sites": len(domains), "issues": dict(bad),
            "product_types": dict(product_types), "domains": dict(domains),
            "database_sha256": manifest["database_sha256"], "vector_sha256": dense["vector_sha256"],
            "seconds": round(time.perf_counter() - started, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    path = parser.parse_args().output.resolve()
    if not path.is_relative_to(WORKSPACE / "reports") or path.exists():
        raise ValueError("Use a new output path under reports")
    value = verify()
    path.mkdir(parents=True)
    write_json(path / "coverage.json", value)
    inline = {k: v for k, v in value.items() if k not in ("product_types", "domains")}
    report = path / "全库一致性核验.txt"
    report.write_text("全库只读核验。检查逐行目录、FTS、源 SHA、E5 行映射和归一化。\n"
                      + json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
    operation("A 全库一致性核验", "逐条校验 SQLite 源摘要、复合身份、FTS 行及 E5 向量。", str(inline), [report])
    print(json.dumps(inline, ensure_ascii=False))


if __name__ == "__main__":
    main()
