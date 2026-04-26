import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NRFI Terminal · First-Inning Intelligence",
  description:
    "MLB NRFI/YRFI prediction terminal — first-inning Poisson model, pitcher + offense blend, slate-wide ranking board.",
  icons: {
    icon: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%235dff9a' d='M12 2 22 12 12 22 2 12Z'/%3E%3C/svg%3E",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
