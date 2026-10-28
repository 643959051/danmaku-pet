from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from danmaku_pet.cloud.config import CloudConfig
from danmaku_pet.cloud.server import create_app
from danmaku_pet.shared.protocol import Envelope
from tests.support import FakeEngine


def make_client(config: CloudConfig, *, engine=None) -> TestClient:
    config.mock_auto_ack = True
    config.mock_ack_delay_s = 0.01
    app = create_app(config, engine=engine or FakeEngine())
    return TestClient(app)


def admin_headers(config: CloudConfig) -> dict[str, str]:
    return {"Authorization": f"Bearer {config.admin_token}"}


def wait_for(client: TestClient, headers: dict[str, str], room: str, predicate, timeout: float = 3.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get("/api/status", headers=headers).json()
        rooms = last["orchestrator"]["rooms"]
        current = next((item for item in rooms if item["room_id"] == room), None)
        if current is not None and predicate(current):
            return last, current
        time.sleep(0.02)
    raise AssertionError(f"room {room} condition not met; last={last}")


def test_health_is_public_and_has_no_secrets(cloud_config: CloudConfig) -> None:
    with make_client(cloud_config) as client:
        health = client.get("/api/health").json()
        assert health["ok"] is True
        assert health["protocol_version"] == 1
        assert health["environment"] == "dev"
        assert cloud_config.admin_token not in str(health)


def test_v1_ping_is_public_and_returns_200(cloud_config: CloudConfig) -> None:
    with make_client(cloud_config) as client:
        response = client.get("/v1/ping")
        assert response.status_code == 200
        assert response.json()["ok"] is True


def test_admin_status_requires_a_token(cloud_config: CloudConfig) -> None:
    with make_client(cloud_config) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
        response = client.get("/api/status", headers=admin_headers(cloud_config))
        assert response.status_code == 200
        body = response.text
        assert cloud_config.admin_token not in body
        assert cloud_config.client_token not in body
        if cloud_config.deepseek_api_key:
            assert cloud_config.deepseek_api_key not in body


def test_manual_comment_waits_in_pending_then_reply_dispatches(cloud_config: CloudConfig) -> None:
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        sent = client.post(
            "/api/rooms/room-a/events/manual",
            headers=headers,
            json={"text": "你好呀", "user": "小满"},
        )
        assert sent.status_code == 200
        _, room = wait_for(client, headers, "room-a", lambda item: len(item["pending"]) == 1)
        assert room["stats"]["completed"] == 0

        reply = client.post("/api/rooms/room-a/reply", headers=headers, json={})
        assert reply.status_code == 200, reply.text
        _, room = wait_for(client, headers, "room-a", lambda item: item["stats"]["completed"] >= 1)
        assert room["pending"] == []
        assert room["recent"][0]["status"] == "completed"


def test_gift_is_dispatched_even_when_auto_reply_is_off(cloud_config: CloudConfig) -> None:
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        gift = client.post(
            "/api/rooms/room-a/events/gift",
            headers=headers,
            json={"gift_name": "嘉年华", "gift_id": "g-1", "gift_count": 1, "user": "阿星"},
        )
        assert gift.status_code == 200, gift.text
        _, room = wait_for(
            client,
            headers,
            "room-a",
            lambda item: item["stats"]["completed"] >= 1,
        )
        gift_entries = [entry for entry in room["recent"] if entry["event"]["source"] == "gift_sim"]
        assert gift_entries and gift_entries[0]["plan"]["priority"] == "high"


def test_test_endpoints_are_hidden_in_production_mode(cloud_config: CloudConfig) -> None:
    cloud_config.enable_test_endpoints = False
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        assert client.post(
            "/api/rooms/room-a/events/manual", headers=headers, json={"text": "hi"}
        ).status_code == 404
        assert client.post(
            "/api/rooms/room-a/events/gift", headers=headers, json={"gift_name": "玫瑰"}
        ).status_code == 404
        assert client.post(
            "/api/simulator/start", headers=headers, json={"interval_seconds": 2}
        ).status_code == 404
        assert client.post(
            "/api/rooms/room-a/context/clear", headers=headers, json={}
        ).status_code == 404


def test_persona_update_is_persisted(cloud_config: CloudConfig) -> None:
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        response = client.post(
            "/api/settings/persona",
            headers=headers,
            json={
                "name": "小盐",
                "identity": "盐系主播",
                "tone": "冷静",
                "catchphrase": "就这？",
                "reply_length": "medium",
                "emoji": False,
                "rules": "不要用烂梗",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["persona"]["name"] == "小盐"
        stored = asyncio.run(client.app.state.store.load_persona())
        assert stored is not None and stored["name"] == "小盐"


def test_simulator_start_and_stop(cloud_config: CloudConfig) -> None:
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        started = client.post(
            "/api/simulator/start",
            headers=headers,
            json={"interval_seconds": 0.8, "scenario": "chat", "room_id": "room-sim"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["simulator"]["running"] is True
        stopped = client.post("/api/simulator/stop", headers=headers, json={})
        assert stopped.status_code == 200
        assert stopped.json()["simulator"]["running"] is False


def test_auto_reply_and_interrupt_endpoints(cloud_config: CloudConfig) -> None:
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        client.post(
            "/api/rooms/room-a/events/manual", headers=headers, json={"text": "hi"}
        )
        wait_for(client, headers, "room-a", lambda item: True)
        response = client.post(
            "/api/settings/auto-reply",
            headers=headers,
            json={"enabled": True, "room_id": "room-a"},
        )
        assert response.status_code == 200
        _, room = wait_for(client, headers, "room-a", lambda item: item["auto_reply"] is True)
        interrupted = client.post("/api/rooms/room-a/interrupt", headers=headers, json={})
        assert interrupted.status_code == 200
        paused = client.post(
            "/api/rooms/room-a/pause", headers=headers, json={"paused": True}
        )
        assert paused.status_code == 200
        _, room = wait_for(client, headers, "room-a", lambda item: item["paused"] is True)


def test_no_endpoint_can_write_a_model_key(cloud_config: CloudConfig) -> None:
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        response = client.post(
            "/api/settings/llm",
            headers=headers,
            json={"api_key": "sk-should-never-be-accepted"},
        )
        assert response.status_code == 404


def test_douyin_receiver_verifies_and_deduplicates(cloud_config: CloudConfig) -> None:
    cloud_config.douyin.verify_mode = "header"
    cloud_config.douyin.verify_token = "platform-token"
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        payload = {
            "room_id": "room-douyin",
            "message_id": "m-1",
            "user": {"nickname": "小满", "id": "u-1"},
            "content": "官方评论",
        }
        unauthorized = client.post("/api/douyin/events/comment", json=payload)
        assert unauthorized.status_code == 401

        accepted = client.post(
            "/api/douyin/events/comment",
            json=payload,
            headers={"x-douyin-token": "platform-token"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["ok"] is True

        duplicate = client.post(
            "/api/douyin/events/comment",
            json=payload,
            headers={"x-douyin-token": "platform-token"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True

        status = client.get("/api/status", headers=headers).json()
        assert status["receiver"]["counts"]["accepted"] == 1
        assert status["receiver"]["counts"]["duplicate"] == 1
        assert any(room["room_id"] == "room-douyin" for room in status["orchestrator"]["rooms"])


def test_douyin_receiver_rejects_a_malformed_payload(cloud_config: CloudConfig) -> None:
    cloud_config.douyin.verify_mode = "header"
    cloud_config.douyin.verify_token = "platform-token"
    with make_client(cloud_config) as client:
        response = client.post(
            "/api/douyin/events/comment",
            json={"room_id": "r", "user": {"nickname": "A"}},
            headers={"x-douyin-token": "platform-token"},
        )
        assert response.status_code == 422


def test_douyin_tasks_endpoint_reports_unconfigured_platform(cloud_config: CloudConfig) -> None:
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        client.post("/api/rooms/room-a/events/manual", headers=headers, json={"text": "hi"})
        wait_for(client, headers, "room-a", lambda item: True)
        response = client.post(
            "/api/douyin/tasks/start",
            headers=headers,
            json={"room_id": "room-a", "kind": "comment"},
        )
        assert response.status_code == 409


def test_admin_websocket_streams_a_snapshot(cloud_config: CloudConfig) -> None:
    with make_client(cloud_config) as client:
        with client.websocket_connect(f"/ws?token={cloud_config.admin_token}") as websocket:
            message = websocket.receive_json()
            assert message["type"] == "snapshot"
            assert message["data"]["app"]["protocol_version"] == 1
            websocket.send_text("ping")
            pong = websocket.receive_json()
            assert pong["type"] == "pong"


def test_client_websocket_end_to_end(cloud_config: CloudConfig) -> None:
    """WSS 备用链路：握手 -> 下发 command -> 回执 result。"""

    cloud_config.gateway_mode = "wss"
    headers = admin_headers(cloud_config)
    with make_client(cloud_config) as client:
        hello = Envelope(
            kind="hello",
            payload={
                "token": cloud_config.client_token,
                "client_id": "client-1",
                "room_id": "room-a",
            },
        ).to_dict()
        with client.websocket_connect("/ws/client") as websocket:
            websocket.send_json(hello)
            welcome = websocket.receive_json()
            assert welcome["kind"] == "welcome"
            assert welcome["payload"]["room_id"] == "room-a"

            gift = client.post(
                "/api/rooms/room-a/events/gift",
                headers=headers,
                json={"gift_name": "火箭", "user": "阿星"},
            )
            assert gift.status_code == 200, gift.text

            command_message = websocket.receive_json()
            while command_message["kind"] != "command":
                command_message = websocket.receive_json()
            command = command_message["payload"]
            assert command["room_id"] == "room-a"
            assert command["plan"]["motion"] == "jump"

            websocket.send_json(
                Envelope(
                    kind="result",
                    payload={
                        "command_id": command["command_id"],
                        "room_id": "room-a",
                        "status": "completed",
                        "speech_ms": 1234,
                        "client_id": "client-1",
                    },
                ).to_dict()
            )
            _, room = wait_for(
                client,
                headers,
                "room-a",
                lambda item: item["stats"]["completed"] >= 1,
            )
            assert room["stats"]["completed"] == 1
            assert room["clients"][0]["last_result_status"] == "completed"


def test_client_websocket_rejects_a_bad_token(cloud_config: CloudConfig) -> None:
    with make_client(cloud_config) as client:
        hello = Envelope(
            kind="hello",
            payload={"token": "wrong", "client_id": "c", "room_id": "room-a"},
        ).to_dict()
        with client.websocket_connect("/ws/client") as websocket:
            websocket.send_json(hello)
            try:
                websocket.receive_json()
            except Exception:
                pass
            else:  # pragma: no cover - the server must not welcome this client
                raise AssertionError("bad token must not receive a welcome frame")
