from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from agent.clients.prometheus_client import PrometheusClient, PrometheusError
from agent.config import config
from agent.core import run_turn
from agent.notifications.base import Notifier
from agent.rules import Rule

logger = logging.getLogger(__name__)

REMEDIATION_TOOL_NAMES = frozenset({"restart_deployment", "scale_deployment", "delete_pod"})

OPS_SYSTEM_PROMPT = (
    "You are an SRE automation agent responsible for remediating infrastructure "
    "incidents safely and autonomously. A monitoring rule has just breached its "
    "threshold; you will be given the rule's details, the current metric value, "
    "the configured remediation target, and recent incident history for this "
    "same rule.\n\n"
    "Use the available tools to investigate and remediate the issue:\n"
    "- query_prometheus: run any PromQL query to gather more context\n"
    "- get_deployment_status: check the replica status of the target deployment\n"
    "- restart_deployment / scale_deployment / delete_pod: take remediation action\n"
    "- send_notification: add extra context to the incident record\n\n"
    "Only act on the target you were given; never invent a namespace or "
    "deployment name. Prefer the least disruptive action likely to work (a "
    "rolling restart before scaling, scaling before deleting pods). If recent "
    "history shows the same action was already tried and did not resolve the "
    "issue, try something different or escalate via send_notification instead "
    "of blindly repeating it. Briefly explain your reasoning, then call the "
    "appropriate tool(s). If you don't believe any action is needed, say so "
    "and don't call a remediation tool.\n\n"
    "Never invent a metric value, tool result, or remediation outcome. Only "
    "state what a tool actually returned. If query_prometheus or "
    "get_deployment_status errors or returns no data, say so explicitly - do "
    "not guess a plausible-sounding value instead."
)


@dataclass
class RuleState:
    consecutive_breaches: int = 0
    last_action_at: float | None = None


@dataclass
class Incident:
    rule_name: str
    triggered_at: float
    value: float
    threshold: float
    actions_taken: list[dict]
    llm_summary: str
    verified_recovered: bool | None
    severity: str


class MonitoringAgent:
    def __init__(
        self,
        rules: list[Rule],
        prometheus: PrometheusClient,
        notifier: Notifier,
        provider_name: str = "ollama",
    ) -> None:
        self.rules = rules
        self._prometheus = prometheus
        self._notifier = notifier
        self._provider_name = provider_name
        self._state: dict[str, RuleState] = {rule.name: RuleState() for rule in rules}
        self.incidents: list[Incident] = []
        self.last_cycle_summary: dict[str, dict] = {}

    def run_cycle(self) -> None:
        summary: dict[str, dict] = {}
        remediations_this_cycle = 0

        for rule in self.rules:
            try:
                status, remediations_this_cycle = self._evaluate_rule(rule, remediations_this_cycle)
                summary[rule.name] = status
            except Exception:
                logger.exception("Unhandled error evaluating rule '%s'", rule.name)
                summary[rule.name] = {"status": "error"}

        self.last_cycle_summary = summary

    def _evaluate_rule(self, rule: Rule, remediations_this_cycle: int) -> tuple[dict, int]:
        state = self._state[rule.name]

        try:
            value = self._prometheus.latest_value(rule.query)
        except PrometheusError:
            logger.exception("Failed to query Prometheus for rule '%s'", rule.name)
            # A transient scrape/query failure neither counts as progress
            # toward a breach nor looks like recovery - just skip this cycle.
            return {"status": "query_failed"}, remediations_this_cycle

        if value is None:
            logger.warning("Rule '%s' query returned no data: %s", rule.name, rule.query)
            return {"status": "no_data"}, remediations_this_cycle

        if not rule.is_breached(value):
            state.consecutive_breaches = 0
            return {"status": "ok", "value": value}, remediations_this_cycle

        state.consecutive_breaches += 1
        logger.warning(
            "Rule '%s' breached (value=%s %s %s, consecutive=%d/%d)",
            rule.name, value, rule.comparator, rule.threshold,
            state.consecutive_breaches, rule.consecutive_breaches_required,
        )

        if state.consecutive_breaches < rule.consecutive_breaches_required:
            return {"status": "breached_pending_confirmation", "value": value}, remediations_this_cycle

        now = time.time()
        if state.last_action_at is not None and now - state.last_action_at < rule.cooldown_seconds:
            remaining = int(rule.cooldown_seconds - (now - state.last_action_at))
            logger.info("Rule '%s' is in cooldown for %ds more; skipping remediation", rule.name, remaining)
            return {"status": "cooldown", "value": value}, remediations_this_cycle

        if remediations_this_cycle >= config.max_remediations_per_cycle:
            logger.error(
                "Rule '%s' breached but the per-cycle remediation budget (%d) is exhausted; skipping",
                rule.name, config.max_remediations_per_cycle,
            )
            self._notifier.send(
                title=f"Remediation rate-limited: {rule.name}",
                message=(
                    f"Rule '{rule.name}' breached (value={value}) but "
                    f"{config.max_remediations_per_cycle} remediation(s) already ran this cycle. "
                    "Skipping to avoid a restart storm - investigate manually."
                ),
                severity="critical",
            )
            return {"status": "rate_limited", "value": value}, remediations_this_cycle

        # Stamp the cooldown clock the moment we decide to act, not after
        # verification completes (which can take up to VERIFY_TIMEOUT_SECONDS).
        state.last_action_at = now
        self._handle_breach(rule, value)
        state.consecutive_breaches = 0
        return {"status": "remediated", "value": value}, remediations_this_cycle + 1

    def _handle_breach(self, rule: Rule, value: float) -> None:
        self._notifier.send(
            title=f"Alert firing: {rule.name}",
            message=(
                f"{rule.description or rule.query}\n"
                f"Query: {rule.query}\n"
                f"Value: {value} (threshold: {rule.comparator} {rule.threshold})\n"
                f"Target: {self._format_target(rule)}\n"
                "Autonomous remediation is starting."
            ),
            severity="warning",
        )

        try:
            result = run_turn(
                [],
                self._build_incident_prompt(rule, value),
                provider_name=self._provider_name,
                system_prompt=OPS_SYSTEM_PROMPT,
            )
        except Exception as exc:
            logger.exception("LLM-driven remediation failed for rule '%s'", rule.name)
            self._notifier.send(
                title=f"Remediation FAILED: {rule.name}",
                message=(
                    "The automation model or a tool call raised an error before any "
                    f"action could be confirmed: {exc}"
                ),
                severity="critical",
            )
            self._append_incident(
                Incident(
                    rule_name=rule.name,
                    triggered_at=time.time(),
                    value=value,
                    threshold=rule.threshold,
                    actions_taken=[],
                    llm_summary=f"error: {exc}",
                    verified_recovered=None,
                    severity="critical",
                )
            )
            return

        actions_taken = [
            {"tool": inv.name, "arguments": inv.arguments, "result": inv.result}
            for inv in result.tool_invocations
            if inv.name in REMEDIATION_TOOL_NAMES
        ]

        verified: bool | None = None
        if actions_taken:
            verified = self._verify_recovery(rule)

        if actions_taken and verified:
            title, severity = f"Remediation resolved: {rule.name}", "info"
        elif actions_taken:
            title, severity = f"Remediation UNRESOLVED: {rule.name}", "critical"
        else:
            title, severity = f"No action taken: {rule.name}", "warning"

        self._notifier.send(
            title=title,
            message=(
                f"Actions taken: {actions_taken or 'none'}\n"
                f"Model reasoning: {result.text}\n"
                f"Verification: {'recovered' if verified else ('not verified' if verified is None else 'still breached')}"
            ),
            severity=severity,
        )

        self._append_incident(
            Incident(
                rule_name=rule.name,
                triggered_at=time.time(),
                value=value,
                threshold=rule.threshold,
                actions_taken=actions_taken,
                llm_summary=result.text,
                verified_recovered=verified,
                severity=severity,
            )
        )

    def _verify_recovery(self, rule: Rule) -> bool:
        deadline = time.monotonic() + config.verify_timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(config.verify_poll_interval_seconds)
            try:
                value = self._prometheus.latest_value(rule.query)
            except PrometheusError:
                logger.exception("Verification query failed for rule '%s'", rule.name)
                continue
            if value is not None and not rule.is_breached(value):
                logger.info("Rule '%s' recovered (value=%s)", rule.name, value)
                return True
        logger.error("Rule '%s' did not recover within %ds", rule.name, config.verify_timeout_seconds)
        return False

    def _build_incident_prompt(self, rule: Rule, value: float) -> str:
        history_lines = [
            f"- value={inc.value}, actions={[a['tool'] for a in inc.actions_taken] or 'none'}, "
            f"recovered={inc.verified_recovered}"
            for inc in self.incidents[-50:]
            if inc.rule_name == rule.name
        ][-5:]
        history_text = "\n".join(history_lines) or "No prior incidents recorded for this rule."

        return (
            f"Rule '{rule.name}' breached: {rule.description or rule.query}\n"
            f"PromQL: {rule.query}\n"
            f"Current value: {value}\n"
            f"Threshold: {rule.comparator} {rule.threshold}\n"
            f"Remediation target: {self._format_target(rule)}\n"
            f"Suggested action (hint only, use your judgement): {rule.action_hint or 'none'}\n\n"
            f"Recent incident history for this rule:\n{history_text}\n\n"
            "Decide what to do and use the available tools."
        )

    @staticmethod
    def _format_target(rule: Rule) -> str:
        if rule.target is None:
            return "no specific target configured"
        return f"{rule.target.kind}/{rule.target.name} in namespace '{rule.target.namespace}'"

    def _append_incident(self, incident: Incident) -> None:
        self.incidents.append(incident)
        self.incidents = self.incidents[-200:]
