"use client";

import Head from "next/head";
import { NextChat } from "@/components/next/next-chat";
import { config } from "@/config";

export default function Index() {
    return (
        <>
            <Head>
                <title>{config.app.name}</title>
            </Head>
            <NextChat />
        </>
    );
}
