(() => {
  "use strict";

  const status = document.querySelector("#status");
  const summary = document.querySelector("#report-summary");
  const list = document.querySelector("#report-list");
  const openButton = document.querySelector("#open-tour");
  let shareToken = typeof globalThis.__CARR_TOUR_TAKE_SHARE_TOKEN__ === "function"
    ? globalThis.__CARR_TOUR_TAKE_SHARE_TOKEN__() : "";
  let reportProperties = new globalThis.Map();
  let mapInstance = null;
  // Client feedback (shortlist and comment). The projection ref comes only from
  // the feedback read; a property gets controls only if that read lists it.
  // feedbackState: loading | ready | none (link has no feedback scope) | unavailable.
  const FEEDBACK_TIMEOUT_MS = 8000;
  const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;
  const feedbackStatus = document.querySelector("#feedback-status");
  const retryButton = document.querySelector("#retry-feedback");
  let feedback = null;
  let feedbackState = "loading";
  const rowsByRef = new globalThis.Map();
  const wired = new globalThis.Set();

  function setStatus(message) { status.textContent = message; }

  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    let data = null;
    try { data = await response.json(); } catch { /* errors remain generic */ }
    if (!response.ok) throw new Error(data?.error || "request_failed");
    return data;
  }

  function text(value, fallback) {
    return typeof value === "string" && value ? value : fallback;
  }

  function validPropertyRef(value) {
    return typeof value === "string" && /^property:public:[A-Za-z0-9_-]{16,128}$/.test(value);
  }

  function routeOrder(item, index) {
    return Number.isFinite(item?.route_sequence) ? item.route_sequence : index + 1;
  }

  function propertyAddress(item, fallback) {
    const parts = [item?.address, item?.suite].filter(value => typeof value === "string" && value.trim());
    return parts.length ? parts.join(" · ") : fallback;
  }

  async function send(path, body) {
    const response = await fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    let data = null;
    try { data = await response.json(); } catch { /* an unreadable body is an unknown outcome */ }
    return { status: response.status, saved: response.status === 200 && data?.data?.saved === true };
  }

  // A control owns one unsettled attempt. Re-sending the same value is an
  // explicit retry and keeps the key; a different value is a new action and
  // retires the old attempt. Only a read "saved" acknowledgement settles an
  // attempt as saved; a 200 without one, a 5xx or a dropped connection leaves
  // the outcome unknown and the key in place.
  async function submitFeedback(control, kind, propertyRef, value) {
    if (!feedback || !feedback.refs.has(propertyRef)) return "refused";
    if (control.inFlight) return "busy";
    if (!control.attempt || control.attempt.value !== value) control.attempt = { key: crypto.randomUUID(), value };
    const body = kind === "shortlist"
      ? { projection_ref: feedback.projectionRef, property_ref: propertyRef, shortlisted: value, idempotency_key: control.attempt.key }
      : { projection_ref: feedback.projectionRef, property_ref: propertyRef, comment: value, idempotency_key: control.attempt.key };
    control.inFlight = true;
    try {
      const result = await send(`/api/share/${kind}`, body);
      if (result.saved) { control.attempt = null; return "saved"; }
      if (result.status === 200 || result.status >= 500) return "retry";
      control.attempt = null;
      if (result.status === 401 || result.status === 403 || result.status === 404) { feedback = null; return "unavailable"; }
      return "refused";
    } catch { return "retry"; }
    finally { control.inFlight = false; }
  }

  const OUTCOME = {
    saved: "Saved.", retry: "Not confirmed yet. Try again.", busy: "Still saving…",
    unavailable: "This link is no longer active. Ask your broker for a new one.", refused: "That could not be saved.",
  };

  function feedbackControls(propertyRef, note) {
    const box = document.createElement("div");
    box.className = "feedback";
    const say = message => { note.textContent = message; };
    if (feedback.scopes.has("shortlist")) {
      const control = { inFlight: false, attempt: null };
      let on = false;
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "shortlist-toggle";
      const paint = () => {
        pick.setAttribute("aria-pressed", String(on));
        pick.textContent = on ? "Shortlisted" : "Add to shortlist";
      };
      paint();
      pick.addEventListener("click", async () => {
        const wanted = !on;
        pick.disabled = true;
        const result = await submitFeedback(control, "shortlist", propertyRef, wanted);
        pick.disabled = false;
        if (result === "saved") { on = wanted; paint(); }
        say(OUTCOME[result]);
        if (result === "unavailable") disableFeedback();
      });
      box.append(pick);
    }
    if (feedback.scopes.has("comment")) {
      const control = { inFlight: false, attempt: null };
      const field = document.createElement("input");
      field.type = "text";
      field.className = "comment-field";
      field.maxLength = 1000;
      field.setAttribute("aria-label", "One-line comment for your broker on this property");
      const post = document.createElement("button");
      post.type = "button";
      post.className = "comment-send";
      post.textContent = "Send comment";
      const sendComment = async () => {
        const snapshot = field.value.trim();
        if (!snapshot) { say("Write a comment first."); return; }
        if (CONTROL_CHARS.test(snapshot)) { say("Comments are one line. Remove line breaks and special characters."); return; }
        post.disabled = true;
        const result = await submitFeedback(control, "comment", propertyRef, snapshot);
        post.disabled = false;
        if (result === "saved") {
          // Only clear what was sent; a newer draft typed meanwhile stays.
          if (field.value.trim() === snapshot) { field.value = ""; say(OUTCOME.saved); }
          else say("Saved. Your newer text is still in the box.");
        } else say(OUTCOME[result]);
        if (result === "unavailable") disableFeedback();
      };
      post.addEventListener("click", sendComment);
      field.addEventListener("keydown", event => {
        if (event?.key !== "Enter") return;
        if (typeof event.preventDefault === "function") event.preventDefault();
        return sendComment();
      });
      box.append(field, post);
    }
    return box;
  }

  function attachFeedback() {
    if (feedbackState !== "ready") return;
    for (const [ref, row] of rowsByRef) {
      if (wired.has(ref) || !feedback.refs.has(ref)) continue;
      wired.add(ref);
      const note = document.createElement("p");
      note.className = "status feedback-note";
      note.setAttribute("role", "status");
      row.append(feedbackControls(ref, note), note);
    }
  }

  function disableFeedback() {
    feedbackState = "none";
    list.dataset.feedbackState = feedbackState;
    wired.clear();
    for (const control of list.querySelectorAll(".feedback")) control.remove();
  }

  function render(report) {
    const items = Array.isArray(report?.stops) ? report.stops :
      (Array.isArray(report?.items) ? report.items : (Array.isArray(report?.properties) ? report.properties : []));
    const properties = items.map((item, index) => ({ item, index }))
      .filter(({ item }) => validPropertyRef(item?.property_ref))
      .sort((left, right) => routeOrder(left.item, left.index) - routeOrder(right.item, right.index));
    reportProperties = new globalThis.Map(properties.map(({ item }) => [item.property_ref, item]));
    document.querySelector("#report-title").textContent = "Tour report";
    summary.textContent = `${properties.length} ${properties.length === 1 ? "property" : "properties"} in this report.`;
    list.replaceChildren();
    rowsByRef.clear();
    wired.clear();
    for (const { item, index } of properties) {
      const row = document.createElement("li");
      row.className = "report-item";
      const route = document.createElement("p");
      route.className = "route-label";
      route.textContent = text(item.route_label, `Stop ${routeOrder(item, index)}`);
      const heading = document.createElement("h3");
      heading.textContent = text(item.name, text(item.title, "Tour property"));
      const detail = document.createElement("p");
      detail.textContent = text(item.summary, text(item.status, propertyAddress(item, "Details available in the packet.")));
      row.append(route, heading, detail);
      rowsByRef.set(item.property_ref, row);
      list.append(row);
    }
    if (!properties.length) list.textContent = "No properties are available in this report.";
    list.setAttribute("aria-busy", "false");
    attachFeedback();
  }

  function validMapPoint(point) {
    return validPropertyRef(point?.property_ref) && Number.isInteger(point?.route_sequence) && point.route_sequence > 0 &&
      Number.isFinite(point?.latitude) && point.latitude >= -90 && point.latitude <= 90 &&
      Number.isFinite(point?.longitude) && point.longitude >= -180 && point.longitude <= 180;
  }

  async function renderMap(payload) {
    const points = (Array.isArray(payload?.points) ? payload.points : []).filter(validMapPoint)
      .sort((left, right) => left.route_sequence - right.route_sequence);
    if (!points.length) return;
    const { LngLatBounds, Map: MapLibreMap, Marker, NavigationControl, Popup, setWorkerUrl } =
      await import("/vendor/maplibre-gl-6.4.1/maplibre-gl.mjs");
    setWorkerUrl("/vendor/maplibre-gl-6.4.1/maplibre-gl-worker.mjs");
    const mapSection = document.querySelector("#map-section");
    mapSection.hidden = false;
    if (mapInstance) mapInstance.remove();
    mapInstance = new MapLibreMap({
      container: "tour-map",
      style: { version: 8, sources: {}, layers: [{ id: "background", type: "background", paint: { "background-color": "#eef4f8" } }] },
      center: [points[0].longitude, points[0].latitude], zoom: 11, attributionControl: false,
    });
    mapInstance.addControl(new NavigationControl({ showCompass: false }), "top-right");
    const bounds = new LngLatBounds();
    for (const point of points) {
      const coordinate = [point.longitude, point.latitude];
      bounds.extend(coordinate);
      const property = reportProperties.get(point.property_ref) || {};
      const marker = document.createElement("button");
      marker.type = "button";
      marker.textContent = typeof point.route_label === "string" ? point.route_label : String(point.route_sequence);
      marker.setAttribute("aria-label", `Stop ${point.route_sequence}: ${text(property.name, "Tour property")}`);
      const popupBody = document.createElement("div");
      const popupTitle = document.createElement("strong");
      popupTitle.textContent = text(property.name, `Stop ${point.route_sequence}`);
      const popupAddress = document.createElement("div");
      popupAddress.textContent = propertyAddress(property, "Verified access point");
      popupBody.append(popupTitle, popupAddress);
      new Marker({ element: marker }).setLngLat([point.longitude, point.latitude])
        .setPopup(new Popup({ offset: 18 }).setDOMContent(popupBody)).addTo(mapInstance);
    }
    mapInstance.on("load", () => {
      if (points.length > 1) mapInstance.fitBounds(bounds, { padding: 56, maxZoom: 14, duration: 0 });
    });
  }

  async function fetchReport() {
    const payload = await request("/api/share/report");
    return payload.data || {};
  }

  async function fetchMap() {
    const payload = await request("/api/share/map");
    return payload.data || {};
  }

  // Resolves with the feedback grant, or null when this link carries no
  // feedback scope. Anything else (5xx, a malformed body) throws: unavailable.
  async function fetchFeedback(signal) {
    const response = await fetch("/api/share/feedback", { credentials: "same-origin", ...(signal ? { signal } : {}) });
    if (response.status === 401 || response.status === 403 || response.status === 404) return null;
    if (!response.ok) throw new Error("feedback_unavailable");
    let payload = null;
    try { payload = await response.json(); } catch { throw new Error("feedback_unavailable"); }
    const data = payload?.data;
    if (!data || typeof data !== "object" || !/^projection:public:[A-Za-z0-9_-]{16,128}$/.test(data.projection_ref || "") ||
      !Array.isArray(data.permission_scopes) || !Array.isArray(data.items)) throw new Error("feedback_unavailable");
    const scopes = new globalThis.Set(data.permission_scopes.filter(scope => scope === "shortlist" || scope === "comment"));
    const refs = new globalThis.Set(data.items.map(item => item?.property_ref).filter(validPropertyRef));
    if (!scopes.size || !refs.size) return null;
    return { projectionRef: data.projection_ref, scopes, refs };
  }

  // Optional and independent: it has its own deadline and never gates the
  // packet or map. A failure is reported and can be retried.
  async function loadFeedback() {
    feedbackState = "loading";
    list.dataset.feedbackState = feedbackState;
    feedbackStatus.textContent = "";
    retryButton.hidden = true;
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    let timer = null;
    try {
      feedback = await new Promise((resolve, reject) => {
        timer = setTimeout(() => { if (controller) controller.abort(); reject(new Error("feedback_timeout")); }, FEEDBACK_TIMEOUT_MS);
        fetchFeedback(controller?.signal).then(resolve, reject);
      });
      feedbackState = feedback ? "ready" : "none";
    } catch {
      feedback = null;
      feedbackState = "unavailable";
      feedbackStatus.textContent = "Shortlist and comments are unavailable right now.";
      retryButton.hidden = false;
    } finally { clearTimeout(timer); }
    list.dataset.feedbackState = feedbackState;
    attachFeedback();
  }

  async function loadTour() {
    try {
      const reports = Promise.allSettled([fetchReport(), fetchMap()]);
      void loadFeedback();
      // Packet and map are independently scoped. Fetch both, then render in a
      // stable order so a valid map-only or packet-only grant still opens.
      const [reportResult, mapResult] = await reports;
      const reportLoaded = reportResult.status === "fulfilled";
      const mapLoaded = mapResult.status === "fulfilled";
      if (!reportLoaded && !mapLoaded) throw new Error("share_scope_unavailable");
      if (reportLoaded) render(reportResult.value);
      else {
        document.querySelector("#report-title").textContent = "Shared tour map";
        summary.textContent = "Verified access points included in this share.";
        list.textContent = "This link includes the interactive map only.";
        list.setAttribute("aria-busy", "false");
      }
      if (mapLoaded) await renderMap(mapResult.value);
      else document.querySelector("#map-section").hidden = true;
      setStatus(reportLoaded && mapLoaded ? "Report and map loaded." : reportLoaded ? "Report loaded." : "Map loaded.");
    } catch {
      setStatus("This shared report is unavailable.");
      list.setAttribute("aria-busy", "false");
    }
  }

  async function openTour() {
    const token = shareToken;
    shareToken = "";
    openButton.disabled = true;
    if (!token) return;
    try {
      await request("/api/share/exchange", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ token }),
      });
      await loadTour();
    } catch {
      setStatus("This shared report is unavailable.");
      list.setAttribute("aria-busy", "false");
    }
  }

  function bootstrap() {
    if (!shareToken) {
      openButton.hidden = true;
      setStatus("Opening your shared report…");
      void loadTour();
      return;
    }
    openButton.disabled = false;
    setStatus("Select Open tour to view this shared report.");
  }

  openButton.addEventListener("click", () => { void openTour(); });
  retryButton.addEventListener("click", () => { void loadFeedback(); });
  bootstrap();
})();
