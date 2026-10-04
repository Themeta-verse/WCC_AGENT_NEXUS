"""NEXUS MessagingHub — cross-agent message store for Phase 4 autonomous workflows.

The MessagingHub provides a lightweight, SQLite-backed message channel that
allows agents executing workflow tasks to exchange structured messages:
  - task_started / task_completed / task_failed (lifecycle)
  - request_information / information_available (Q&A handoff)
  - needs_review (escalation to human-in-the-loop)
  - status_update (progress reporting)

Messages are scoped to a workflow and optionally to a task. They are
persisted durably so that a worker restart does not lose cross-agent
communication history. The truth boundary is preserved: all messages
are INFERRED / untrusted until independently verified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import json
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


MESSAGE_TYPES = {
    "TASK_STARTED",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_RETRY",
    "TASK_RECOVERED",
    "REQUEST_INFORMATION",
    "INFORMATION_AVAILABLE",
    "NEEDS_REVIEW",
    "STATUS_UPDATE",
    "WORKFLOW_PAUSED",
    "WORKFLOW_RESUMED",
    # Phase 5 collaboration protocol
    "REQUEST",
    "RESPONSE",
    "QUESTION",
    "ANSWER",
    "HANDOFF",
    "REVIEW_REQUEST",
    "APPROVAL_REQUEST",
    "APPROVAL_GRANTED",
    "APPROVAL_REJECTED",
    "BLOCKED",
    "ESCALATION",
    "DYNAMIC_TASK_CREATED",
    "WORKFLOW_PAUSED_APPROVAL",
}


class MessagingHub:
    """Durable message store for agent communication within workflows.

    Wraps the NexusDatabase workflow_messages table, providing a typed
    interface for sending and retrieving messages between agents.
    """

    def __init__(self, database: Any):
        self.database = database

    def send(
        self,
        workflow_id: str,
        tenant_id: str,
        message_type: str,
        content: dict[str, Any],
        *,
        from_agent_id: str | None = None,
        to_agent_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a message through the hub.

        Args:
            workflow_id: The workflow this message belongs to
            tenant_id: Tenant scope
            message_type: One of MESSAGE_TYPES (or a custom string)
            content: Arbitrary JSON-serializable message payload
            from_agent_id: Sender agent (if applicable)
            to_agent_id: Recipient agent (if applicable)
            task_id: Associated task (if applicable)
            correlation_id: Correlation ID for grouping related messages

        Returns:
            The persisted message dict
        """
        if message_type not in MESSAGE_TYPES:
            content = {"message_type": message_type, **content}

        message_id = f"msg-{uuid.uuid4()}"
        return self.database.create_workflow_message(
            message_id=message_id,
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type=message_type,
            content=content,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def receive(
        self,
        workflow_id: str,
        tenant_id: str,
        *,
        task_id: str | None = None,
        message_type: str | None = None,
        limit: int = 100,
        mark_processed: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieve messages for a workflow (optionally filtered by task/type).

        If mark_processed is True, fetched messages are marked as processed
        so subsequent calls only see new messages.
        """
        if message_type:
            messages = self.database.list_workflow_messages(
                tenant_id, workflow_id, message_type=message_type, task_id=task_id, limit=limit
            )
        else:
            messages = self.database.list_workflow_messages(
                tenant_id, workflow_id, task_id=task_id, limit=limit
            )
        if mark_processed:
            for msg in messages:
                self.database.mark_message_processed(tenant_id, msg["message_id"])
        return messages

    def pending(
        self,
        workflow_id: str,
        tenant_id: str,
        *,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get unprocessed messages without marking them processed."""
        messages = self.database.list_unprocessed_messages(
            tenant_id, workflow_id, task_id=task_id, limit=limit
        )
        return messages

    def messages_for_agent(
        self,
        workflow_id: str,
        tenant_id: str,
        agent_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get unprocessed messages addressed to a specific agent."""
        messages = self.database.list_unprocessed_messages(
            tenant_id, workflow_id, limit=limit
        )
        return [
            m for m in messages
            if m.get("to_agent_id") == agent_id or m.get("to_agent_id") is None
        ]

    def broadcast(
        self,
        workflow_id: str,
        tenant_id: str,
        message_type: str,
        content: dict[str, Any],
        *,
        from_agent_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Broadcast a message to all agents (to_agent_id=None)."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type=message_type,
            content=content,
            from_agent_id=from_agent_id,
            correlation_id=correlation_id,
        )

    def request_information(
        self,
        workflow_id: str,
        tenant_id: str,
        query: str,
        *,
        from_agent_id: str,
        to_agent_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a request_information message from one agent to another."""
        correlation_id = f"info-{uuid.uuid4()}"
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="REQUEST_INFORMATION",
            content={"query": query, "correlation_id": correlation_id},
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def reply_information(
        self,
        workflow_id: str,
        tenant_id: str,
        answer: dict[str, Any],
        *,
        from_agent_id: str,
        to_agent_id: str,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Send an information_available reply message."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="INFORMATION_AVAILABLE",
            content=answer,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def needs_review(
        self,
        workflow_id: str,
        tenant_id: str,
        reason: str,
        *,
        from_agent_id: str,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a needs_review escalation message (human-in-the-loop)."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="NEEDS_REVIEW",
            content={
                "reason": reason,
                "details": details or {},
            },
            from_agent_id=from_agent_id,
            task_id=task_id,
        )

    def status_update(
        self,
        workflow_id: str,
        tenant_id: str,
        status: str,
        *,
        from_agent_id: str,
        task_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a status_update message for progress reporting."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="STATUS_UPDATE",
            content={"status": status, "data": data or {}},
            from_agent_id=from_agent_id,
            task_id=task_id,
        )

    def task_lifecycle(
        self,
        workflow_id: str,
        tenant_id: str,
        event: str,
        *,
        agent_id: str,
        task_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Emit a task lifecycle message (task_started, task_completed, task_failed, task_retry)."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type=event,
            content={
                "task_id": task_id,
                "agent_id": agent_id,
                "details": details or {},
            },
            from_agent_id=agent_id,
            task_id=task_id,
        )

    def recent(
        self,
        workflow_id: str,
        tenant_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get all recent messages for a workflow, newest first."""
        return self.database.list_workflow_messages(tenant_id, workflow_id, limit=limit)

    # ---- Phase 5 Collaboration Protocol -----------------------------------------

    def request(
        self,
        workflow_id: str,
        tenant_id: str,
        query: str,
        *,
        from_agent_id: str,
        to_agent_id: str,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a REQUEST message from one agent to another."""
        correlation_id = f"req-{uuid.uuid4().hex[:12]}"
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="REQUEST",
            content={
                "query": query,
                "context": context or {},
                "correlation_id": correlation_id,
            },
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def respond(
        self,
        workflow_id: str,
        tenant_id: str,
        response: dict[str, Any],
        *,
        from_agent_id: str,
        to_agent_id: str,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a RESPONSE message."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="RESPONSE",
            content=response,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def question(
        self,
        workflow_id: str,
        tenant_id: str,
        question: str,
        *,
        from_agent_id: str,
        to_agent_id: str | None = None,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a QUESTION to another agent or broadcast to all."""
        correlation_id = f"q-{uuid.uuid4().hex[:12]}"
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="QUESTION",
            content={
                "question": question,
                "context": context or {},
                "correlation_id": correlation_id,
            },
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def answer(
        self,
        workflow_id: str,
        tenant_id: str,
        answer_text: str,
        *,
        from_agent_id: str,
        to_agent_id: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
        confidence: str = "INFERRED",
    ) -> dict[str, Any]:
        """Send an ANSWER message."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="ANSWER",
            content={
                "answer": answer_text,
                "confidence": confidence,
                "correlation_id": correlation_id,
            },
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def handoff(
        self,
        workflow_id: str,
        tenant_id: str,
        task_id: str,
        *,
        from_agent_id: str,
        to_agent_id: str,
        payload: dict[str, Any] | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Send a HANDOFF message transferring a task between agents."""
        correlation_id = f"handoff-{task_id}"
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="HANDOFF",
            content={
                "task_id": task_id,
                "payload": payload or {},
                "notes": notes,
                "correlation_id": correlation_id,
            },
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    def review_request(
        self,
        workflow_id: str,
        tenant_id: str,
        reason: str,
        *,
        from_agent_id: str,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a REVIEW_REQUEST message for peer review of an artifact/decision."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="REVIEW_REQUEST",
            content={"reason": reason, "details": details or {}},
            from_agent_id=from_agent_id,
            task_id=task_id,
        )

    def approval_request(
        self,
        workflow_id: str,
        tenant_id: str,
        operation: str,
        *,
        from_agent_id: str,
        task_id: str | None = None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an APPROVAL_REQUEST message to pause workflow for human approval."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="APPROVAL_REQUEST",
            content={
                "operation": operation,
                "reason": reason,
                "payload": payload or {},
            },
            from_agent_id=from_agent_id,
            to_agent_id=None,
            task_id=task_id,
        )

    def blocked(
        self,
        workflow_id: str,
        tenant_id: str,
        reason: str,
        *,
        from_agent_id: str,
        task_id: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a BLOCKED message when a task cannot proceed."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="BLOCKED",
            content={"reason": reason, "details": details or {}},
            from_agent_id=from_agent_id,
            task_id=task_id,
        )

    def escalate(
        self,
        workflow_id: str,
        tenant_id: str,
        reason: str,
        *,
        from_agent_id: str,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an ESCALATION message to flag a problem for human attention."""
        return self.send(
            workflow_id=workflow_id,
            tenant_id=tenant_id,
            message_type="ESCALATION",
            content={"reason": reason, "details": details or {}},
            from_agent_id=from_agent_id,
            task_id=task_id,
        )
