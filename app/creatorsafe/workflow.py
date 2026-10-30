"""Small, local, single-operator workflow. SQLite serializes duplicate reservations."""
import hashlib
import json
import re
import shutil
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4


CHECKS = ("english_story", "original_value", "facts", "licenses_and_releases",
          "attribution", "visual_and_audio_quality", "ad_suitability", "ai_disclosure")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def normalize(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def similarity(left, right):
    """Conservative local heuristic, not semantic or internet-wide plagiarism detection."""
    a, b = normalize(left), normalize(right)
    def shingles(text):
        words = text.split()
        return {tuple(words[i:i + 3]) for i in range(max(1, len(words) - 2))}
    sa, sb = shingles(a), shingles(b)
    return max(SequenceMatcher(None, a, b, autojunk=False).ratio(),
               len(sa & sb) / max(1, len(sa | sb)))


def required_text(record, key):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value.strip()


def validate(plan):
    if plan.get("language") != "en":
        raise ValueError("MVP requires language=en; reviewer must verify spoken English")
    required_text(plan, "title")
    required_text(plan, "original_contribution")
    story = plan.get("story", {})
    script = "\n\n".join(required_text(story, key) for key in ("hook", "setup", "turn", "payoff"))
    if not 25 <= len(script.split()) <= 140:
        raise ValueError("Story must contain 25–140 words for a short English narrative")
    references = plan.get("references", [])
    if not isinstance(references, list) or any(not isinstance(r, str) or not r.strip() for r in references):
        raise ValueError("references must be a list of non-empty source descriptions or URLs")
    if plan.get("fiction") is not True and not references:
        raise ValueError("Nonfiction requires source references")
    if not isinstance(plan.get("fiction"), bool):
        raise ValueError("fiction must be a boolean")
    assets = plan.get("assets", [])
    if not assets or not any(a.get("kind") == "footage" for a in assets):
        raise ValueError("At least one licensed footage asset is required")
    if sum(a.get("kind") == "narration" for a in assets) != 1:
        raise ValueError("Exactly one licensed English narration file is required")
    for asset in assets:
        if asset.get("kind") not in {"footage", "narration"}:
            raise ValueError("MVP accepts footage and narration only; no background music")
        for key in ("path", "source_url", "creator", "license_name", "license_url",
                    "acquired_at", "evidence_path", "rights_reviewed_by", "rights_notes"):
            required_text(asset, key)
        datetime.fromisoformat(asset["acquired_at"])
        if asset.get("commercial_use") is not True or asset.get("derivatives") is not True:
            raise ValueError("Commercial use and derivatives must be explicitly permitted")
        if asset.get("rights_reviewed") is not True:
            raise ValueError("Human rights review is required, including third-party releases")
        if not isinstance(asset.get("attribution_required"), bool):
            raise ValueError("attribution_required must be a boolean")
        if asset["attribution_required"]:
            required_text(asset, "attribution")
        if not re.fullmatch(r"[a-f0-9]{64}", asset.get("sha256", "")):
            raise ValueError("Each asset requires its expected SHA-256")
    return script


class Workflow:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "catalog.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, script TEXT NOT NULL, "
                       "state TEXT NOT NULL, bundle TEXT, approval TEXT, output_hash TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def job_dir(self, job):
        if not re.fullmatch(r"[a-f0-9]{32}", job):
            raise ValueError("Invalid job ID")
        return self.root / job

    def prepare(self, plan_path, engine):
        plan_path = Path(plan_path).resolve()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        script = validate(plan)
        job = uuid4().hex
        folder = self.job_dir(job)
        # Reserve before paid/media work; concurrent equivalent requests cannot both render.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for previous, old_script in db.execute("SELECT id, script FROM jobs WHERE state != 'failed'"):
                if similarity(script, old_script) >= 0.80:
                    raise ValueError(f"Duplicate or near-duplicate story: {previous}")
            db.execute("INSERT INTO jobs(id,script,state) VALUES(?,?,'rendering')", (job, script))
        try:
            folder.mkdir()
            snapshots = []
            for index, asset in enumerate(plan["assets"]):
                record = dict(asset)
                for key in ("path", "evidence_path"):
                    source = (plan_path.parent / asset[key]).resolve()
                    target = folder / f"{index}-{key}{source.suffix.lower()}"
                    shutil.copyfile(source, target)
                    record[key] = target.name
                    record[key + "_sha256"] = digest(target)
                if record["path_sha256"] != asset["sha256"]:
                    raise ValueError("Asset checksum mismatch")
                snapshots.append(record)
            plan["assets"] = snapshots
            (folder / "plan.json").write_text(canonical(plan), encoding="utf-8")
            # The adapter receives only immutable job snapshots, not mutable input paths.
            output = Path(engine(job, script, plan, folder)).resolve()
            final = folder / "final.mp4"
            if output != final:
                shutil.copyfile(output, final)
            if final.stat().st_size == 0:
                raise ValueError("Engine returned empty output")
            output_hash = digest(final)
            hashes = {"plan.json": digest(folder / "plan.json"), "final.mp4": output_hash}
            for asset in snapshots:
                for key in ("path", "evidence_path"):
                    hashes[asset[key]] = asset[key + "_sha256"]
            bundle = {"job": job, "files": hashes, "checks": list(CHECKS),
                      "duplicate_method": "local normalized script similarity >=0.80 and exact video SHA-256",
                      "limitations": "No semantic/perceptual matching or external catalog; human review required"}
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT id FROM jobs WHERE output_hash=? AND state != 'failed'",
                              (output_hash,)).fetchone():
                    raise ValueError("Duplicate rendered video")
                db.execute("UPDATE jobs SET state='awaiting_review', bundle=?, output_hash=? WHERE id=?",
                           (canonical(bundle), output_hash, job))
            (folder / "review.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")
            return job
        except Exception:
            with self.connect() as db:
                db.execute("UPDATE jobs SET state='failed' WHERE id=?", (job,))
            raise

    def verified(self, job, db):
        folder = self.job_dir(job)
        row = db.execute("SELECT state,bundle,approval FROM jobs WHERE id=?", (job,)).fetchone()
        if not row or not row[1]:
            raise ValueError("No completed review package")
        bundle = json.loads(row[1])
        for filename, expected in bundle["files"].items():
            if digest(folder / filename) != expected:
                raise ValueError("Review package changed; create a new job and review again")
        return row, bundle

    def approve(self, job, reviewer, notes, checks):
        if not reviewer.strip() or not notes.strip() or set(checks) != set(CHECKS):
            raise ValueError("Named human reviewer, notes and all checks are required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row, bundle = self.verified(job, db)
            if row[0] != "awaiting_review":
                raise ValueError("Only an awaiting-review job can be approved")
            approval = {"reviewer": reviewer, "notes": notes, "checks": list(checks),
                        "at": datetime.now(timezone.utc).isoformat(),
                        "bundle_sha256": hashlib.sha256(canonical(bundle).encode()).hexdigest()}
            db.execute("UPDATE jobs SET state='approved',approval=? WHERE id=?", (canonical(approval), job))

    def reject(self, job, reviewer, notes):
        if not reviewer.strip() or not notes.strip():
            raise ValueError("Reviewer and rejection reason are required")
        self.job_dir(job)
        with self.connect() as db:
            result = db.execute("UPDATE jobs SET state='rejected',approval=? WHERE id=? AND state='awaiting_review'",
                                (canonical({"reviewer": reviewer, "reason": notes}), job))
            if result.rowcount != 1:
                raise ValueError("Only an awaiting-review job can be rejected")

    def export(self, job, destination):
        with self.connect() as db:
            row, bundle = self.verified(job, db)
            if row[0] != "approved" or not row[2]:
                raise ValueError("Human approval is required before export")
            approval = json.loads(row[2])
            if approval["bundle_sha256"] != hashlib.sha256(canonical(bundle).encode()).hexdigest():
                raise ValueError("Approval does not match the review package")
            target = Path(destination).resolve()
            target.mkdir(parents=True, exist_ok=False)
            try:
                # Recheck the copied bytes too; a changed source must never escape the gate.
                for filename, expected in bundle["files"].items():
                    shutil.copyfile(self.job_dir(job) / filename, target / filename)
                    if digest(target / filename) != expected:
                        raise ValueError("Package changed during export")
                (target / "approval.json").write_text(json.dumps(approval, indent=2), encoding="utf-8")
                (target / "review.json").write_text(json.dumps(bundle, indent=2), encoding="utf-8")
                plan = json.loads((target / "plan.json").read_text(encoding="utf-8"))
                credits = [a.get("attribution", "") for a in plan["assets"] if a.get("attribution")]
                (target / "credits.txt").write_text("\n".join(credits), encoding="utf-8")
            except Exception:
                shutil.rmtree(target)
                raise
        return target
