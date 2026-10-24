export interface DisplayVersion {
  version: string;
  channel: "PRIVATE" | "PUBLIC";
}

export function resolveDisplayVersion(root: string, packageVersion: string): DisplayVersion;
export function resolveDisplayVersionFromConfig(configUrl: string, packageVersion: string): DisplayVersion;
