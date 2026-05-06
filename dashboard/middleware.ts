import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// T-V21-2026-05-06: Edge middleware strips `?date=` from "/" before
// Next.js even sees the request.  Belt-and-suspenders against the
// stuck-URL bug — page.tsx already redirects, but middleware fires
// earlier in the request lifecycle so even cron-pinged URLs and CDN
// edge nodes see the canonical clean URL.
//
// Also sets `Clear-Site-Data: "cache"` on the redirect response: any
// browser that follows the 307 will simultaneously evict its cached
// copies of the dirty URL, which is the missing piece that keeps
// Chrome's BFCache from serving stale HTML on the next "back" press.
export function middleware(req: NextRequest) {
  const url = req.nextUrl;
  if (url.pathname === "/" && url.searchParams.has("date")) {
    const clean = url.clone();
    clean.searchParams.delete("date");
    const res = NextResponse.redirect(clean, 307);
    res.headers.set("Clear-Site-Data", '"cache"');
    res.headers.set(
      "Cache-Control",
      "private, no-cache, no-store, max-age=0, must-revalidate",
    );
    return res;
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/"],
};
