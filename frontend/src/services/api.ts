import merge from "lodash.merge";

export class ApiService {
    protected apiUrl: string;

    constructor(apiUrl: string) {
        this.apiUrl = apiUrl;
    }

    protected async fetchApi(url: string, options: RequestInit = {}): Promise<Response> {
        const mergedOptions = merge(options, {
            credentials: "include",
            headers: {
                "Content-Type": "application/json",
                referrerPolicy: "unsafe-url",
            },
        });

        const response = await fetch(url, mergedOptions);

        if (!response.ok) {
            if (response.status === 401 && !window.location.pathname.startsWith("/login")) {
                window.location.href = "/login";
            }
            console.error("response code", response.status);
            throw new Error(`API request failed with status: ${response.status}`);
        }

        return response;
    }
}
