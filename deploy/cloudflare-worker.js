// Cloudflare Worker: serves the app under your own workers.dev link, so the shared address does not show the
// GitHub account. Paste this whole file into the Worker's editor (Cloudflare dashboard) and press Deploy.
//   /data/live.json -> the live file in the repository (updated every 10 minutes in market hours)
//   /reels/<file>   -> the daily videos (the repository's "reels" release)
//   everything else  -> the GitHub Pages site
const SITE = "https://ashokkumar3502.github.io/nse-research-lab";
const LIVE = "https://raw.githubusercontent.com/ashokkumar3502/nse-research-lab/main/docs/data/live.json";
const REELS = "https://github.com/ashokkumar3502/nse-research-lab/releases/download/reels/";
const DROP = ["x-github-request-id", "x-fastly-request-id", "x-served-by", "x-cache", "x-cache-hits", "x-timer",
              "via", "x-proxy-cache", "access-control-allow-origin", "content-security-policy", "server"];

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method not allowed", {status: 405});
    const live = url.pathname === "/data/live.json";
    if (url.pathname.startsWith("/reels/")) {                 // videos: pass Range so the player can seek
      const v = await fetch(REELS + url.pathname.slice(7), {headers: request.headers.get("range") ? {range: request.headers.get("range")} : {}, redirect: "follow"});
      const vh = new Headers(v.headers);
      DROP.forEach(k => vh.delete(k));
      vh.set("content-type", "video/mp4");
      vh.delete("content-disposition");
      return new Response(v.body, {status: v.status, headers: vh});
    }
    const target = live ? LIVE : SITE + (url.pathname === "/" ? "/" : url.pathname) + url.search;
    const r = await fetch(target, {cf: {cacheTtl: live ? 0 : 120, cacheEverything: !live}});
    const h = new Headers(r.headers);
    DROP.forEach(k => h.delete(k));
    if (live) {
      h.set("content-type", "application/json; charset=utf-8");
      h.set("cache-control", "no-store");
    }
    if (r.status >= 300 && r.status < 400 && h.get("location")) {   // keep redirects on this address
      h.set("location", h.get("location").replace(SITE, ""));
    }
    return new Response(r.body, {status: r.status, headers: h});
  },
};
