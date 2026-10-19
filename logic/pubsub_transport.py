from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any
from uuid import uuid4

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import pubsub_v1

from logic.pubsub_routing import (
    PubSubMessageError,
    decode_worker_command,
    decode_worker_reply,
    encode_worker_command,
    encode_worker_reply,
)


WorkerHandler = Callable[[dict[str, Any]], dict[str, Any] | Awaitable[dict[str, Any]]]
EventHandler = Callable[[dict[str, Any]], None | Awaitable[None]]


class PubSubTransport:
    def __init__(
        self,
        project_id: str,
        *,
        prefix: str,
        gateway_id: str | None = None,
        worker_id: str | None = None,
        request_timeout_seconds: float = 15,
        publisher: Any | None = None,
        subscriber: Any | None = None,
    ) -> None:
        if not project_id or not prefix:
            raise ValueError("Pub/Sub project ID and resource prefix are required.")
        if bool(gateway_id) == bool(worker_id):
            raise ValueError("Configure exactly one of gateway_id or worker_id.")
        self.project_id = project_id
        self.prefix = prefix
        self.gateway_id = gateway_id
        self.worker_id = worker_id
        self.request_timeout_seconds = request_timeout_seconds
        self.publisher = publisher or pubsub_v1.PublisherClient(
            publisher_options=pubsub_v1.types.PublisherOptions(
                enable_message_ordering=True,
            )
        )
        self.subscriber = subscriber or pubsub_v1.SubscriberClient()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._subscriber_futures: list[Any] = []
        self._subscriptions: list[str] = []
        self._gateway_event_handler: EventHandler | None = None

    def _topic(self, suffix: str) -> str:
        return self.publisher.topic_path(self.project_id, f"{self.prefix}-{suffix}")

    def _ensure_topic(self, suffix: str) -> str:
        topic = self._topic(suffix)
        try:
            self.publisher.create_topic(request={"name": topic})
        except AlreadyExists:
            pass
        return topic

    def _subscription(self, name: str) -> str:
        return self.subscriber.subscription_path(self.project_id, name)

    def _ensure_subscription(self, name: str, topic: str) -> str:
        path = self._subscription(name)
        try:
            self.subscriber.create_subscription(
                name=path,
                topic=topic,
                enable_message_ordering=True,
                ack_deadline_seconds=60,
            )
        except AlreadyExists:
            pass
        self._subscriptions.append(path)
        return path

    async def start_gateway(self, event_handler: EventHandler) -> None:
        if self.gateway_id is None:
            raise RuntimeError("This Pub/Sub transport is not configured as a gateway.")
        self._loop = asyncio.get_running_loop()
        self._gateway_event_handler = event_handler
        self._ensure_topic("worker-replies")
        self._ensure_topic("table-events")
        reply_sub = self._ensure_subscription(
            f"{self.prefix}-gateway-{self.gateway_id}-replies",
            self._topic("worker-replies"),
        )
        event_sub = self._ensure_subscription(
            f"{self.prefix}-gateway-{self.gateway_id}-events",
            self._topic("table-events"),
        )
        self._subscriber_futures.extend(
            [
                self.subscriber.subscribe(reply_sub, callback=self._on_reply),
                self.subscriber.subscribe(event_sub, callback=self._on_event),
            ]
        )

    async def request_worker(
        self,
        *,
        worker_id: str,
        scope_type: str,
        scope_id: str,
        table_id: str,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        if self.gateway_id is None or self._loop is None:
            raise RuntimeError("Gateway transport has not been started.")
        request_id = str(uuid4())
        reply: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        self._pending[request_id] = reply
        data = encode_worker_command(
            gateway_id=self.gateway_id,
            request_id=request_id,
            worker_id=worker_id,
            scope_type=scope_type,
            scope_id=scope_id,
            table_id=table_id,
            command=command,
        )
        worker_digest = hashlib.sha256(worker_id.encode("utf-8")).hexdigest()[:32]
        topic = self._topic(f"worker-{worker_digest}-commands")
        ordering_key = f"{scope_type}:{scope_id}:{table_id}"
        try:
            publish_future = self.publisher.publish(topic, data, ordering_key=ordering_key)
            await asyncio.to_thread(publish_future.result, self.request_timeout_seconds)
            return await asyncio.wait_for(reply, timeout=self.request_timeout_seconds)
        except (asyncio.TimeoutError, FutureTimeoutError) as error:
            raise TimeoutError("The owning worker did not reply before the command deadline.") from error
        finally:
            self._pending.pop(request_id, None)
            if not reply.done():
                reply.cancel()

    async def start_worker(self, handler: WorkerHandler) -> None:
        if self.worker_id is None:
            raise RuntimeError("This Pub/Sub transport is not configured as a worker.")
        self._loop = asyncio.get_running_loop()
        worker_digest = hashlib.sha256(self.worker_id.encode("utf-8")).hexdigest()[:32]
        topic = self._ensure_topic(f"worker-{worker_digest}-commands")
        self._ensure_topic("worker-replies")
        self._ensure_topic("table-events")
        subscription = self._ensure_subscription(
            f"{self.prefix}-worker-{worker_digest}-commands",
            topic,
        )
        self._subscriber_futures.append(
            self.subscriber.subscribe(
                subscription,
                callback=lambda message: self._on_command(message, handler),
            )
        )

    def _on_reply(self, message: Any) -> None:
        try:
            envelope = decode_worker_reply(message.data, expected_gateway_id=self.gateway_id or "")
            request_id = envelope["request_id"]
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._resolve_reply, request_id, envelope["result"])
            message.ack()
        except PubSubMessageError:
            message.ack()

    def _resolve_reply(self, request_id: str, result: dict[str, Any]) -> None:
        future = self._pending.get(request_id)
        if future is not None and not future.done():
            future.set_result(result)

    def _on_event(self, message: Any) -> None:
        try:
            envelope = json.loads(message.data)
            if (
                not isinstance(envelope, dict)
                or envelope.get("schema_version") != 1
                or envelope.get("gateway_id") != self.gateway_id
                or not isinstance(envelope.get("event"), dict)
            ):
                message.ack()
                return
            event = envelope["event"]
            if self._loop is not None and self._gateway_event_handler is not None:
                delivery = asyncio.run_coroutine_threadsafe(
                    self._dispatch_event(self._gateway_event_handler, event),
                    self._loop,
                )
                delivery.result(self.request_timeout_seconds)
            message.ack()
        except (UnicodeDecodeError, json.JSONDecodeError, PubSubMessageError):
            message.ack()
        except Exception:
            message.nack()

    @staticmethod
    async def _dispatch_event(handler: EventHandler, event: dict[str, Any]) -> None:
        result = handler(event)
        if asyncio.iscoroutine(result):
            await result

    def publish_reply(
        self,
        *,
        gateway_id: str,
        request_id: str,
        result: dict[str, Any],
    ) -> None:
        if self.worker_id is None:
            raise RuntimeError("This Pub/Sub transport is not configured as a worker.")
        data = encode_worker_reply(
            gateway_id=gateway_id,
            request_id=request_id,
            worker_id=self.worker_id,
            result=result,
        )
        self.publisher.publish(self._topic("worker-replies"), data).result(
            self.request_timeout_seconds
        )

    def publish_gateway_event(
        self,
        *,
        gateway_id: str,
        event: dict[str, Any],
        ordering_key: str,
    ) -> None:
        if self.worker_id is None:
            raise RuntimeError("This Pub/Sub transport is not configured as a worker.")
        if not gateway_id or not ordering_key or not isinstance(event, dict):
            raise ValueError("Gateway ID, ordering key, and event payload are required.")
        data = json.dumps(
            {"schema_version": 1, "gateway_id": gateway_id, "event": event},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self.publisher.publish(
            self._topic("table-events"),
            data,
            ordering_key=ordering_key,
        ).result(
            self.request_timeout_seconds
        )

    def _on_command(self, message: Any, handler: WorkerHandler) -> None:
        try:
            envelope = decode_worker_command(
                message.data,
                expected_worker_id=self.worker_id or "",
            )
            result = handler(envelope)
            if asyncio.iscoroutine(result):
                if self._loop is None:
                    raise RuntimeError("Worker transport has not been started.")
                result = asyncio.run_coroutine_threadsafe(result, self._loop).result(
                    self.request_timeout_seconds
                )
            self.publish_reply(
                gateway_id=envelope["gateway_id"],
                request_id=envelope["request_id"],
                result=result,
            )
            message.ack()
        except PubSubMessageError:
            message.ack()
        except Exception:
            message.nack()

    async def close(self) -> None:
        for future in self._subscriber_futures:
            future.cancel()
        self._subscriber_futures.clear()
        if self.gateway_id is not None:
            for subscription in self._subscriptions:
                try:
                    self.subscriber.delete_subscription(subscription=subscription)
                except NotFound:
                    pass
        self._subscriptions.clear()
        self.publisher.close()
        self.subscriber.close()
