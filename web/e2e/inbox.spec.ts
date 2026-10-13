import { expect, test, type APIRequestContext } from "@playwright/test";

// Checkpoints in the browser: a merge waiting for approval, and a stuck task.
const ORIGIN = process.env.ORIGIN_REPO!;
const SHOTS = process.env.SHOTS_DIR;

test.use({ viewport: { width: 1280, height: 800 } });

async function project(request: APIRequestContext, name: string, extra: Record<string, unknown>) {
  const r = await request.post("/api/projects", {
    data: { name, repo_url: ORIGIN, executor_kind: "shell", skip_spec: true, ...extra },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return (await r.json()) as { id: number };
}

async function task(request: APIRequestContext, pid: number, data: Record<string, unknown>) {
  const r = await request.post(`/api/projects/${pid}/tasks`, { data: { validation_cmd: "true", ...data } });
  expect(r.ok(), await r.text()).toBeTruthy();
  return (await r.json()) as { id: number };
}

test("approve a merge and retry a stuck task from the inbox", async ({ page, request }) => {
  const merge = await project(request, "e2e-merge", { gates: { task_merge: true, milestone: false } });
  const t1 = await task(request, merge.id, { title: "Add CHANGELOG", executor_script: "echo '# Changes' > CHANGELOG.md" });

  const stuck = await project(request, "e2e-stuck", { watchdog: { max_retries: 1 }, gates: { milestone: false } });
  const t2 = await task(request, stuck.id, {
    title: "Flaky migration",
    executor_script: "echo \"connection refused ($(head -c 6 /dev/urandom | base64 | tr -d 0-9))\"; exit 1",
  });

  // Earlier tests may have left their own checkpoints; look at ours only.
  await page.goto("/inbox");
  const mergeCard = page.locator(".gate", { hasText: "Add CHANGELOG" });
  const stuckCard = page.locator(".gate", { hasText: "Flaky migration" });
  await expect(mergeCard).toBeVisible({ timeout: 60_000 });
  await expect(stuckCard).toBeVisible({ timeout: 60_000 });
  await expect(page.locator(".topbar .count")).not.toHaveText("0");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/inbox.png` });

  // Merge: approve -> the task is merged and done.
  await mergeCard.getByRole("button", { name: "Merge" }).click();
  await expect(mergeCard).toHaveCount(0);
  await expect
    .poll(async () => (await (await request.get(`/api/tasks/${t1.id}`)).json()).state, { timeout: 30_000 })
    .toBe("done");

  // Stuck: discard.
  await expect(stuckCard).toContainText("the retry failed");
  await stuckCard.getByRole("button", { name: "Discard" }).click();
  await expect
    .poll(async () => (await (await request.get(`/api/tasks/${t2.id}`)).json()).state, { timeout: 30_000 })
    .toBe("discarded");
  await expect(stuckCard).toHaveCount(0);
  await expect(page.locator("table.runs")).toContainText("Add CHANGELOG", { timeout: 20_000 });

  // The stuck task's page shows the watchdog panel and its attempts.
  await page.goto(`/tasks/${t2.id}`);
  await expect(page.locator(".watchdog")).toContainText("Model calls");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/task-watchdog.png` });
  await page.getByRole("button", { name: "Details" }).click();
  await expect(page.locator("table.runs tbody tr")).toHaveCount(2);
});
