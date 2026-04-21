"use client";

import Head from "next/head";
import Home from "@/components/template/home";
import { config } from "@/config";

export default function Index() {
    return (
        <>
            <Head><title>{config.app.name}</title></Head>
            <Home />
        </>
    );
}
