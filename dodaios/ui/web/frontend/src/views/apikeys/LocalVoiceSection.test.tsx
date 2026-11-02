/**
 * The Local voice card is mounted on the Live calls tab (it had no screen before). It must stay out of the way
 * until the provider list is loaded and offers it, and it must never switch anything on by itself.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  useUiLanguage: () => "en",
}));

const state = vi.hoisted(() => ({
  providers: [] as Array<Record<string, unknown>>,
  loading: false,
  error: null as string | null,
}));
vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => ({
    providers: state.providers,
    loading: state.loading,
    error: state.error,
    refetch: () => undefined,
    setActiveOptimistic: () => undefined,
  }),
}));
vi.mock("@/components/providers/ProviderTierSection", () => ({
  makeProviderCategories: () => ({}),
  ProviderCategory: () => null,
  useTierHealth: () => ({ realtime: undefined }),
  TierSection: ({ providers }: { providers: Array<{ id: string }> }) => (
    <div data-testid="tier-section">{providers.map((p) => p.id).join(",")}</div>
  ),
}));

import { LocalVoiceSection } from "@/views/apikeys/VoiceEngine";

const LOCAL = { id: "local-voice", tier: "realtime", label: "Local voice" };
const GPT = { id: "openai-live", tier: "realtime", label: "GPT-Live" };

beforeEach(() => {
  state.providers = [GPT, LOCAL];
  state.loading = false;
  state.error = null;
});
afterEach(() => cleanup());

describe("LocalVoiceSection", () => {
  it("shows only the local voice provider, with its title and description", () => {
    render(<LocalVoiceSection />);
    expect(screen.getByTestId("voice-local-section")).toBeTruthy();
    expect(screen.getByTestId("tier-section").textContent).toBe("local-voice");
    expect(screen.getByText("providers_page.local_voice_section_title")).toBeTruthy();
    expect(screen.getByText("providers_page.local_voice_section_desc")).toBeTruthy();
  });

  it("renders nothing while loading, on error, or when the provider is not offered", () => {
    state.loading = true;
    const { rerender } = render(<LocalVoiceSection />);
    expect(screen.queryByTestId("voice-local-section")).toBeNull();
    state.loading = false;
    state.error = "backend unreachable";
    rerender(<LocalVoiceSection />);
    expect(screen.queryByTestId("voice-local-section")).toBeNull();
    state.error = null;
    state.providers = [GPT];
    rerender(<LocalVoiceSection />);
    expect(screen.queryByTestId("voice-local-section")).toBeNull();
  });
});
