import { readFile } from "node:fs/promises";
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
} from "@firebase/rules-unit-testing";
import { doc, getDoc, setDoc } from "firebase/firestore";

const projectId = process.env.GCLOUD_PROJECT ?? "demo-cowboys-ph-poker";
let environment;

before(async () => {
  const rules = await readFile(new URL("../firestore.rules", import.meta.url), "utf8");
  environment = await initializeTestEnvironment({
    projectId,
    firestore: { rules },
  });
});

after(async () => {
  await environment?.cleanup();
});

test("clients cannot read or write private rake stats or hand audits", async () => {
  const privatePaths = [
    "clubs/club-a/player_rake_stats/player-a",
    "clubs/club-a/player_rake_stats/player-a/hands/hand-a",
    "clubs/club-a/tables/table-a/hands/hand-a",
  ];
  const clients = [
    environment.unauthenticatedContext().firestore(),
    environment.authenticatedContext("player-a").firestore(),
  ];

  for (const client of clients) {
    for (const path of privatePaths) {
      const reference = doc(client, path);
      await assertFails(getDoc(reference), `${path} should not be readable`);
      await assertFails(setDoc(reference, { client_write: true }), `${path} should not be writable`);
    }
  }
});

test("trusted Admin context can access private records", async () => {
  await environment.withSecurityRulesDisabled(async (context) => {
    const reference = doc(
      context.firestore(),
      "clubs/club-a/player_rake_stats/player-a/hands/hand-admin",
    );
    await assertSucceeds(setDoc(reference, { server_write: true }));
    const snapshot = await assertSucceeds(getDoc(reference));
    assert.deepEqual(snapshot.data(), { server_write: true });
  });
});
