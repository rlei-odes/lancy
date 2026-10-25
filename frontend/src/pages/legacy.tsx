"use client";

import Head from "next/head";
import Home from "@/components/template/home";
import { config } from "@/config";

/** The classic chat, kept at /legacy while the new one becomes the default. */
export default function Legacy() {
    return (
        <>
            <Head><title>{config.app.name}</title></Head>
            <Home />
        </>
    );
}
