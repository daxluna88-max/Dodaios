import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MicControls } from "@/components/voice/MicControls";
import { getMicState, setMicModeSetting } from "@/lib/micSettings";

afterEach(() => { cleanup(); setMicModeSetting("auto"); });

describe("MicControls", () => {
  it("always shows the mode picker, the gate only during a call", () => {
    const { rerender } = render(<MicControls live={false} />);
    expect(screen.getByTestId("mic-mode")).toBeTruthy();
    expect(screen.queryByTestId("mic-gate")).toBeNull();
    rerender(<MicControls live />);
    expect(screen.getByTestId("mic-gate")).toBeTruthy();
  });
  it("tap toggles the gate in auto; hold opens it only while pressed in push", () => {
    render(<MicControls live />);
    fireEvent.click(screen.getByTestId("mic-gate"));
    expect(getMicState().open).toBe(false);
    act(() => setMicModeSetting("push"));
    expect(getMicState().open).toBe(false);
    fireEvent.pointerDown(screen.getByTestId("mic-gate"));
    expect(getMicState().open).toBe(true);
    fireEvent.pointerUp(screen.getByTestId("mic-gate"));
    expect(getMicState().open).toBe(false);
  });
  it("hides the gate when always on", () => {
    setMicModeSetting("always");
    render(<MicControls live />);
    expect(screen.queryByTestId("mic-gate")).toBeNull();
  });
});
