from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image

from glkvm_mcp import server
from glkvm_mcp.kvmd import GlkvmError, InputOutcomeUnknown


def _ocr_lines(count: int = 1, text: str = "Status", score: float = 0.91) -> list:
    results = []
    for index in range(count):
        y = 10 + index * 30
        results.append(
            (
                [[10, y], [210, y], [210, y + 20], [10, y + 20]],
                text,
                score,
            )
        )
    return results


def _payload(result) -> dict:
    if len(result.content) != 1 or result.content[0].type != "text":
        raise AssertionError(f"expected one JSON text block, got {result.content!r}")
    if result.structured_content is not None:
        raise AssertionError("observation JSON was duplicated as structured content")
    return json.loads(result.content[0].text)


class ObserveScreenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.image = Image.new("RGB", (320, 200), color=(10, 20, 30))
        self.snapshot = AsyncMock(return_value=(self.image, "frame-digest12"))
        self.type_text_call = AsyncMock()
        self.login = AsyncMock(return_value="sensitive-token-value")
        self.original_client = server.client
        self.original_ocr = server.ocr
        server.client = SimpleNamespace(
            snapshot=self.snapshot,
            type_text=self.type_text_call,
            login=self.login,
        )
        server.ocr = SimpleNamespace(run=Mock(return_value=(_ocr_lines(), 5.0)))
        server._tree_cache.clear()

    def tearDown(self) -> None:
        server.client = self.original_client
        server.ocr = self.original_ocr
        server._tree_cache.clear()

    async def test_observe_is_fresh_text_only_and_reports_provenance_and_cache(
        self,
    ) -> None:
        first = await server.mcp.call_tool("observe_screen", {})
        second = await server.mcp.call_tool("observe_screen", {})
        one = _payload(first)
        two = _payload(second)

        self.assertEqual(self.snapshot.await_count, 2)
        self.assertNotEqual(one["observation_id"], two["observation_id"])
        self.assertEqual(one["resolution_px"], {"width": 320, "height": 200})
        digest = hashlib.sha256()
        digest.update(self.image.width.to_bytes(8, "big"))
        digest.update(self.image.height.to_bytes(8, "big"))
        digest.update(self.image.tobytes())
        self.assertEqual(one["frame_sha256"], digest.hexdigest())
        self.assertIsNone(one["device_capture_timestamp_utc"])
        self.assertEqual(one["target_identity"]["status"], "unknown")
        self.assertFalse(one["ocr"]["cached"])
        self.assertTrue(two["ocr"]["cached"])
        self.assertEqual(len(one["lines"]), 1)
        self.assertEqual(one["lines"][0]["bbox_xywh_px"], [10, 10, 200, 20])
        self.assertEqual(one["lines"][0]["confidence_quality"], "estimated")
        self.assertEqual(server.ocr.run.call_count, 1)

    async def test_bottom_selection_returns_tail_in_screen_order_and_reports_omissions(
        self,
    ) -> None:
        results = []
        for index in range(6):
            y = 10 + index * 25
            results.append(
                (
                    [[10, y], [210, y], [210, y + 20], [10, y + 20]],
                    f"line-{index}",
                    0.91,
                )
            )
        server.ocr.run = Mock(return_value=(results, 5.0))
        result = await server.mcp.call_tool(
            "observe_screen",
            {"max_lines": 2, "line_selection": "bottom"},
        )
        payload = _payload(result)

        self.assertEqual(
            [line["text"] for line in payload["lines"]], ["line-4", "line-5"]
        )
        self.assertEqual(
            [line["bbox_xywh_px"][1] for line in payload["lines"]], [110, 135]
        )
        self.assertEqual(payload["truncation"]["lines_omitted"], 4)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["limits"]["line_selection"], "bottom")

    async def test_bottom_selection_keeps_tail_when_character_budget_truncates(
        self,
    ) -> None:
        results = []
        for index in range(6):
            y = 10 + index * 25
            results.append(
                (
                    [[10, y], [210, y], [210, y + 20], [10, y + 20]],
                    f"line-{index}-" + ("x" * 600),
                    0.91,
                )
            )
        server.ocr.run = Mock(return_value=(results, 5.0))
        result = await server.mcp.call_tool(
            "observe_screen",
            {
                "max_lines": 2,
                "max_chars": 1500,
                "line_selection": "bottom",
            },
        )
        payload = _payload(result)

        self.assertLessEqual(
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            1500,
        )
        self.assertEqual(len(payload["lines"]), 1)
        self.assertTrue(payload["lines"][0]["text"].startswith("line-5-"))
        self.assertEqual(payload["truncation"]["lines_omitted"], 5)
        self.assertTrue(payload["truncated"])

    async def test_low_confidence_is_returned_as_uncertain(self) -> None:
        server._tree_cache.clear()
        server.ocr.run = Mock(return_value=(_ocr_lines(score=0.21), 4.0))
        result = await server.mcp.call_tool("observe_screen", {})

        payload = _payload(result)
        line = payload["lines"][0]
        self.assertEqual(line["confidence"], 0.21)
        self.assertEqual(line["confidence_quality"], "low")
        self.assertIn("may be wrong", payload["interpretation"])

    async def test_serialized_observation_respects_both_output_limits(self) -> None:
        server._tree_cache.clear()
        server.ocr.run = Mock(return_value=(_ocr_lines(8, "x" * 600), 5.0))
        result = await server.mcp.call_tool(
            "observe_screen",
            {"max_lines": 2, "max_chars": 1024},
        )
        payload = _payload(result)

        self.assertLessEqual(len(payload["lines"]), 2)
        self.assertLessEqual(
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
            1024,
        )
        self.assertTrue(payload["truncated"])
        self.assertGreater(payload["truncation"]["lines_omitted"], 0)

    async def test_saved_frames_are_unique_private_and_have_byte_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GLKVM_ARTIFACT_DIR"] = tmp
            try:
                first = await server.mcp.call_tool(
                    "observe_screen", {"save_frame": True}
                )
                second = await server.mcp.call_tool(
                    "observe_screen", {"save_frame": True}
                )
                first_payload = _payload(first)
                second_payload = _payload(second)
                one = first_payload["saved_frame"]
                two = second_payload["saved_frame"]
                path_one, path_two = Path(one["path"]), Path(two["path"])
                self.assertNotEqual(path_one, path_two)
                self.assertEqual(path_one.read_bytes(), path_two.read_bytes())
                self.assertEqual(one["bytes"], path_one.stat().st_size)
                self.assertEqual(
                    one["sha256"], hashlib.sha256(path_one.read_bytes()).hexdigest()
                )
                self.assertEqual(stat.S_IMODE(path_one.stat().st_mode), 0o400)
                self.assertEqual(stat.S_IMODE(path_one.parent.stat().st_mode), 0o700)
                self.assertEqual(
                    one["provenance"]["observation_id"],
                    first_payload["observation_id"],
                )
            finally:
                os.environ.pop("GLKVM_ARTIFACT_DIR", None)

    async def test_existing_permissive_directory_is_not_changed_and_limits_prevent_orphans(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "existing"
            artifact_dir.mkdir(mode=0o755)
            artifact_dir.chmod(0o755)
            os.environ["GLKVM_ARTIFACT_DIR"] = str(artifact_dir)
            try:
                with self.assertRaises(OSError):
                    server._artifact_directory()
                self.assertEqual(stat.S_IMODE(artifact_dir.stat().st_mode), 0o755)

                artifact_dir.chmod(0o700)
                with unittest.mock.patch.object(
                    server, "_observation_payload", side_effect=ValueError("too large")
                ):
                    result = await server.mcp.call_tool(
                        "observe_screen",
                        {"save_frame": True},
                    )
                self.assertFalse(_payload(result)["ok"])
                self.assertEqual(list(artifact_dir.iterdir()), [])
            finally:
                os.environ.pop("GLKVM_ARTIFACT_DIR", None)

    async def test_relative_artifact_directory_returns_an_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=".") as tmp:
            expected_parent = Path(tmp).absolute()
            with unittest.mock.patch.dict(os.environ, {"GLKVM_ARTIFACT_DIR": tmp}):
                result = await server.mcp.call_tool(
                    "observe_screen", {"save_frame": True}
                )
                payload = json.loads(result.content[0].text)
                self.assertTrue(payload["ok"])
                artifact = payload["saved_frame"]
                path = Path(artifact["path"])
                self.assertTrue(path.is_absolute())
                self.assertEqual(path.parent, expected_parent)
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), artifact["sha256"]
                )

    async def test_capture_and_save_failures_never_return_prior_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GLKVM_ARTIFACT_DIR"] = tmp
            try:
                previous = await server.mcp.call_tool(
                    "observe_screen", {"save_frame": True}
                )
                self.snapshot.side_effect = RuntimeError(
                    "stale server response with token"
                )
                capture_failure = await server.mcp.call_tool(
                    "observe_screen", {"save_frame": True}
                )
                previous_payload = _payload(previous)
                capture_payload = _payload(capture_failure)
                self.assertFalse(capture_payload["ok"])
                self.assertTrue(capture_failure.is_error)
                self.assertEqual(
                    capture_payload["failure"],
                    {
                        "stage": "capture",
                        "category": "internal",
                    },
                )
                self.assertNotIn("saved_frame", capture_payload)
                self.assertNotIn("token", json.dumps(capture_payload).lower())

                self.snapshot.side_effect = None
                self.snapshot.return_value = (self.image, "frame-digest12")
                with unittest.mock.patch.object(
                    server, "_save_frame", side_effect=OSError("disk full")
                ):
                    save_failure = await server.mcp.call_tool(
                        "observe_screen", {"save_frame": True}
                    )
                save_payload = _payload(save_failure)
                self.assertFalse(save_payload["ok"])
                self.assertNotIn("saved_frame", save_payload)
                self.assertNotIn(
                    previous_payload["saved_frame"]["path"], json.dumps(save_payload)
                )
            finally:
                os.environ.pop("GLKVM_ARTIFACT_DIR", None)

    async def test_capture_error_includes_only_sanitized_category_and_http_status(
        self,
    ) -> None:
        self.snapshot.side_effect = GlkvmError(
            "https://device.invalid?token=private response text",
            category="authentication",
            status_code=403,
        )
        result = await server.mcp.call_tool("observe_screen", {})

        self.assertTrue(result.is_error)
        payload = _payload(result)
        self.assertEqual(
            payload["failure"],
            {
                "stage": "capture",
                "category": "authentication",
                "http_status": 403,
            },
        )
        self.assertNotIn("device.invalid", json.dumps(payload))
        self.assertNotIn("private response text", json.dumps(payload))

    async def test_ocr_cache_includes_letters_and_score_options(self) -> None:
        await server.screen_elements()
        await server.screen_elements()
        await server.screen_elements(letters=True)
        await server.screen_elements(min_score=0.9)
        self.assertEqual(server.ocr.run.call_count, 3)

    async def test_type_text_omits_value_only_when_requested_and_refresh_auth_sanitizes(
        self,
    ) -> None:
        secret = "password-token-should-not-be-returned"
        hidden = await server.mcp.call_tool(
            "type_text",
            {"text": secret, "echo_text": False},
        )
        self.assertNotIn(secret, json.dumps(hidden.structured_content))
        self.assertEqual(hidden.structured_content["characters_typed"], len(secret))

        default = await server.mcp.call_tool("type_text", {"text": secret})
        self.assertEqual(default.structured_content["typed"], secret)
        refreshed_ok = await server.mcp.call_tool("refresh_auth", {})
        self.assertEqual(refreshed_ok.structured_content, {"ok": True})
        self.login.assert_awaited_with(force=True)
        self.login.side_effect = RuntimeError(secret)
        refreshed = await server.mcp.call_tool("refresh_auth", {})
        self.assertFalse(refreshed.structured_content["ok"])
        self.assertNotIn(secret, json.dumps(refreshed.structured_content))
        self.assertTrue(refreshed.is_error)
        self.assertEqual(
            refreshed.structured_content["failure"]["category"], "internal"
        )

    async def test_type_text_timeout_is_an_mcp_error_with_uncertain_status(
        self,
    ) -> None:
        self.type_text_call.side_effect = InputOutcomeUnknown(category="timeout")
        result = await server.mcp.call_tool("type_text", {"text": "long command"})

        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["status"], "uncertain")
        self.assertEqual(result.structured_content["failure"], {"category": "timeout"})
        self.assertIn(
            "inspect the target before retrying", result.structured_content["error"]
        )

    async def test_type_text_limit_rejects_before_dispatching_to_driver(self) -> None:
        secret = "oversized-sensitive-input"
        self.type_text_call.side_effect = GlkvmError(
            "input exceeds limit",
            category="validation",
        )
        result = await server.mcp.call_tool("type_text", {"text": secret * 400})

        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["status"], "rejected")
        self.assertFalse(result.structured_content["typing_started"])
        self.assertEqual(
            result.structured_content["failure"], {"category": "validation"}
        )
        self.assertNotIn(secret, json.dumps(result.structured_content))


if __name__ == "__main__":
    unittest.main()
