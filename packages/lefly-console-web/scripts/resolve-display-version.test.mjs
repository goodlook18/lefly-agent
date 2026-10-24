import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { resolveDisplayVersion } from "./resolve-display-version.mjs";

function withCheckout(files, check) {
  const root = mkdtempSync(join(tmpdir(), "lefly-display-version-"));
  try {
    for (const [name, contents] of Object.entries(files)) {
      writeFileSync(join(root, name), contents);
    }
    check(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

const privateVersion = 'schema_version = 1\ncurrent_version = "0.1.2"\n';
const publicManifest = 'schema_version = 1\nrelease_version = "0.1.1"\n';

test("private iteration displays its owner-entered version", () => {
  withCheckout({
    "private-version.toml": privateVersion,
    "public-release.toml": publicManifest,
  }, (root) => {
    assert.deepEqual(resolveDisplayVersion(root, "0.1.1"), { version: "0.1.2", channel: "PRIVATE" });
  });
});

test("aligned public candidate uses the selected private version", () => {
  withCheckout({
    "private-version.toml": privateVersion,
    "public-release.toml": 'schema_version = 1\nrelease_version = "0.1.2"\n',
  }, (root) => {
    assert.deepEqual(resolveDisplayVersion(root, "0.1.2"), { version: "0.1.2", channel: "PUBLIC" });
    assert.throws(() => resolveDisplayVersion(root, "0.1.1"), /package version.*public release/);
  });
});

test("exported public checkout uses the package version", () => {
  withCheckout({}, (root) => {
    assert.deepEqual(resolveDisplayVersion(root, "0.1.1"), { version: "0.1.1", channel: "PUBLIC" });
  });
});

test("missing or invalid private register does not fall back to public", () => {
  withCheckout({ "public-release.toml": publicManifest }, (root) => {
    assert.throws(() => resolveDisplayVersion(root, "0.1.1"), /private-version.toml is missing/);
  });
  withCheckout({
    "private-version.toml": 'schema_version = 1\ncurrent_version = "bad"\n',
    "public-release.toml": publicManifest,
  }, (root) => {
    assert.throws(() => resolveDisplayVersion(root, "0.1.1"), /invalid current_version/);
  });
});
