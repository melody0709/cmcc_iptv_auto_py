import asyncio
import importlib.util
import logging
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest import mock

import aiohttp
from aiohttp import web


CHECKER_DIR = Path(__file__).resolve().parent
os.environ["IPTV_CHECKER_DISABLE_FILE_LOG"] = "1"
CHECKER_MODULE_PATH = CHECKER_DIR / "iptv_checker_v3.py"
CHECKER_SPEC = importlib.util.spec_from_file_location(
    "iptv_checker_v3_module", CHECKER_MODULE_PATH
)
CHECKER_MODULE = importlib.util.module_from_spec(CHECKER_SPEC)
CHECKER_SPEC.loader.exec_module(CHECKER_MODULE)
IPTVCheckerFinal = CHECKER_MODULE.IPTVCheckerFinal


def make_ts_payload(packet_count=4):
    packet = bytearray(188)
    packet[0] = 0x47
    return bytes(packet) * packet_count


class NoDiskChecker(IPTVCheckerFinal):
    def load_cache(self):
        return {}

    def save_cache(self):
        pass

    def write_failed_log(self, failed_channels):
        pass


class ProbeFailureChecker(NoDiskChecker):
    async def probe_video_info(self, probe_url, *args, **kwargs):
        if kwargs.get("return_status"):
            return None, "timeout", "simulated timeout"
        return None


class ProbeSuccessChecker(NoDiskChecker):
    def __init__(self, *args, **kwargs):
        self.probe_calls = 0
        super().__init__(*args, **kwargs)

    async def probe_video_info(self, probe_url, *args, **kwargs):
        self.probe_calls += 1
        info = {
            "resolution": "1080P",
            "codec": "H264",
            "last_probed": "2026-07-12 12:00:00",
        }
        if kwargs.get("return_status"):
            return info, "success", ""
        return info


class ProbeErrorChecker(NoDiskChecker):
    async def probe_video_info(self, probe_url, *args, **kwargs):
        if kwargs.get("return_status"):
            return None, "error", "simulated invalid media"
        return None


class ProbeNoVideoChecker(NoDiskChecker):
    async def probe_video_info(self, probe_url, *args, **kwargs):
        if kwargs.get("return_status"):
            return None, "no_video", "simulated audio-only stream"
        return None


class IPTVCheckerRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.responders = {}

        async def dispatch(request):
            responder = self.responders.get(request.path)
            if responder is None:
                return web.Response(status=404)
            result = responder(request)
            if asyncio.iscoroutine(result):
                result = await result
            return result

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", dispatch)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def check_once(self, checker, path):
        channel = {
            "name": "test",
            "url": self.base_url + path,
            "group": "test",
            "needs_check": True,
        }
        async with aiohttp.ClientSession() as session:
            return await checker.check_stream_alive(
                session, channel, asyncio.Semaphore(1)
            )

    async def test_unit_tests_disable_production_file_logging(self):
        self.assertFalse(
            any(
                isinstance(handler, logging.FileHandler)
                for handler in CHECKER_MODULE.logger.handlers
            )
        )

    async def test_ts_sync_hits_must_share_one_packet_alignment(self):
        payload = bytearray(188 * 4)
        payload[0] = payload[188] = 0x47
        payload[1] = payload[189] = 0x47
        checker = NoDiskChecker(timeout=2, workers=1)

        self.assertFalse(checker._looks_like_ts_payload(bytes(payload)))

    async def test_cache_prefers_repository_config_directory(self):
        checker = NoDiskChecker(timeout=2, workers=1)

        self.assertEqual(
            Path(checker.cache_file).resolve(),
            (CHECKER_DIR.parent / "config" / "probe_cache.json").resolve(),
        )

    async def test_hls_tries_newest_segment_instead_of_expired_oldest(self):
        playlist = (
            "#EXTM3U\n"
            "#EXT-X-TARGETDURATION:5\n"
            "#EXT-X-MEDIA-SEQUENCE:10\n"
            "#EXTINF:5,\nold.ts\n"
            "#EXTINF:5,\nnew.ts\n"
        ).encode()
        self.responders["/live.m3u8"] = lambda request: web.Response(
            body=playlist, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/old.ts"] = lambda request: web.Response(status=404)
        self.responders["/new.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/live.m3u8"
        )

        self.assertTrue(alive, msg)
        self.assertTrue(channel["probe_url"].endswith("/new.ts"))

    async def test_socket_fallback_also_validates_hls_segment_body(self):
        playlist = b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:20\n#EXTINF:5,\nsocket.ts\n"
        self.responders["/socket.m3u8"] = lambda request: web.Response(
            body=playlist, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/socket.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )
        checker = NoDiskChecker(timeout=2, workers=1)
        channel = {
            "name": "socket",
            "url": self.base_url + "/socket.m3u8",
            "group": "test",
            "needs_check": True,
        }

        alive, msg = await checker._socket_http_probe(channel)

        self.assertTrue(alive, msg)
        self.assertTrue(channel["probe_url"].endswith("/socket.ts"))

    async def test_hls_rejects_http_200_html_segment(self):
        playlist = b"#EXTM3U\n#EXTINF:5,\nerror.ts\n"
        self.responders["/html.m3u8"] = lambda request: web.Response(
            body=playlist, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/error.ts"] = lambda request: web.Response(
            text="<html><body>access denied</body></html>" * 10,
            content_type="text/html",
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/html.m3u8"
        )

        self.assertFalse(alive)
        self.assertIn("切片", msg)

    async def test_master_playlist_recurses_and_prefers_highest_bandwidth(self):
        master = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=640x360\n"
            "low.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080\n"
            "high.m3u8\n"
        ).encode()
        high = b"#EXTM3U\n#EXT-X-TARGETDURATION:5\n#EXTINF:5,\nhigh.ts\n"
        self.responders["/master.m3u8"] = lambda request: web.Response(
            body=master, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/high.m3u8"] = lambda request: web.Response(
            body=high, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/high.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/master.m3u8"
        )

        self.assertTrue(alive, msg)
        self.assertEqual(channel["text_res"], "1080P")
        self.assertTrue(channel["probe_url"].endswith("/high.ts"))

    async def test_master_fallback_label_matches_working_variant(self):
        master = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=640x360\n"
            "low.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080\n"
            "high.m3u8\n"
        ).encode()
        self.responders["/fallback.m3u8"] = lambda request: web.Response(
            body=master, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/high.m3u8"] = lambda request: web.Response(status=404)
        self.responders["/low.m3u8"] = lambda request: web.Response(
            body=b"#EXTM3U\n#EXTINF:5,\nlow.ts\n",
            content_type="application/vnd.apple.mpegurl",
        )
        self.responders["/low.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/fallback.m3u8"
        )

        self.assertTrue(alive, msg)
        self.assertEqual(channel["text_res"], "标清")
        self.assertTrue(channel["probe_url"].endswith("/low.ts"))

    async def test_master_sampling_includes_lowest_variant(self):
        master = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=3840x2160\n4k.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080\n1080.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n720.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=640x360\nlow.m3u8\n"
        ).encode()
        self.responders["/many.m3u8"] = lambda request: web.Response(
            body=master, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/low.m3u8"] = lambda request: web.Response(
            body=b"#EXTM3U\n#EXTINF:5,\nlow.ts\n",
            content_type="application/vnd.apple.mpegurl",
        )
        self.responders["/low.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/many.m3u8"
        )

        self.assertTrue(alive, msg)
        self.assertEqual(channel["text_res"], "标清")

    async def test_stale_header_is_hint_when_latest_segment_is_valid(self):
        now = datetime.now(timezone.utc)
        headers = {
            "Date": format_datetime(now, usegmt=True),
            "Last-Modified": format_datetime(now - timedelta(minutes=10), usegmt=True),
        }
        playlist = (
            b"#EXTM3U\n#EXT-X-TARGETDURATION:5\n"
            b"#EXT-X-MEDIA-SEQUENCE:10\n#EXTINF:5,\nstale.ts\n"
        )
        self.responders["/stale.m3u8"] = lambda request: web.Response(
            body=playlist,
            content_type="application/vnd.apple.mpegurl",
            headers=headers,
        )
        self.responders["/stale.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/stale.m3u8"
        )

        self.assertTrue(alive, msg)
        self.assertTrue(channel.get("_playlist_stale_hint"))

    async def test_raw_stream_rejects_long_json_error(self):
        self.responders["/fake.ts"] = lambda request: web.json_response(
            {"error": "token expired", "padding": "x" * 300}
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/fake.ts"
        )

        self.assertFalse(alive)
        self.assertIn("文本", msg)

    async def test_media_magic_wins_over_wrong_text_content_type(self):
        self.responders["/mislabelled.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="text/plain"
        )

        alive, channel, msg = await self.check_once(
            NoDiskChecker(timeout=2, workers=1), "/mislabelled.ts"
        )

        self.assertTrue(alive, msg)

    async def test_unrecognized_binary_is_removed_when_ffprobe_rejects_it(self):
        self.responders["/garbage.bin"] = lambda request: web.Response(
            body=b"\x01\x02\x03\x04" * 100,
            content_type="application/octet-stream",
        )
        checker = ProbeErrorChecker(timeout=2, workers=1, enable_probe=True)
        channel = {
            "name": "garbage",
            "url": self.base_url + "/garbage.bin",
            "group": "test",
            "needs_check": True,
            "is_alive": False,
        }

        processed = await checker.process_channel_list([channel])

        self.assertFalse(processed[0]["is_alive"])
        self.assertIn("无可播放视频流", processed[0]["msg"])

    async def test_media_mime_without_magic_is_still_weak_evidence(self):
        self.responders["/fake-mime.ts"] = lambda request: web.Response(
            body=b"\x00" * 400,
            content_type="video/mp2t",
        )
        checker = ProbeErrorChecker(timeout=2, workers=1, enable_probe=True)
        channel = {
            "name": "fake-mime",
            "url": self.base_url + "/fake-mime.ts",
            "group": "test",
            "needs_check": True,
            "is_alive": False,
        }

        processed = await checker.process_channel_list([channel])

        self.assertFalse(processed[0]["is_alive"])

    async def test_no_video_cannot_be_overridden_by_text_resolution(self):
        master = (
            b"#EXTM3U\n"
            b"#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080\n"
            b"video.m3u8\n"
        )
        self.responders["/no-video-master.m3u8"] = lambda request: web.Response(
            body=master, content_type="application/vnd.apple.mpegurl"
        )
        self.responders["/video.m3u8"] = lambda request: web.Response(
            body=b"#EXTM3U\n#EXTINF:5,\nvideo.ts\n",
            content_type="application/vnd.apple.mpegurl",
        )
        self.responders["/video.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )
        checker = ProbeNoVideoChecker(timeout=2, workers=1, enable_probe=True)
        channel = {
            "name": "audio-only",
            "url": self.base_url + "/no-video-master.m3u8",
            "group": "test",
            "needs_check": True,
            "is_alive": False,
        }

        processed = await checker.process_channel_list([channel])

        self.assertEqual(processed[0].get("text_res"), "1080P")
        self.assertFalse(processed[0]["is_alive"])
        self.assertNotIn("probe_info", processed[0])

    async def test_inconclusive_ffprobe_does_not_override_strong_liveness(self):
        self.responders["/raw.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )
        checker = ProbeFailureChecker(
            timeout=2, workers=1, enable_probe=True, cache_expire_hours=24
        )
        channel = {
            "name": "playable",
            "url": self.base_url + "/raw.ts",
            "group": "test",
            "needs_check": True,
            "is_alive": False,
        }

        processed = await checker.process_channel_list([channel])

        self.assertTrue(processed[0]["is_alive"])

    async def test_legacy_unknown_cache_is_reprobed_not_used_as_death_verdict(self):
        self.responders["/cached.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )
        checker = ProbeSuccessChecker(
            timeout=2, workers=1, enable_probe=True, cache_expire_hours=24
        )
        url = self.base_url + "/cached.ts"
        now_utc8 = datetime.now(timezone(timedelta(hours=8))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        checker.cache_data[url] = {
            "resolution": "Unknown",
            "codec": "Unknown",
            "last_probed": now_utc8,
        }
        channel = {
            "name": "cached",
            "url": url,
            "group": "test",
            "needs_check": True,
            "is_alive": False,
        }

        processed = await checker.process_channel_list([channel])

        self.assertTrue(processed[0]["is_alive"])
        self.assertEqual(processed[0]["probe_info"], "[1080P][H264]")
        self.assertEqual(checker.probe_calls, 1)

    async def test_missing_ffprobe_keeps_step1_result(self):
        self.responders["/no-ffprobe.ts"] = lambda request: web.Response(
            body=make_ts_payload(), content_type="video/mp2t"
        )
        checker = NoDiskChecker(timeout=2, workers=1, enable_probe=True)
        checker.ffprobe_path = None
        url = self.base_url + "/no-ffprobe.ts"
        checker.cache_data[url] = {
            "resolution": "1080P",
            "codec": "H264",
            "last_probed": datetime.now(timezone(timedelta(hours=8))).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
        channel = {
            "name": "no-ffprobe",
            "url": url,
            "group": "test",
            "needs_check": True,
            "is_alive": False,
        }

        processed = await checker.process_channel_list([channel])

        self.assertTrue(processed[0]["is_alive"])
        self.assertEqual(processed[0]["probe_info"], "[1080P][H264]")

    async def test_ffprobe_timeout_terminates_child_process(self):
        class HangingProcess:
            def __init__(self):
                self.returncode = None
                self.killed = False

            async def communicate(self):
                await asyncio.sleep(60)

            def kill(self):
                self.killed = True
                self.returncode = -9

            async def wait(self):
                return self.returncode

        checker = NoDiskChecker(timeout=2, workers=1, enable_probe=True)
        checker.ffprobe_path = "ffprobe"
        checker.probe_timeout = 0.01
        process = HangingProcess()

        with mock.patch.object(
            CHECKER_MODULE.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(return_value=process),
        ):
            info, status, detail = await checker.probe_video_info(
                "http://example.test/live.m3u8", return_status=True
            )

        self.assertIsNone(info)
        self.assertEqual(status, "timeout")
        self.assertTrue(process.killed)

    async def test_unsupported_windows_loop_is_inconclusive_not_dead(self):
        checker = NoDiskChecker(timeout=2, workers=1, enable_probe=True)
        checker.ffprobe_path = "ffprobe"

        with mock.patch.object(
            CHECKER_MODULE.asyncio,
            "create_subprocess_exec",
            new=mock.AsyncMock(side_effect=NotImplementedError),
        ):
            info, status, detail = await checker.probe_video_info(
                "http://example.test/live.m3u8", return_status=True
            )

        self.assertIsNone(info)
        self.assertEqual(status, "unavailable")
        self.assertIn("Proactor", detail)


if __name__ == "__main__":
    unittest.main()
