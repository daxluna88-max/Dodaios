/**
 * The limits panel (ADR-0056, block 12): the range of AUTOMATIC adaptation and the owner's
 * EXPLICIT request are shown as two different things, the allowed moods bind both, and the panel
 * does not promise a modulated voice, a connected avatar or lip sync.
 *
 * Written, NOT run (no vitest/tsc run in this block).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/i18n")>()),
  useT: () => (key: string) => key,
  useLocaleChunk: () => true,
}));

import { PersonalityLimits } from "@/views/assistant/PersonalityLimits";
import type { PersonalityState } from "@/views/assistant/personalityApi";

function state(over: Partial<NonNullable<PersonalityState["limits"]>> = {}): PersonalityState {
  return {
    limits: {
      adaptation_max_intensity: 1,
      owner_max_intensity: 3,
      moods_allowed: ["cheerful", "serious"],
      locked_mood: null,
      ...over,
    },
    channels: { text: "style_instruction", realtime: "session_instructions", voice: "unavailable", avatar: "not_wired", lip_sync: "none" },
    realtime_delivery: { "openai-realtime": "every_turn", "gemini-live": "connect_only" },
  } as unknown as PersonalityState;
}

afterEach(cleanup);

describe("PersonalityLimits", () => {
  it("shows the automatic range and the owner request as two separate blocks", () => {
    render(<PersonalityLimits data={state()} />);
    expect(screen.getByTestId("personality-limits-auto")).toBeTruthy();
    expect(screen.getByTestId("personality-limits-owner")).toBeTruthy();
    expect(screen.getByTestId("personality-limits-auto-value").textContent).toContain("personality_limits.auto_value");
    expect(screen.getByTestId("personality-limits-owner-value").textContent).toContain("personality_limits.owner_value");
  });

  it("says automatic adaptation is off when the range is zero", () => {
    render(<PersonalityLimits data={state({ adaptation_max_intensity: 0 })} />);
    expect(screen.getByTestId("personality-limits-auto-value").textContent).toBe("personality_limits.auto_off");
  });

  it("lists only the allowed moods and the delivery per realtime engine", () => {
    render(<PersonalityLimits data={state()} />);
    const moods = screen.getByTestId("personality-limits-moods").textContent ?? "";
    expect(moods).toContain("personality_mood_cheerful");
    expect(moods).not.toContain("personality_mood_calm");
    const delivery = screen.getByTestId("personality-limits-delivery").textContent ?? "";
    expect(delivery).toContain("openai-realtime: personality_limits.delivery_every_turn");
    expect(delivery).toContain("gemini-live: personality_limits.delivery_connect_only");
  });

  it("states that the voice is not modulated and there is no lip sync", () => {
    render(<PersonalityLimits data={state()} />);
    const text = screen.getByTestId("personality-limits-channels").textContent ?? "";
    expect(text).toContain("personality_limits.ch_voice");
    expect(text).toContain("personality_limits.ch_avatar");
  });

  it("renders nothing for an older server that sends no limits", () => {
    const old = { channels: {} } as unknown as PersonalityState;
    const { container } = render(<PersonalityLimits data={old} />);
    expect(container.innerHTML).toBe("");
  });
});
