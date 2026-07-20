import type { Metadata } from "next";
import "../../../packages/design-tokens/tokens.css";
import "./globals.css";
import Nav from "../components/Nav";

export const metadata: Metadata = {
  title: "Stone Techno Companion",
  description: "Stage 3 Next.js front end scaffold (docs/roadmap.md section 3.3).",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main>{children}</main>
      </body>
    </html>
  );
}
