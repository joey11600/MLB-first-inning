import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NRFI Terminal · First-Inning Intelligence",
  description:
    "MLB NRFI/YRFI prediction terminal — first-inning Poisson model, pitcher + offense blend, slate-wide ranking board.",
  // Phase 6 PWA: link the manifest so Chrome / Edge / Safari pick up
  // installable + theme metadata.  The 'icons' block here drives the
  // browser tab favicon; richer manifest icons are in /manifest.json.
  manifest: "/manifest.json",
  applicationName: "NRFI Terminal",
  appleWebApp: {
    // Tells iOS Safari to render the page like a native app when
    // launched from the home screen (no Safari chrome).
    capable:    true,
    title:      "NRFI",
    statusBarStyle: "black-translucent",
  },
  icons: {
    // Browser tab favicon — kept as the inline SVG so an offline
    // user still sees something in the tab without needing a fetch.
    icon: [
      { url: "/icon-192.svg", type: "image/svg+xml" },
      // Fallback inline data-URI for ancient browsers / iOS bookmarks
      // that can't fetch SVG icons -- renders an OK-ish version of
      // the diamond mark.
      { url: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%235dff9a' d='M12 2 22 12 12 22 2 12Z'/%3E%3C/svg%3E", type: "image/svg+xml" },
    ],
    apple: [
      { url: "/apple-touch-icon.svg", sizes: "180x180" },
    ],
  },
  // Help WebShare, link previews, etc. render with the right brand.
  other: {
    "msapplication-TileColor": "#07090b",
  },
};

// T2.34 Phase 6: explicit Viewport export (Next.js 14 separates this
// from `metadata`).  Pinning theme-color matches the manifest so the
// Android system bar tints to phosphor-green when the PWA is active.
// `viewport-fit: cover` lets the dashboard use the iPhone notch area
// once installed.
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f6f7" },
    { media: "(prefers-color-scheme: dark)",  color: "#07090b" },
  ],
  width:        "device-width",
  initialScale: 1,
  viewportFit:  "cover",
};

// Run BEFORE React hydrates — applies the saved (or system-preferred) theme
// to the <html> element so there's no flash of wrong theme on first paint.
const themeBootstrap = `
(function () {
  try {
    var stored = localStorage.getItem('nrfi-theme');
    // Bloomberg terminal default: dark unless user explicitly chose light
    var theme = stored === 'light' ? 'light' : 'dark';
    var root = document.documentElement;
    root.classList.toggle('dark', theme === 'dark');
    root.classList.toggle('light', theme === 'light');
  } catch (e) {}
})();
`;

// T2.34 Phase 6: register the service worker on mount.  Wrapped in
// the same inline bootstrap script so it runs before React hydrates,
// ensuring the dashboard is always served via SW once installed.
// Service workers only register over HTTPS (production); the localhost
// exception in browsers means this also works during `npm run dev`.
const swBootstrap = `
(function () {
  if (!('serviceWorker' in navigator)) return;
  // Defer until after the page has settled to avoid competing with
  // initial paint -- a service-worker-install blocking the first
  // contentful paint is a Lighthouse anti-pattern.
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(function (reg) {
        // T-V21-2026-05-06c: force an update check on every page load.
        // Browsers normally only check for new SW versions every 24h;
        // this bypasses that interval so when we ship a new sw.js the
        // user picks it up on their next visit instead of after a day.
        // Critical for evicting the stale nrfi-v2 cache that was
        // serving an old shell on alternating refreshes.
        if (reg && typeof reg.update === 'function') {
          reg.update().catch(function () {});
        }
      })
      .catch(function (err) { console.warn('[sw] registration failed:', err); });
  });
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
        <script dangerouslySetInnerHTML={{ __html: swBootstrap }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
