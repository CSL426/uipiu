

export function getMode(): "public" | "admin" {
  return window.location.pathname.startsWith("/admin") ? "admin" : "public";
}

export function navigateTo(pathname: string) {
  window.history.pushState({}, "", pathname);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
