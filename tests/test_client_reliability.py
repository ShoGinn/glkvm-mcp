from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, call

import httpx

from glkvm_mcp.kvmd import GlkvmClient, GlkvmError, InputOutcomeUnknown


class ClientReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.client = GlkvmClient()
        self.client.login = AsyncMock(return_value="opaque-token")
        self.requests: list[httpx.Request] = []

    async def asyncTearDown(self) -> None:
        if self.client._http is not None:
            await self.client._http.aclose()

    def use_transport(self, handler) -> None:
        self.client._http = httpx.AsyncClient(
            base_url="https://kvm.invalid",
            transport=httpx.MockTransport(handler),
        )

    async def test_auth_rejection_does_not_replay_posted_input(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(401, text="private response body", request=request)

        self.use_transport(handler)
        with self.assertRaises(GlkvmError) as caught:
            await self.client.request(
                "POST",
                "/api/hid/print",
                raw_content=b"sensitive target text",
            )

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(caught.exception.category, "authentication")
        self.assertEqual(caught.exception.status_code, 401)
        self.assertNotIn("private response body", str(caught.exception))
        self.assertNotIn("sensitive target text", str(caught.exception))

    async def test_stale_auth_retry_is_limited_to_get(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if len(self.requests) == 1:
                return httpx.Response(401, request=request)
            return httpx.Response(200, json={"ok": True}, request=request)

        self.use_transport(handler)
        response = await self.client.request("GET", "/api/hid")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(
            self.client.login.await_args_list, [call(), call(force=True), call()]
        )

    async def test_type_text_timeout_is_uncertain_and_is_not_retried(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            raise httpx.ReadTimeout("sensitive transport details", request=request)

        self.use_transport(handler)
        with self.assertRaises(InputOutcomeUnknown) as caught:
            await self.client.type_text("sensitive target text", delay=0)

        self.assertEqual(len(self.requests), 1)
        self.assertEqual(caught.exception.category, "timeout")
        self.assertNotIn("sensitive target text", str(caught.exception))

    async def test_type_text_rejects_oversize_or_unbounded_work_before_login(
        self,
    ) -> None:
        invalid_requests = [
            ("x" * 9_001, 0.01),
            ("short", 0.101),
            ("x" * 9_000, 0.02),  # estimated serial work exceeds the 5-minute cap
        ]
        for text, delay in invalid_requests:
            with self.subTest(length=len(text), delay=delay):
                with self.assertRaises(GlkvmError) as caught:
                    await self.client.type_text(text, delay=delay)
                self.assertEqual(caught.exception.category, "validation")

        self.client.login.assert_not_awaited()
        self.assertEqual(self.requests, [])

    async def test_invalid_unicode_in_later_chunk_is_rejected_before_login(
        self,
    ) -> None:
        text = "a" * 900 + "\ud800"

        with self.assertRaises(GlkvmError) as caught:
            await self.client.type_text(text, delay=0.01)

        self.assertEqual(caught.exception.category, "validation")
        self.assertNotIn("\ud800", str(caught.exception))
        self.client.login.assert_not_awaited()
        self.assertEqual(self.requests, [])

    async def test_mouse_click_requires_both_coordinates_before_sending_input(
        self,
    ) -> None:
        with self.assertRaises(GlkvmError) as caught:
            await self.client.mouse_click(x=12, y=None)

        self.assertEqual(caught.exception.category, "validation")
        self.client.login.assert_not_awaited()
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
