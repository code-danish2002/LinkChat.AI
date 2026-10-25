import type { Metadata, Viewport } from "next";

import { AuthProvider } from "@/components/auth/AuthProvider";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "LinkChat", template: "%s · LinkChat" },
  description: "Paste any link and chat with its content. Open-source models with automatic failover.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
    { media: "(prefers-color-scheme: dark)", color: "#020617" },
  ],
};

const themeScript = `(function(){try{var d=window.matchMedia('(prefers-color-scheme: dark)').matches;var s=localStorage.getItem('theme');if(s==='dark'||(!s&&d))document.documentElement.classList.add('dark');}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="font-sans">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
