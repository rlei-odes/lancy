import "@/styles/globals.css";
import type { AppProps } from "next/app";
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

    // Nothing renders until the router has its query populated. This mirrored
    // router.isReady into state via an effect, which only delayed the same result
    // by a render; isReady is already reactive, so read it directly.
    const isAuthPage = router.pathname === "/login" || router.pathname.startsWith("/auth/");

    return router.isReady ? (
        <BrandingProvider>
            <MediaQueryProvider waitMs={200}>
                <ThemeProvider>
                    <DisclaimerProvider>
                        {isAuthPage ? (
                            <div className={inter.className}>
                                <Component {...pageProps} />
                            </div>
                        ) : (
                            <MessagingProvider>
                                <div className={inter.className}>
                                    <Component {...pageProps} />
                                    <DisclaimerDialog />
                                    <BackendStatus />
                                </div>
                            </MessagingProvider>
                        )}
                    </DisclaimerProvider>
                </ThemeProvider>
            </MediaQueryProvider>
        </BrandingProvider>
    ) : null;
}
