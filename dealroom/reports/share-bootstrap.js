(() => {
  "use strict";

  const fragment = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  const params = new URLSearchParams(fragment);
  const candidate = /^[A-Za-z0-9_-]{43}$/.test(fragment) ? fragment
    : (params.size === 1 && params.has("token") ? params.get("token") : "");
  let token = /^[A-Za-z0-9_-]{43}$/.test(candidate || "") ? candidate : "";

  // Scrub the bearer before any optional renderer dependency is requested.
  window.history.replaceState(null, document.title, `${window.location.pathname}${window.location.search}`);
  Object.defineProperty(globalThis, "__CARR_TOUR_TAKE_SHARE_TOKEN__", {
    configurable: true,
    value() {
      const value = token;
      token = "";
      delete globalThis.__CARR_TOUR_TAKE_SHARE_TOKEN__;
      return value;
    },
  });
})();
