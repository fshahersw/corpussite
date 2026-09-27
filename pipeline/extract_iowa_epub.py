"""Bounded offline extraction of the explicitly identified saved Iowa EPUB."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
from urllib.parse import unquote, urlsplit
import zipfile

from lxml import etree


VERSION = "1.0.0"
COLLECTION = Path(__file__).resolve().parents[1] / "corpus" / "official_law_pages"
TARGET_SHA = "4b473ca23c98470e0a4157b782824c363acca7458fb2ad812e62dd55f4fe9333"
TARGET_URL = "https://www.legis.iowa.gov/docs/IACODE/IowaCodeWithActs.epub"
OUT = COLLECTION / "epub_document_derivatives"
OPF = "http://www.idpf.org/2007/opf"
XHTML = "http://www.w3.org/1999/xhtml"
CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
LIMITS = {"max_members": 5000, "max_member_bytes": 16 * 1024 * 1024,
          "max_total_uncompressed_bytes": 256 * 1024 * 1024,
          "max_compression_ratio": 500, "max_text_bytes": 256 * 1024 * 1024,
          "minimum_free_disk_bytes": 512 * 1024 * 1024}
# Only this observed, external-only declaration may be removed before parsing.
# No DTD is loaded or processed; internal subsets and all custom entities fail.
XHTML_DOCTYPE = re.compile(
    r'<!DOCTYPE\s+html\s+PUBLIC\s+"-//W3C//DTD XHTML 1\.1//EN"\s+'
    r'"http://www\.w3\.org/TR/xhtml11/DTD/xhtml11\.dtd"\s*>')
BLOCKS = {"body", "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
          "section", "article", "header", "footer", "blockquote", "pre", "table", "tr",
          "dl", "dt", "dd", "figure", "figcaption", "address", "hr"}
SKIP_TAGS = {"script", "style", "template", "object", "iframe", "embed", "img", "audio", "video"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value, jsonl=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=None if jsonl else 2) + "\n", encoding="utf-8")
    tmp.replace(path)


def safe_member(name):
    stripped = name[:-1] if name.endswith("/") else name
    return bool(stripped and not stripped.startswith("/") and not any(c in stripped for c in "\\:\x00")
                and all(p not in ("", ".", "..") for p in stripped.split("/")))


def package_href(package, href):
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("OPF member references must be local paths without URL components")
    name = unquote(parsed.path, encoding="utf-8", errors="strict")
    if not safe_member(name) or name.endswith("/"):
        raise ValueError("Unsafe OPF member reference")
    resolved = str(PurePosixPath(package).parent / name)
    if not safe_member(resolved):
        raise ValueError("Unsafe resolved OPF member reference")
    return resolved


class NoExternalResolver(etree.Resolver):
    def resolve(self, url, public_id, context):
        raise ValueError("External XML resources are prohibited")


def safe_xml(data, *, xhtml=False):
    if len(data) > LIMITS["max_member_bytes"]:
        raise ValueError("XML part exceeds byte limit")
    # This explicit pass supports UTF-8 only; NULs cannot hide UTF-16/32 entities.
    text = data.decode("utf-8-sig", errors="strict")
    if "\x00" in text or "<!ENTITY" in text.upper():
        raise ValueError("NULs and entity declarations are prohibited")
    suppressed = False
    if "<!DOCTYPE" in text.upper():
        matches = list(XHTML_DOCTYPE.finditer(text)) if xhtml else []
        if len(matches) != 1:
            raise ValueError("DTD/internal-subset declarations are prohibited")
        text = text[:matches[0].start()] + text[matches[0].end():]
        suppressed = True
        if "<!DOCTYPE" in text.upper():
            raise ValueError("Additional DTD declarations are prohibited")
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             dtd_validation=False, attribute_defaults=False, recover=False,
                             huge_tree=False, remove_comments=True, remove_pis=True)
    parser.resolvers.add(NoExternalResolver())
    root = etree.fromstring(text.encode("utf-8"), parser)
    if root.getroottree().docinfo.doctype or any(isinstance(e, etree._Entity) for e in root.iter()):
        raise ValueError("DTD or unresolved entity found after safe parsing")
    return root, suppressed


def xhtml_text(data):
    root, suppressed = safe_xml(data, xhtml=True)
    if root.tag != "{" + XHTML + "}html":
        raise ValueError("Spine item is not an XHTML document")
    bodies = root.findall("{" + XHTML + "}body")
    if len(bodies) != 1:
        raise ValueError("XHTML part must contain exactly one body")
    chunks, skipped = [], Counter()

    def visit(element):
        name = etree.QName(element)
        tag = name.localname
        if name.namespace != XHTML or tag in SKIP_TAGS:
            skipped[("foreign:" if name.namespace != XHTML else "") + tag] += 1
            return
        if tag in BLOCKS or tag == "br":
            chunks.append("\n")
        if element.text:
            chunks.append(re.sub(r"\s+", " ", element.text))
        for child in element:
            visit(child)
            if child.tail:
                chunks.append(re.sub(r"\s+", " ", child.tail))
        if tag in BLOCKS:
            chunks.append("\n")
        elif tag in ("td", "th"):
            chunks.append("\t")

    visit(bodies[0])
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in "".join(chunks).split("\n")]
    lines = [line for line in lines if line]
    result = "\n".join(lines) + ("\n" if lines else "")
    return result, {"text_paragraphs": len(lines), "paragraph_text_characters": sum(map(len, lines)),
                    "external_doctype_suppressed_not_loaded": suppressed,
                    "unsupported_subtrees_not_rendered": dict(skipped)}


def validate_archive(archive):
    infos = archive.infolist()
    names = [i.filename for i in infos]
    if len(infos) > LIMITS["max_members"] or len(names) != len(set(names)):
        raise ValueError("Too many or duplicate archive members")
    if any(not safe_member(n) for n in names):
        raise ValueError("Unsafe archive member path")
    if any(stat.S_ISLNK(i.external_attr >> 16) or i.flag_bits & 1 for i in infos):
        raise ValueError("Symlinks or encrypted ZIP entries are unsupported")
    if any(i.file_size > LIMITS["max_member_bytes"] or i.file_size / max(i.compress_size, 1) > LIMITS["max_compression_ratio"] for i in infos):
        raise ValueError("Archive member exceeds size or compression bound")
    if sum(i.file_size for i in infos) > LIMITS["max_total_uncompressed_bytes"]:
        raise ValueError("Archive total exceeds extraction bound")
    if "META-INF/encryption.xml" in names:
        raise ValueError("EPUB encryption metadata requires separate review")
    return infos


def extract_epub(raw, out, raw_sha):
    """Read ZIP bytes without ever writing a ZIP member to a filesystem path."""
    raw, out = Path(raw), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(out).free < LIMITS["minimum_free_disk_bytes"] + LIMITS["max_text_bytes"]:
        raise ValueError("Insufficient free disk space for bounded EPUB derivative")
    text_path = out / "text" / (raw_sha + ".txt")
    if not re.fullmatch(r"[0-9a-f]{64}", raw_sha):
        raise ValueError("Invalid source hash")
    text_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = text_path.with_suffix(".txt.tmp")
    with zipfile.ZipFile(raw) as archive:
        infos = validate_archive(archive)
        # Hash every member, including unrendered CSS/navigation assets. A ZIP
        # CRC mismatch or an oversized actual read fails before publication.
        parts = {}
        for info in infos:
            h, count = hashlib.sha256(), 0
            with archive.open(info) as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    count += len(chunk)
                    if count > LIMITS["max_member_bytes"]:
                        raise ValueError("Actual member byte count exceeds limit")
                    h.update(chunk)
            if count != info.file_size:
                raise ValueError("ZIP member size mismatch")
            parts[info.filename] = {"package_part": info.filename, "sha256": h.hexdigest(),
                                    "uncompressed_bytes": count, "compressed_bytes": info.compress_size,
                                    "role": "non_spine", "status": "skipped_non_spine",
                                    "reason": "Asset is outside the OPF spine and was hashed without rendering"}
        if archive.read("mimetype") != b"application/epub+zip":
            raise ValueError("Archive does not declare the EPUB container mimetype")
        container_warnings = []
        if infos[0].filename != "mimetype":
            container_warnings.append("The mimetype member is not first in ZIP order; content/container/OPF validation identifies this EPUB.")
        if archive.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
            container_warnings.append("The mimetype member is compressed; its decoded value was validated.")
        container, _ = safe_xml(archive.read("META-INF/container.xml"))
        if container.tag != "{" + CONTAINER + "}container":
            raise ValueError("Invalid EPUB container root")
        roots = container.findall("{" + CONTAINER + "}rootfiles/{" + CONTAINER + "}rootfile")
        if len(roots) != 1 or roots[0].get("media-type") != "application/oebps-package+xml":
            raise ValueError("Expected exactly one OPF rootfile")
        package = roots[0].get("full-path", "")
        if not safe_member(package) or package not in parts:
            raise ValueError("Unsafe or missing OPF rootfile path")
        opf, _ = safe_xml(archive.read(package))
        if opf.tag != "{" + OPF + "}package":
            raise ValueError("Invalid OPF package root")
        if any("{http://www.w3.org/XML/1998/namespace}base" in e.attrib for e in opf.iter()):
            raise ValueError("OPF xml:base resolution is outside this bounded pass")
        manifest = opf.find("{" + OPF + "}manifest")
        spine = opf.find("{" + OPF + "}spine")
        if manifest is None or spine is None or not len(spine):
            raise ValueError("Missing OPF manifest or spine")
        items = {}
        for item in manifest:
            identifier = item.get("id", "")
            if item.tag != "{" + OPF + "}item" or not identifier or identifier in items:
                raise ValueError("Invalid or duplicate OPF manifest ID")
            name = package_href(package, item.get("href", ""))
            if name not in parts:
                raise ValueError("OPF member is missing from ZIP")
            items[identifier] = {"name": name, "media_type": item.get("media-type", ""),
                                 "properties": item.get("properties", ""), "href": item.get("href")}
            parts[name]["manifest_ids"] = parts[name].get("manifest_ids", []) + [identifier]
            parts[name]["media_type"] = item.get("media-type", "")
        selected, seen = [], set()
        for index, ref in enumerate(spine, 1):
            identifier = ref.get("idref")
            if ref.tag != "{" + OPF + "}itemref" or identifier not in items:
                raise ValueError("Spine references an invalid manifest ID")
            item = items[identifier]
            if item["name"] in seen:
                raise ValueError("Duplicate spine member requires separate review")
            if ref.get("linear", "yes") not in ("yes", "no"):
                raise ValueError("Invalid OPF spine linear attribute")
            seen.add(item["name"])
            selected.append((index, identifier, ref.get("linear", "yes"), item))
        for name, role in (("mimetype", "container_mimetype"), ("META-INF/container.xml", "container"), (package, "package_document")):
            parts[name].update(role=role, status="validated")
            parts[name].pop("reason", None)
        totals, skipped_spine, text_bytes = Counter(), [], 0
        with tmp.open("wb") as output:
            for index, identifier, linear, item in selected:
                part = parts[item["name"]]
                part.update(role="spine", spine_index=index, idref=identifier, linear=linear)
                if item["media_type"] != "application/xhtml+xml":
                    part.update(status="skipped_unsupported_spine", reason="Only XHTML spine items are rendered")
                    skipped_spine.append(item["name"])
                    continue
                body, statistics = xhtml_text(archive.read(item["name"]))
                block = ("\n===== EPUB SPINE PART " + str(index) + ": " + item["name"] + " =====\n" + body).encode("utf-8")
                text_bytes += len(block)
                if text_bytes > LIMITS["max_text_bytes"]:
                    raise ValueError("Derivative text exceeds byte bound")
                output.write(block)
                part.update(status="extracted", xml_sha256=part["sha256"],
                            xml_bytes_read=part["uncompressed_bytes"],
                            text_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                            text_bytes=len(body.encode("utf-8")), **statistics)
                part.pop("reason", None)
                totals.update({k: statistics[k] for k in ("text_paragraphs", "paragraph_text_characters")})
                totals["extracted_spine_items"] += 1
        if not totals["extracted_spine_items"]:
            raise ValueError("No XHTML spine content extracted")
        tmp.replace(text_path)
        metadata = opf.find("{" + OPF + "}metadata")
        opf_metadata = [{"tag": e.tag, "text": " ".join("".join(e.itertext()).split()),
                         "attributes": dict(e.attrib)} for e in metadata] if metadata is not None else []
        return {"status": "extracted" if not skipped_spine else "extracted_partial",
                "extractor_version": VERSION, "extraction_method": "Python zipfile + guarded lxml XHTML body text in validated OPF spine order",
                "parser": {"name": "lxml.etree", "version": etree.LXML_VERSION, "libxml_version": etree.LIBXML_VERSION,
                           "python_version": sys.version.split()[0], "DTD_loading": False,
                           "entity_resolution": False, "network_enabled": False, "recover": False},
                "parent_raw_sha256": raw_sha, "detected_format": "epub", "container_mimetype": "application/epub+zip",
                "container_validation_warnings": container_warnings,
                "mimetype_zip_index": [i.filename for i in infos].index("mimetype") + 1,
                "mimetype_compression_method": archive.getinfo("mimetype").compress_type,
                "package_document": package, "opf_version": opf.get("version"), "opf_metadata": opf_metadata,
                "reading_order": "OPF spine itemref document order, including any linear=no items, without following links",
                "spine_items": len(selected), "extracted_spine_items": totals["extracted_spine_items"],
                "skipped_unsupported_spine_parts": skipped_spine, "package_parts": list(parts.values()),
                "archive_members": len(infos), "archive_uncompressed_bytes": sum(i.file_size for i in infos),
                "text_path": str(text_path.resolve()), "text_sha256": digest(text_path), "text_bytes": text_bytes,
                "text_paragraphs": totals["text_paragraphs"], "paragraph_text_characters": totals["paragraph_text_characters"],
                "extraction_limits": LIMITS.copy(), "derived_at_utc": now(), "network_requests": 0,
                "archive_members_written_to_filesystem": 0, "originals_modified": False,
                "limitations": [
                    "UTF-8 XML only. The observed standard external XHTML 1.1 DOCTYPE is removed in memory; no DTD or custom entity is processed.",
                    "Text follows the validated OPF spine, not ZIP ordering or hyperlinks. Edition/title metadata is preserved and is not a current-law assertion.",
                    "Plain body text normalizes whitespace and labels package parts. Paragraph counts are nonempty text blocks; table layout, CSS, and amendment emphasis are not rendered.",
                    "Scripts, styles, embedded objects, media and foreign-namespace subtrees are skipped with counts. Navigation/CSS/non-spine assets are hashed but not rendered or fetched.",
                    "No archive paths are written, no links are followed, no scripts execute, and no DTD, XInclude, XSLT or external resource is fetched."]}


def main():
    source_manifest = COLLECTION / "document_derivatives" / "manifest.jsonl"
    rows = [json.loads(line) for line in source_manifest.read_text(encoding="utf-8").splitlines() if line]
    matches = [row for row in rows if row.get("parent_raw_sha256") == TARGET_SHA and row.get("source_url") == TARGET_URL]
    if len(matches) != 1:
        raise ValueError("Expected exactly one explicitly identified Iowa EPUB source record")
    source = matches[0]
    record = {k: v for k, v in source.items() if k not in ("status", "note", "error")}
    record.update(upstream_inventory_status=source["status"], source_inventory_path=str(source_manifest),
                  source_inventory_sha256=digest(source_manifest), snapshot_at_utc=now())
    try:
        raw, meta = Path(record["parent_raw_path"]).resolve(), Path(record["parent_metadata_path"]).resolve()
        if not raw.is_relative_to((COLLECTION / "raw").resolve()) or not meta.is_relative_to((COLLECTION / "metadata").resolve()):
            raise ValueError("Original paths escape the explicit collection")
        metadata = json.loads(meta.read_text(encoding="utf-8"))
        if not metadata.get("raw_complete") or not 200 <= metadata.get("http_status", 0) < 300:
            raise ValueError("Saved metadata does not record a complete successful source")
        if digest(raw) != TARGET_SHA or metadata.get("sha256") != TARGET_SHA or raw.stat().st_size != record["parent_byte_count"]:
            raise ValueError("Saved source hash or byte count differs from provenance")
        if digest(meta) != record["parent_metadata_sha256"]:
            raise ValueError("Saved original metadata hash differs from source inventory")
        record.update(parent_sha256_verified=True, declared_format="application/octet-stream")
        record.update(extract_epub(raw, OUT, TARGET_SHA))
    except Exception as exc:
        record.update(status="extraction_error", error=type(exc).__name__ + ": " + str(exc))
    write_json(OUT / "metadata" / (TARGET_SHA + ".json"), record)
    write_json(OUT / "manifest.jsonl", record, jsonl=True)
    summary = {k: record.get(k) for k in ("status", "source_url", "parent_raw_sha256", "text_path", "text_sha256",
               "text_bytes", "text_paragraphs", "paragraph_text_characters", "spine_items", "extracted_spine_items", "archive_members", "error")}
    summary.update(generated_at_utc=now(), network_requests=0, original_payloads_and_metadata_modified=False,
                   archive_members_written_to_filesystem=0, source_documents_processed=1, full_source_corpus_complete=False)
    write_json(OUT / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if record["status"] == "extracted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
