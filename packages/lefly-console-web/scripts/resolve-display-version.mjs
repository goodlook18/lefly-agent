import { lstatSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const VERSION_PATTERN = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;

function readOptionalFile(root, name) {
  const path = join(root, name);
  let metadata;
  try {
    metadata = lstatSync(path);
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
  if (!metadata.isFile()) throw new Error(`${name} must be a regular file`);
  return readFileSync(path, "utf8");
}

function assignment(source, field, name) {
  const rows = source.split(/\r?\n/).filter((row) => new RegExp(`^\\s*${field}\\s*=`).test(row));
  if (rows.length !== 1) throw new Error(`${name} must contain exactly one ${field}`);
  const match = rows[0].match(new RegExp(`^\\s*${field}\\s*=\\s*([^#]*?)(?:\\s*#.*)?$`));
  return match?.[1].trim() ?? "";
}

function versionFromFile(source, field, name) {
  if (assignment(source, "schema_version", name) !== "1") {
    throw new Error(`${name} has invalid schema_version`);
  }
  const match = assignment(source, field, name).match(/^"([^"]*)"$/);
  if (!match || !VERSION_PATTERN.test(match[1])) {
    throw new Error(`${name} has invalid ${field}`);
  }
  return match[1];
}

export function resolveDisplayVersion(root, packageVersion) {
  if (!VERSION_PATTERN.test(packageVersion)) {
    throw new Error("Console package version is invalid");
  }
  const privateSource = readOptionalFile(root, "private-version.toml");
  const publicSource = readOptionalFile(root, "public-release.toml");
  if (privateSource === null && publicSource === null) {
    return { version: packageVersion, channel: "PUBLIC" };
  }
  if (privateSource === null) throw new Error("private-version.toml is missing from private checkout");
  if (publicSource === null) throw new Error("public-release.toml is missing from private checkout");

  const privateVersion = versionFromFile(privateSource, "current_version", "private-version.toml");
  const releaseVersion = versionFromFile(publicSource, "release_version", "public-release.toml");
  if (privateVersion !== releaseVersion) {
    return { version: privateVersion, channel: "PRIVATE" };
  }
  if (packageVersion !== releaseVersion) {
    throw new Error("Console package version does not match the public release version");
  }
  return { version: privateVersion, channel: "PUBLIC" };
}

export function resolveDisplayVersionFromConfig(configUrl, packageVersion) {
  const root = fileURLToPath(new URL("../../", configUrl));
  return resolveDisplayVersion(root, packageVersion);
}
