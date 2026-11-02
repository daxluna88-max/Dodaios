/**
 * True when this page is served from the same machine as the browser (localhost). Obsidian is a
 * desktop app that reads the wiki from the disk of the machine where Jarvis runs, so on a remote
 * host (a VPS reached over the network or a tailnet) installing it there makes no sense.
 */
export function isLocalHost(hostname: string = typeof window === "undefined" ? "" : window.location.hostname): boolean {
  const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return host === "localhost" || host === "127.0.0.1" || host === "::1" || host.endsWith(".localhost");
}
