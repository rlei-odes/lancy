import "@/styles/globals.css";
import type { AppProps } from "next/app";
import { useEffect, useState } from "react";
import { Translation } from "@/lib/lang/i18n";
import { MessagingProvider } from "@/hooks/useMessaging";
import { useRouter } from "next/router";
import { ThemeProvider } from "@/hooks/useTheme";
import { MediaQueryProvider } from "@/hooks/useMediaQuery";
import { Inter } from "next/font/google";
import { BrandingProvider } from "@/hooks/useBranding";
import { DisclaimerProvider } from "@/hooks/useDisclaimer";
import { DisclaimerDialog } from "@/components/sections/dialogs/disclaimer-dialog";
import { BackendStatus } from "@/components/sections/backend-status";

Translation.init();
const inter = Inter({ subsets: ["latin"] });

export default function App({ Component, pageProps }: AppProps) {
    const router = useRouter();
    const [render, setRender] = useState(false);

    useEffect(() => {
        if (!router.isReady) return;
        setRender(true);
    }, [router.isReady]);

    return render ? (
        <BrandingProvider>
            <MediaQueryProvider waitMs={200}>
                <ThemeProvider>
                    <DisclaimerProvider>
                        <MessagingProvider>
                            <div className={inter.className}>
                                <Component {...pageProps} />
                                <DisclaimerDialog />
                                {router.pathname !== "/login" && <BackendStatus />}
                            </div>
                        </MessagingProvider>
                    </DisclaimerProvider>
                </ThemeProvider>
            </MediaQueryProvider>
        </BrandingProvider>
    ) : null;
}
