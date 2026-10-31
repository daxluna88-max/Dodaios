import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MobileDeviceMenu } from "./MobileDeviceMenu";
import type { JarvisDeviceAdapter } from "@/lib/mobileDeviceAccess";

afterEach(cleanup);

function fake(allowed = true): JarvisDeviceAdapter {
  return {
    capability: () => allowed ? "available" : "unsupported",
    chooseFiles: vi.fn(async () => ({ files: [new File(["hi"], "foto.jpg", { type: "image/jpeg" })], source: "photo" as const })),
    location: vi.fn(async () => ({ latitude: 45, longitude: 9, accuracyMeters: 12, acquiredAt: 1 })),
    microphone: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream)),
    stop: vi.fn(),
  };
}

describe("MobileDeviceMenu", () => {
  it("never reads a sensor on initial rendering", () => {
    const adapter = fake();
    render(<MobileDeviceMenu adapter={adapter} onMedia={vi.fn()} onLocation={vi.fn()} />);
    expect(adapter.location).not.toHaveBeenCalled();
    expect(adapter.microphone).not.toHaveBeenCalled();
    expect(adapter.chooseFiles).not.toHaveBeenCalled();
  });
  it("asks for position only after explicit interaction", async () => {
    const adapter = fake();
    const onLocation = vi.fn();
    render(<MobileDeviceMenu adapter={adapter} onMedia={vi.fn()} onLocation={onLocation} />);
    fireEvent.click(screen.getByRole("button", { name: "Accessori telefono" }));
    fireEvent.click(screen.getByRole("button", { name: "Posizione" }));
    await waitFor(() => expect(onLocation).toHaveBeenCalledWith(expect.objectContaining({ latitude: 45 })));
  });
  it("does not acquire unavailable capabilities", () => {
    const adapter = fake(false);
    render(<MobileDeviceMenu adapter={adapter} onMedia={vi.fn()} onLocation={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Accessori telefono" }));
    fireEvent.click(screen.getByRole("button", { name: "Posizione" }));
    expect(screen.getByRole("alert").textContent).toContain("non disponibile");
    expect(adapter.location).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Microfono" })).toBeNull();
  });
  it("delivers chosen files without automatically uploading", async () => {
    const adapter = fake();
    const onMedia = vi.fn();
    render(<MobileDeviceMenu adapter={adapter} onMedia={onMedia} onLocation={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Accessori telefono" }));
    fireEvent.click(screen.getByRole("button", { name: "Fotocamera" }));
    await waitFor(() => expect(onMedia).toHaveBeenCalledWith(expect.objectContaining({ source: "photo" })));
  });
});
