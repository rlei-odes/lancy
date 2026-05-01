import { useEffect, useState } from "react";

export type Role = "admin" | "user";

export function useRole(): { role: Role | null; loading: boolean } {
    const [role, setRole] = useState<Role | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        fetch("/api/auth/me")
            .then((r) => r.json())
            .then((data) => setRole(data.role ?? null))
            .catch(() => setRole(null))
            .finally(() => setLoading(false));
    }, []);

    return { role, loading };
}