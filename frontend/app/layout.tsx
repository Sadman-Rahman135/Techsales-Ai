import type { Metadata } from "next";
import { JetBrains_Mono, Lexend, Noto_Sans_Bengali, Source_Sans_3 } from "next/font/google";
import "./globals.css";

// design-system/MASTER.md §3: self-hosted via next/font (display: swap, no layout shift).
const lexend = Lexend({ variable: "--font-lexend", subsets: ["latin"], weight: ["500", "600"] });
const sourceSans = Source_Sans_3({ variable: "--font-source-sans", subsets: ["latin"], weight: ["400", "600"] });
const jetbrains = JetBrains_Mono({ variable: "--font-jetbrains", subsets: ["latin"], weight: ["400"] });
// Bangla is P1: available as a fallback in every stack, but not preloaded for English pages.
const notoBengali = Noto_Sans_Bengali({
  variable: "--font-noto-bengali",
  subsets: ["bengali"],
  weight: ["400", "600"],
  preload: false,
});

export const metadata: Metadata = {
  title: { default: "TechCare Assistant", template: "%s · TechCare" },
  description: "Answers from TechCare's guides and policies, with sources.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${lexend.variable} ${sourceSans.variable} ${jetbrains.variable} ${notoBengali.variable} h-full antialiased`}
    >
      <body className="flex min-h-dvh flex-col">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-4 focus:z-[80] focus:inline-flex focus:min-h-11 focus:items-center focus:rounded-control focus:bg-accent focus:px-4 focus:text-on-accent"
        >
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
