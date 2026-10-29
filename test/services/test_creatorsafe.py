import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from app.creatorsafe.engine import render
from app.creatorsafe.workflow import CHECKS, Workflow, digest, similarity


class CreatorSafeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workflow = Workflow(self.base / "jobs")
        (self.base / "clip.mp4").write_bytes(b"footage")
        (self.base / "voice.wav").write_bytes(b"narration")
        (self.base / "license.txt").write_text("Signed commercial derivative grant", encoding="utf-8")
        self.plan = {
            "title": "The seed ledger", "language": "en", "fiction": True,
            "original_contribution": "An original fictional story about tracking small experiments",
            "story": {
                "hook": "Mara almost threw away her smallest seed.",
                "setup": "Her balcony pots had failed all spring, so she began a notebook recording each watering.",
                "turn": "One quiet morning, the neglected pot sprouted while the others stood empty.",
                "payoff": "She stopped guessing, changed one habit at a time, and finally learned what her garden needed.",
            },
            "assets": [],
        }
        for kind, name in (("footage", "clip.mp4"), ("narration", "voice.wav")):
            self.plan["assets"].append({
                "kind": kind, "path": name, "sha256": digest(self.base / name),
                "source_url": "https://example.com/my-owned-work", "creator": "Test creator",
                "license_name": "Owned work", "license_url": "https://example.com/grant",
                "acquired_at": "2026-10-30", "evidence_path": "license.txt",
                "commercial_use": True, "derivatives": True, "rights_reviewed": True,
                "rights_reviewed_by": "Human tester", "rights_notes": "No third-party rights depicted",
                "attribution_required": True, "attribution": "Created by Test creator",
            })

    def prepare(self, output=b"unique-video", engine=None):
        plan_path = self.base / "plan.json"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        def fake_engine(job, script, plan, folder):
            video = folder / "rendered.mp4"
            video.write_bytes(output)
            return video
        return self.workflow.prepare(plan_path, engine or fake_engine)

    def test_approval_required_and_export_preserves_provenance(self):
        job = self.prepare()
        target = self.base / "export"
        with self.assertRaisesRegex(ValueError, "approval"):
            self.workflow.export(job, target)
        self.assertFalse(target.exists())
        with self.assertRaises(ValueError):
            self.workflow.approve(job, "", "Watched", CHECKS)
        with self.assertRaises(ValueError):
            self.workflow.approve(job, "Alice", "Watched", CHECKS[:-1])
        self.workflow.approve(job, "Alice", "Watched and verified rights", CHECKS)
        self.workflow.export(job, target)
        self.assertEqual((target / "final.mp4").read_bytes(), b"unique-video")
        self.assertIn("Test creator", (target / "credits.txt").read_text())
        self.assertEqual(json.loads((target / "approval.json").read_text())["reviewer"], "Alice")
        self.assertTrue(list(target.glob("*evidence_path.txt")))

    def test_modified_package_invalidates_approval(self):
        for filename in ("final.mp4", "plan.json", "0-evidence_path.txt", "1-path.wav"):
            with self.subTest(filename=filename):
                workflow = Workflow(self.base / filename.replace(".", "-"))
                original = self.workflow
                self.workflow = workflow
                job = self.prepare()
                self.workflow.approve(job, "Alice", "Reviewed", CHECKS)
                (self.workflow.job_dir(job) / filename).write_bytes(b"modified")
                with self.assertRaisesRegex(ValueError, "changed"):
                    self.workflow.export(job, self.base / (job + "export"))
                self.workflow = original

    def test_checksum_and_missing_rights_fail_before_engine(self):
        engine = Mock()
        self.plan["assets"][0]["commercial_use"] = False
        with self.assertRaises(ValueError):
            self.prepare(engine=engine)
        self.plan["assets"][0]["commercial_use"] = True
        (self.base / "clip.mp4").write_bytes(b"changed input")
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.prepare(engine=engine)
        engine.assert_not_called()

    def test_duplicate_story_blocks_before_render_even_after_restart(self):
        self.prepare()
        self.workflow = Workflow(self.workflow.root)
        self.plan["story"]["hook"] = "MARA almost threw away her smallest seed!!!"
        engine = Mock()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.prepare(engine=engine)
        engine.assert_not_called()

    def test_duplicate_video_blocks_different_story(self):
        self.prepare()
        self.plan["story"] = {
            "hook": "An old lighthouse blinked twice at noon.",
            "setup": "The keeper climbed every stair searching for the source of the unexpected signal.",
            "turn": "A loose wire touched the copper housing whenever gulls landed on the railing.",
            "payoff": "After a careful repair, silence returned and the harbor slept peacefully that evening.",
        }
        with self.assertRaisesRegex(ValueError, "Duplicate rendered"):
            self.prepare()

    def test_rejection_and_render_failure_cannot_be_approved(self):
        job = self.prepare()
        self.workflow.reject(job, "Alice", "Story needs work")
        with self.assertRaises(ValueError):
            self.workflow.approve(job, "Alice", "Reviewed", CHECKS)
        self.plan["story"]["hook"] = "Another entirely different adventure begins in a snowy valley far away."
        # Failed jobs retain evidence but are never eligible for approval/export.
        other = Workflow(self.base / "failed")
        self.workflow = other
        with self.assertRaises(RuntimeError):
            self.prepare(engine=Mock(side_effect=RuntimeError("render failed")))
        with closing(sqlite3.connect(other.db_path)) as db:
            job, state = db.execute("SELECT id,state FROM jobs").fetchone()
        self.assertEqual(state, "failed")
        with self.assertRaises(ValueError):
            other.approve(job, "Alice", "Reviewed", CHECKS)

    def test_similarity_detects_minor_rewrite(self):
        self.assertGreaterEqual(similarity("One small seed grew in a pot today.",
                                           "One small seed grew in the pot today!"), 0.8)

    def test_engine_adapter_passes_reviewed_inputs_to_existing_pipeline(self):
        schema = types.ModuleType("app.models.schema")
        def model(**kwargs):
            return types.SimpleNamespace(**kwargs)
        schema.VideoParams = model
        schema.MaterialInfo = model
        services = types.ModuleType("app.services")
        services.material_upload = types.SimpleNamespace(save_material_upload=Mock(return_value="stored.mp4"))
        output = self.base / "result.mp4"
        output.write_bytes(b"engine video")
        services.task = types.SimpleNamespace(start=Mock(return_value={
            "videos": [str(output)], "audio_duration": 35,
        }))
        services.upload_post = types.SimpleNamespace(upload_post_service=types.SimpleNamespace(auto_upload=False))
        with patch.dict(sys.modules, {"app.models.schema": schema, "app.services": services}):
            job = self.prepare(engine=render)
        args, kwargs = services.task.start.call_args
        self.assertEqual(args[1].video_source, "local")
        self.assertEqual(args[1].video_language, "en")
        self.assertEqual(args[1].video_aspect, "9:16")
        self.assertEqual(args[1].bgm_volume, 0)
        self.assertEqual(args[1].video_count, 1)
        self.assertEqual(args[1].video_materials[0].url, "stored.mp4")
        self.assertTrue(kwargs["allow_server_file_input"])
        self.assertEqual((self.workflow.job_dir(job) / "final.mp4").read_bytes(), b"engine video")

    def test_upload_boundary_blocks_old_settings_and_direct_calls(self):
        # Load the actual service with inert dependencies: test must never need network/keys.
        config = types.ModuleType("app.config")
        config.config = types.SimpleNamespace(app={"upload_post_auto_upload": True,
                                                    "upload_post_enabled": True})
        request_logging = types.ModuleType("app.utils.request_logging")
        request_logging.trace_http_call = Mock()
        requests = types.ModuleType("requests")
        requests.post = Mock(side_effect=AssertionError("Network must never be called"))
        loguru = types.ModuleType("loguru")
        loguru.logger = Mock()
        path = Path(__file__).resolve().parents[2] / "app/services/upload_post.py"
        spec = importlib.util.spec_from_file_location("creatorsafe_upload_boundary_test", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"app.config": config, "requests": requests,
                                      "loguru": loguru, "app.utils.request_logging": request_logging}):
            spec.loader.exec_module(module)
        service = module.UploadPostService()
        self.assertFalse(service.auto_upload)
        for privacy in ("public", "private", "unlisted"):
            result = service.upload_video("anything.mp4", "Story", ["youtube"],
                                          youtube_extra={"privacyStatus": privacy})
            self.assertFalse(result["success"])
            self.assertIn("publishing disabled", result["error"])
        requests.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
