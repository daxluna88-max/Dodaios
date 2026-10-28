"""Blocks 17 + 18 (written, NOT run): late or identity-less events never act on the NEW turn.

Block 18 adds the admission order ``turn_seq``: the bridge needs the COMPLETE identity
(generation, order, session id, turn id) on every active-turn signal and adopts a start only if
its order is newer than every start adopted before.
"""
from __future__ import annotations

import asyncio

from dodaios.avatar.bridge import AvatarBridge
from dodaios.control.stop import StopController
from dodaios.core.bus import EventBus
from dodaios.core.events import (
    AudioOutFirst,
    AvatarSignal,
    BrainTurnStarted,
    SystemStateChanged,
    VoiceTurnCompleted,
    VoiceTurnStarted,
    next_turn_seq,
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


def _id(g, seq, tid, sid="s"):
    return {"stop_generation": g, "turn_seq": seq, "turn_id": tid, "session_id": sid}


def test_next_turn_seq_is_strictly_increasing_and_positive():
    a, b, c = next_turn_seq(), next_turn_seq(), next_turn_seq()
    assert 0 < a < b < c


async def test_stop_resume_new_turn_then_old_start_audio_completion_are_dropped():
    bus, ctl, seen = await _setup()
    old = ctl.generation
    await bus.publish(VoiceTurnStarted(**_id(old, 1, "t-old")))
    await ctl.stop()
    ctl.resume()
    new = ctl.generation
    assert new != old
    await bus.publish(VoiceTurnStarted(**_id(new, 2, "t-new")))
    base = await _kinds(seen)
    assert base == ["turn_start", "stop", "idle", "turn_start"]
    # OLD events arrive late, after the new turn started.
    await bus.publish(VoiceTurnStarted(**_id(old, 1, "t-old")))
    await bus.publish(AudioOutFirst(**_id(old, 1, "t-old")))
    await bus.publish(VoiceTurnCompleted(**_id(old, 1, "t-old")))
    assert await _kinds(seen) == base
    # The NEW turn still works, and an old completion cannot end its speech.
    await bus.publish(AudioOutFirst(**_id(new, 2, "t-new")))
    await bus.publish(VoiceTurnCompleted(**_id(old, 1, "t-old")))
    await bus.publish(VoiceTurnCompleted(**_id(new, 2, "t-new")))
    assert (await _kinds(seen))[len(base):] == ["speaking_start", "speaking_stop"]


async def test_same_generation_other_turn_id_is_dropped():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(**_id(g, 2, "t2")))
    await bus.publish(AudioOutFirst(**_id(g, 1, "t1")))
    assert await _kinds(seen) == ["turn_start"]


async def test_events_without_identity_are_dropped_while_a_turn_is_active():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(turn_id="legacy"))  # no generation: never adopted
    await bus.publish(VoiceTurnStarted(**_id(g, 1, "t1")))
    await bus.publish(AudioOutFirst())  # legacy producer
    await bus.publish(VoiceTurnCompleted(turn_id="t1"))  # legacy producer
    await bus.publish(BrainTurnStarted())  # legacy producer, turn active
    assert await _kinds(seen) == ["turn_start"]


async def test_old_brain_turn_after_resume_is_dropped():
    bus, ctl, seen = await _setup()
    old = ctl.generation
    await ctl.stop()
    ctl.resume()
    await bus.publish(BrainTurnStarted(**_id(old, 1, "t1")))
    assert await _kinds(seen) == ["stop", "idle"]


# -- block 18 ------------------------------------------------------------------------------


async def test_same_generation_events_with_each_identity_field_missing_are_rejected():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(**_id(g, 5, "t5")))
    full = _id(g, 5, "t5")
    for missing, blank in (("session_id", ""), ("turn_id", ""), ("turn_seq", 0),
                           ("stop_generation", -1)):
        partial = {**full, missing: blank}
        await bus.publish(AudioOutFirst(**partial))
        await bus.publish(VoiceTurnCompleted(**partial))
    assert await _kinds(seen) == ["turn_start"]  # nothing spoke, nothing ended
    # The complete identity still works afterwards.
    await bus.publish(AudioOutFirst(**full))
    await bus.publish(VoiceTurnCompleted(**full))
    assert await _kinds(seen) == ["turn_start", "speaking_start", "speaking_stop"]


async def test_wrong_session_or_wrong_order_with_same_turn_id_is_rejected():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(**_id(g, 5, "t5")))
    await bus.publish(AudioOutFirst(**_id(g, 5, "t5", sid="other")))
    await bus.publish(AudioOutFirst(**_id(g, 4, "t5")))
    assert await _kinds(seen) == ["turn_start"]


async def test_older_start_after_newer_start_does_not_retarget_the_bridge():
    bus, ctl, seen = await _setup()
    g = ctl.generation  # one STOP generation shared by both turns
    await bus.publish(VoiceTurnStarted(**_id(g, 7, "t-new")))
    await bus.publish(VoiceTurnStarted(**_id(g, 6, "t-old")))  # reversed order
    await bus.publish(AudioOutFirst(**_id(g, 6, "t-old")))  # would speak if retargeted
    assert await _kinds(seen) == ["turn_start"]
    await bus.publish(AudioOutFirst(**_id(g, 7, "t-new")))
    assert await _kinds(seen) == ["turn_start", "speaking_start"]


async def test_duplicate_start_with_equal_order_is_not_readopted():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(**_id(g, 3, "t3")))
    await bus.publish(AudioOutFirst(**_id(g, 3, "t3")))
    await bus.publish(VoiceTurnStarted(**_id(g, 3, "t3")))  # would reset speaking if adopted
    await bus.publish(VoiceTurnCompleted(**_id(g, 3, "t3")))
    assert await _kinds(seen) == ["turn_start", "speaking_start", "speaking_stop"]


async def test_order_floor_survives_stop_so_an_old_start_cannot_return_after_resume():
    bus, ctl, seen = await _setup()
    await bus.publish(VoiceTurnStarted(**_id(ctl.generation, 9, "t9")))
    await ctl.stop()
    ctl.resume()
    # Current generation, but an order not newer than the one already adopted.
    await bus.publish(VoiceTurnStarted(**_id(ctl.generation, 8, "t8")))
    assert await _kinds(seen) == ["turn_start", "stop", "idle"]
    await bus.publish(VoiceTurnStarted(**_id(ctl.generation, 10, "t10")))
    assert (await _kinds(seen))[-1] == "turn_start"


async def test_unscoped_system_state_cannot_end_speaking_of_the_current_turn():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(VoiceTurnStarted(**_id(g, 1, "t1")))
    await bus.publish(AudioOutFirst(**_id(g, 1, "t1")))
    await bus.publish(SystemStateChanged(new_state="LISTENING"))  # unscoped, e.g. an older turn
    await bus.publish(SystemStateChanged(new_state="IDLE"))
    await bus.publish(SystemStateChanged(new_state="IDLE", **_id(g, 0, "t1")))  # incomplete
    assert await _kinds(seen) == ["turn_start", "speaking_start"]
    # Scoped to ANOTHER turn: also no proof.
    await bus.publish(SystemStateChanged(new_state="IDLE", **_id(g, 2, "t2")))
    assert await _kinds(seen) == ["turn_start", "speaking_start"]
    # Scoped to the followed turn: counts.
    await bus.publish(SystemStateChanged(new_state="IDLE", **_id(g, 1, "t1")))
    assert await _kinds(seen) == ["turn_start", "speaking_start", "speaking_stop"]


async def test_unscoped_system_state_still_drives_state_when_no_turn_is_tracked():
    bus, _ctl, seen = await _setup()
    await bus.publish(SystemStateChanged(new_state="LISTENING"))
    assert await _kinds(seen) == ["listening"]


async def test_identity_less_brain_turn_without_a_voice_turn_tracks_nothing():
    bus, ctl, seen = await _setup()
    await bus.publish(BrainTurnStarted())  # chat: thinking display only
    await bus.publish(AudioOutFirst(stop_generation=ctl.generation))  # nothing to follow
    assert await _kinds(seen) == ["thinking"]


async def test_brain_turn_with_complete_identity_adopts_only_if_newer():
    bus, ctl, seen = await _setup()
    g = ctl.generation
    await bus.publish(BrainTurnStarted(**_id(g, 4, "t4")))  # classic: brain start first
    await bus.publish(AudioOutFirst(**_id(g, 4, "t4")))
    await bus.publish(BrainTurnStarted(**_id(g, 3, "t3")))  # older: dropped
    assert await _kinds(seen) == ["thinking", "speaking_start"]
