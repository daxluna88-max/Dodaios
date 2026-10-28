"""Block 17 (written, NOT run): late events of an OLD turn never act on the NEW turn."""
from __future__ import annotations

import asyncio

from dodaios.avatar.bridge import AvatarBridge
from dodaios.control.stop import StopController
from dodaios.core.bus import EventBus
from dodaios.core.events import (
    AudioOutFirst,
    AvatarSignal,
    BrainTurnStarted,
    VoiceTurnCompleted,
    VoiceTurnStarted,
)


async def _setup():
    bus, ctl, seen = EventBus(), StopController(settle_s=0.0), []

    async def collect(ev):
        seen.append(ev)

    bus.subscribe(AvatarSignal, collect)
    AvatarBridge(bus, ctl).attach()
    return bus, ctl, seen


async def _kinds(seen):
    await asyncio.sleep(0.05)
    return [e.kind for e in seen]


async def test_stop_resume_new_turn_then_old_start_audio_completion_are_dropped():
    bus, ctl, seen = await _setup()
    old = ctl.generation
    await bus.publish(VoiceTurnStarted(session_id="s", turn_id="t-old", stop_generation=old))
    await ctl.stop()
    ctl.resume()
    new = ctl.generation
    assert new != old
    await bus.publish(VoiceTurnStarted(session_id="s", turn_id="t-new", stop_generation=new))
    base = await _kinds(seen)
    assert base == ["turn_start", "stop", "idle", "turn_start"]
    # OLD events arrive late, after the new turn started.
    await bus.publish(VoiceTurnStarted(session_id="s", turn_id="t-old", stop_generation=old))
    await bus.publish(AudioOutFirst(session_id="s", turn_id="t-old", stop_generation=old))
    await bus.publish(VoiceTurnCompleted(session_id="s", turn_id="t-old", stop_generation=old))
    assert await _kinds(seen) == base
    # The NEW turn still works, and an old completion cannot end its speech.
    await bus.publish(AudioOutFirst(session_id="s", turn_id="t-new", stop_generation=new))
    await bus.publish(VoiceTurnCompleted(session_id="s", turn_id="t-old", stop_generation=old))
    await bus.publish(VoiceTurnCompleted(session_id="s", turn_id="t-new", stop_generation=new))
    assert (await _kinds(seen))[len(base):] == ["speaking_start", "speaking_stop"]


async def test_same_generation_other_turn_id_is_dropped():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(session_id="s", turn_id="t2", stop_generation=g))
    await bus.publish(AudioOutFirst(session_id="s", turn_id="t1", stop_generation=g))
    assert await _kinds(seen) == ["turn_start"]


async def test_events_without_identity_are_dropped_while_a_turn_is_active():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(turn_id="legacy"))  # no generation: never adopted
    await bus.publish(VoiceTurnStarted(turn_id="t1", stop_generation=g))
    await bus.publish(AudioOutFirst())  # legacy producer
    await bus.publish(VoiceTurnCompleted(turn_id="t1"))  # legacy producer
    await bus.publish(BrainTurnStarted())  # legacy producer, turn active
    assert await _kinds(seen) == ["turn_start"]


async def test_old_brain_turn_after_resume_is_dropped():
    bus, ctl, seen = await _setup()
    old = ctl.generation
    await ctl.stop()
    ctl.resume()
    await bus.publish(BrainTurnStarted(stop_generation=old))
    assert await _kinds(seen) == ["stop", "idle"]
