const LOCAL_NO_PROXY = ["127.0.0.1", "localhost"];

function appendLocalNoProxy(value: string | undefined): string {
  const entries = (value ?? "").split(",").map((entry) => entry.trim()).filter(Boolean);
  for (const host of LOCAL_NO_PROXY) {
    if (!entries.includes(host)) entries.push(host);
  }
  return entries.join(",");
}

export function withLocalNoProxy(environment: Record<string, string | undefined>) {
  return {
    ...environment,
    NO_PROXY: appendLocalNoProxy(environment.NO_PROXY),
    no_proxy: appendLocalNoProxy(environment.no_proxy),
  };
}

export function chromiumLaunchOptions(executablePath: string | undefined) {
  return executablePath ? { executablePath } : undefined;
}
