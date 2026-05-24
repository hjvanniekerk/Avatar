const BUILD_VERSION = "20260524-235811";
const CACHE_VERSION = "nemotron-avatar-v210-20260524-235811";
const STATIC_ASSETS = [
  "./",
  "retarget.js",
  "ik.js",
  "skeletal-solver.js",
  "assets/metahuman-current-clean.glb",
  "assets/metahuman-current.glb",
  "assets/metahuman-sheena-match-morph.gltf",
  "assets/metahuman-sheena-match-morph.bin",
  "assets/metahuman-sheena-match-morph-textures/body_bc.png",
  "assets/metahuman-sheena-match-morph-textures/body_n.png",
  "assets/metahuman-sheena-match-morph-textures/head_lod1_bc.png",
  "assets/metahuman-sheena-match-morph-textures/head_lod1_n.png",
  "assets/metahuman-sheena-match-morph-textures/eye_l_bc.png",
  "assets/metahuman-sheena-match-morph-textures/eye_r_bc.png",
  "assets/metahuman-sheena-match-morph-textures/teeth_bc.png",
  "assets/metahuman-sheena-match-morph.glb",
  "assets/metahuman-sheena-match.glb",
  "assets/sheena-reference-joints.json",
  "assets/sheena-parveen-reference.mp4",
  "presenters.json",
  "manifest.webmanifest",
  "icon-192.png",
  "icon-512.png",
];

const NETWORK_FIRST = new Set(["app.js", "style.css", "index.html", "build.txt"]);
const NO_STORE_SUFFIXES = [
  "browser-rl-status.json",
  "browser-rl-policy.json",
  "rl-history.json",
  "rl-improvement-status.json",
  "realism.json",
  "nemotron-status.json",
  "gibberlink-status.json",
  "nt-workload-status.json",
  "nt-optimizer-directives.json",
];

console.log("[nva-sw] build " + BUILD_VERSION + " cache " + CACHE_VERSION);

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_VERSION);
    await Promise.all(STATIC_ASSETS.map(async asset => {
      const url = asset + (asset.includes("?") ? "&" : "?") + "swv=" + encodeURIComponent(BUILD_VERSION);
      try {
        const response = await fetch(url, {cache: "reload"});
        if (response && response.ok) await cache.put(url, response);
      } catch {}
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("message", event => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key !== CACHE_VERSION).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

function sameOrigin(requestUrl){
  return requestUrl.origin === self.location.origin;
}

function cleanName(url){
  const name = url.pathname.split("/").filter(Boolean).pop() || "index.html";
  return name || "index.html";
}

function isNavigation(request, url){
  return request.mode === "navigate"
    || url.pathname.endsWith("/avatar/")
    || url.pathname.endsWith("/avatar")
    || url.pathname.endsWith("/index.html")
    || url.pathname.endsWith("/fresh.html");
}

function buildBustedRequest(request){
  const url = new URL(request.url);
  url.searchParams.set("nva_build", BUILD_VERSION);
  return new Request(url.toString(), {
    method: request.method,
    headers: request.headers,
    mode: request.mode === "navigate" ? "same-origin" : request.mode,
    credentials: request.credentials,
    redirect: request.redirect,
    referrer: request.referrer,
    cache: "no-store",
  });
}

async function networkFirst(event, cacheKey){
  try {
    const response = await fetch(buildBustedRequest(event.request));
    if (response && response.ok){
      const cache = await caches.open(CACHE_VERSION);
      await cache.put(cacheKey || event.request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(cacheKey || event.request);
    return cached || new Response("", {status: 504, statusText: "offline"});
  }
}

async function cacheFirst(event){
  const cached = await caches.match(event.request, {ignoreSearch: true});
  if (cached) return cached;
  const response = await fetch(event.request);
  if (response && response.ok){
    const cache = await caches.open(CACHE_VERSION);
    await cache.put(event.request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || !sameOrigin(url)) return;

  if (isNavigation(event.request, url)){
    event.respondWith(networkFirst(event, "index.html"));
    return;
  }

  const name = cleanName(url);
  if (NETWORK_FIRST.has(name)){
    event.respondWith(networkFirst(event, name));
    return;
  }

  if (NO_STORE_SUFFIXES.some(suffix => url.pathname.endsWith("/" + suffix))){
    event.respondWith(fetch(buildBustedRequest(event.request)).catch(() => new Response("", {status: 504, statusText: "offline"})));
    return;
  }

  if (/\/(?:debug|verify)-.*\.(?:png|json)$/i.test(url.pathname)){
    event.respondWith(fetch(buildBustedRequest(event.request)).catch(() => new Response("", {status: 404, statusText: "not cached"})));
    return;
  }

  if (url.pathname.endsWith("/sample-export-control.php")){
    event.respondWith(fetch(event.request, {cache: "no-store"}).catch(() => new Response('{"enabled":true,"error":"offline"}', {
      status: 504,
      headers: {"Content-Type": "application/json"},
    })));
    return;
  }

  event.respondWith(cacheFirst(event));
});
