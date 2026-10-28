/**
 * Initial state of the nav sidebar: icon rail or expanded.
 *
 * An explicit choice stored by the user always wins. With no stored choice, a
 * phone-width window starts on the icon rail: the 240 px column would
 * otherwise leave under 160 px for the page, and the composer is cut off.
 */
export const NARROW_VIEWPORT_PX = 640;

export function initialNavCollapsed(
  storedValue: string | null,
  viewportWidth: number,
): boolean {
  if (storedValue === "true") return true;
  if (storedValue === "false") return false;
  return viewportWidth < NARROW_VIEWPORT_PX;
}
