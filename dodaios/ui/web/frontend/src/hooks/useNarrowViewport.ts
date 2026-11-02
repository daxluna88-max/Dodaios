import { useEffect, useState } from "react";
import { NARROW_VIEWPORT_PX } from "@/lib/navCollapsedDefault";

/** True while the window is phone-wide; follows rotation and resizing. */
export function useNarrowViewport(): boolean {
  const query = `(max-width: ${NARROW_VIEWPORT_PX - 1}px)`;
  const [narrow, setNarrow] = useState<boolean>(() => {
    try {
      return window.matchMedia(query).matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    let list: MediaQueryList;
    try {
      list = window.matchMedia(query);
    } catch {
      return;
    }
    const onChange = () => setNarrow(list.matches);
    onChange();
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return narrow;
}
