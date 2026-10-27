"""Block 13 (written, NOT run): the avatar follows real events only, and STOP makes it idle."""
from __future__ import annotations

import asyncio

from dodaios.avatar.bridge import AvatarBridge
from dodaios.avatar.capabilities import OUTPUT_SIGNALS, TTS_CAPABILITIES, lip_sync_available
from dodaios.control.stop import StopController
from dodaios.core.bus import EventBus
from dodaios.core.events import (
    AudioOutFirst,
    AvatarSignal,
    SystemStateChanged,
    VoiceTurnStarted,
)


async def _setup():
    bus, ctl, seen = EventBus(), StopController(settle_s=0.0), []

    async def collect(ev):
        seen.append(ev)

    bus.subscribe(AvatarSignal, collect)
    bridge = AvatarBridge(bus, ctl)
    bridge.attach()
    return bus, ctl, seen


async def _kinds(seen):
    await asyncio.sleep(0.05)
    return [e.kind for e in seen]


async def test_nothing_is_emitted_without_a_real_event():
    _bus, _ctl, seen = await _setup()
    assert await _kinds(seen) == []


async def test_speaking_needs_a_turn_and_an_audible_start():
    bus, _ctl, seen = await _setup()
    await bus.publish(AudioOutFirst())  # no turn yet: dropped
    await bus.publish(VoiceTurnStarted(turn_id="t1"))
    await bus.publish(AudioOutFirst())
    await bus.publish(SystemStateChanged(new_state="IDLE"))
    assert await _kinds(seen) == ["turn_start", "speaking_start", "speaking_stop"]


async def test_stop_goes_idle_and_a_late_audio_start_is_dropped():
    bus, ctl, seen = await _setup()
    await bus.publish(VoiceTurnStarted(turn_id="t1"))
    await ctl.stop()
    await bus.publish(AudioOutFirst())  # stale: door closed
    kinds = await _kinds(seen)
    assert kinds == ["turn_start", "stop", "idle"]
    ctl.resume()
    await bus.publish(AudioOutFirst())  # new generation, but no new turn yet: still dropped
    assert await _kinds(seen) == ["turn_start", "stop", "idle"]


async def test_stale_expression_plan_is_dropped():
    from dodaios.brain.expression import ExpressionPlan, PlanGate, TurnIdentity

    bus, ctl, seen = await _setup()
    bridge = AvatarBridge(bus, ctl)
    gate = PlanGate()
    assert gate.begin("consumer", "s", "t1", ctl.generation)
    plan = ExpressionPlan(TurnIdentity("consumer", "s", "t1", ctl.generation), "v", "smile")
    await ctl.stop()
    assert await bridge.offer_plan(plan, gate) is False


def test_no_provider_claims_lip_sync_or_per_turn_modulation():
    assert all(not v["per_turn"] for v in TTS_CAPABILITIES.values())
    assert not any(lip_sync_available(k) for k in OUTPUT_SIGNALS)
    assert not any("viseme" in str(v).lower() for v in OUTPUT_SIGNALS.values())
