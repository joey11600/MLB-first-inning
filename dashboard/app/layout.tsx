import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NRFI Almanac · First-Inning Intelligence",
  description:
    "MLB NRFI/YRFI prediction terminal — first-inning Poisson model, pitcher + offense blend, slate-wide ranking board.",
  icons: {
    icon: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23644a40' d='M12 2 22 12 12 22 2 12Z'/%3E%3C/svg%3E",
  },
};

// Run BEFORE React hydrates — applies the saved (or system-preferred) theme
// to the <html> element so there's no flash of wrong theme on first paint.
const themeBootstrap = `
(function () {
  try {
    var stored = localStorage.getItem('nrfi-theme');
    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var theme = stored === 'dark' || stored === 'light' ? stored : (prefersDark ? 'dark' : 'light');
    var root = document.documentElement;
    root.classList.toggle('dark', theme === 'dark');
    root.classList.toggle('light', theme === 'light');
  } catch (e) {}
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
      </head>
      <body>{children}</body>
    </html>
  );
}
