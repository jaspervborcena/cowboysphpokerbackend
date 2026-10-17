from __future__ import annotations

import asyncio
import json

from logic.pubsub_routing import encode_worker_command, encode_worker_reply
from logic.pubsub_transport import PubSubTransport


class FakePublishFuture:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id

    def result(self, timeout: float | None = None) -> str:
        return self.message_id


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes, dict[str, str]]] = []
        self.topics: list[str] = []

    def topic_path(self, project_id: str, topic: str) -> str:
        return f"projects/{project_id}/topics/{topic}"

    def create_topic(self, *, request: dict[str, str]) -> None:
        self.topics.append(request["name"])

    def publish(self, topic: str, data: bytes, **attributes: str) -> FakePublishFuture:
        self.published.append((topic, data, attributes))
        return FakePublishFuture(str(len(self.published)))

    def close(self) -> None:
        return None


class FakeSubscriberFuture:
    def cancel(self) -> None:
        return None


class FakeSubscriber:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, str]] = []
        self.deletions: list[str] = []

    def subscription_path(self, project_id: str, subscription: str) -> str:
        return f"projects/{project_id}/subscriptions/{subscription}"

    def create_subscription(
        self,
        *,
        name: str,
        topic: str,
        enable_message_ordering: bool,
        ack_deadline_seconds: int,
    ) -> None:
        assert enable_message_ordering
        assert ack_deadline_seconds == 60
        self.subscriptions.append((name, topic))

    def subscribe(self, subscription: str, callback):
        return FakeSubscriberFuture()

    def delete_subscription(self, *, subscription: str) -> None:
        self.deletions.append(subscription)

    def close(self) -> None:
        return None


class FakeMessage:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.acked = False
        self.nacked = False

    def ack(self) -> None:
        self.acked = True

    def nack(self) -> None:
        self.nacked = True


def make_transport(*, gateway_id: str | None = None, worker_id: str | None = None):
    publisher = FakePublisher()
    subscriber = FakeSubscriber()
    transport = PubSubTransport(
        "test-project",
        prefix="poker-test",
        gateway_id=gateway_id,
        worker_id=worker_id,
        publisher=publisher,
        subscriber=subscriber,
    )
    return transport, publisher, subscriber


def test_gateway_subscribes_to_private_replies_and_events() -> None:
    async def verify() -> None:
        transport, _, subscriber = make_transport(gateway_id="gateway-a")
        await transport.start_gateway(lambda event: None)
        assert len(subscriber.subscriptions) == 2
        assert "gateway-gateway-a-replies" in subscriber.subscriptions[0][0]
        assert "gateway-gateway-a-events" in subscriber.subscriptions[1][0]
        await transport.close()
        assert len(subscriber.deletions) == 2

    asyncio.run(verify())


def test_gateway_command_uses_table_ordering_key_and_correlates_reply() -> None:
    async def verify() -> None:
        transport, publisher, _ = make_transport(gateway_id="gateway-a")
        await transport.start_gateway(lambda event: None)
        request = asyncio.create_task(
            transport.request_worker(
                worker_id="worker-a",
                scope_type="club",
                scope_id="club-1",
                table_id="table-1",
                command={"event": "PLAYER_ACTION", "action_id": "action-1"},
            )
        )
        await asyncio.sleep(0)
        topic, command_bytes, attributes = publisher.published[0]
        command = json.loads(command_bytes)
        assert topic.endswith("worker-replies") is False
        assert attributes["ordering_key"] == "club:club-1:table-1"
        reply = FakeMessage(
            encode_worker_reply(
                gateway_id="gateway-a",
                request_id=command["request_id"],
                worker_id="worker-a",
                result={"accepted": True},
            )
        )
        transport._on_reply(reply)
        assert await request == {"accepted": True}
        assert reply.acked
        await transport.close()

    asyncio.run(verify())


def test_worker_consumes_command_and_publishes_correlated_reply() -> None:
    async def verify() -> None:
        transport, publisher, subscriber = make_transport(worker_id="worker-a")
        await transport.start_worker(lambda envelope: {"table_id": envelope["table_id"]})
        assert len(subscriber.subscriptions) == 1
        command = encode_worker_command(
            gateway_id="gateway-a",
            request_id="request-1",
            worker_id="worker-a",
            scope_type="union",
            scope_id="union-1",
            table_id="table-1",
            command={"event": "START_HAND"},
        )
        message = FakeMessage(command)
        transport._on_command(message, lambda envelope: {"accepted": True})
        assert message.acked
        reply = json.loads(publisher.published[0][1])
        assert reply["gateway_id"] == "gateway-a"
        assert reply["request_id"] == "request-1"
        assert reply["result"] == {"accepted": True}
        await transport.close()

    asyncio.run(verify())


def test_worker_publishes_gateway_events_with_ordering_key() -> None:
    transport, publisher, _ = make_transport(worker_id="worker-a")

    transport.publish_gateway_event(
        gateway_id="gateway-a",
        event={"scope": "club-1", "table_id": "table-1", "user_id": "player-1"},
        ordering_key="club-1:table-1:player-1",
    )

    _, encoded, attributes = publisher.published[0]
    envelope = json.loads(encoded)
    assert attributes["ordering_key"] == "club-1:table-1:player-1"
    assert envelope["gateway_id"] == "gateway-a"


def test_gateway_acks_snapshot_only_after_delivery_completes() -> None:
    async def verify() -> None:
        delivered: list[dict[str, object]] = []

        async def handle_event(event: dict[str, object]) -> None:
            delivered.append(event)

        transport, _, _ = make_transport(gateway_id="gateway-a")
        await transport.start_gateway(handle_event)
        message = FakeMessage(
            json.dumps(
                {
                    "schema_version": 1,
                    "gateway_id": "gateway-a",
                    "event": {"user_id": "player-1", "state": {"table_sequence": 5}},
                }
            ).encode()
        )
        await asyncio.to_thread(transport._on_event, message)
        assert message.acked
        assert delivered[0]["state"]["table_sequence"] == 5
        await transport.close()

    asyncio.run(verify())
