import { execFileSync } from "node:child_process";
import { rmSync } from "node:fs";
import { dirname } from "node:path";
import { expect, test } from "@playwright/test";

// Phase 1 in the browser, against the scripted demo planner (tests/fakellm.py --demo)
// started by scripts/e2e.sh: chat -> docs -> approve spec -> review plan -> execute.
const ORIGIN = process.env.ORIGIN_REPO!;
const LLM = process.env.FAKE_LLM_URL!;

test.use({ viewport: { width: 1280, height: 800 } });

test("specify, review the plan, execute", async ({ page, request }) => {
  test.setTimeout(180_000);
  const node = await (await request.post("/api/nodes", { data: { name: "demo-planner", base_url: LLM } })).json();
  const repo = `${dirname(ORIGIN)}/greeter.git`;
  rmSync(repo, { recursive: true, force: true });
  execFileSync("git", ["clone", "-q", "--bare", ORIGIN, repo]);
  const r = await request.post("/api/projects", {
    data: {
      name: "greeter",
      repo_url: repo,
      executor_kind: "shell",
      role_bindings: { planner: { node_id: node.id, model: "fake-coder" } },
    },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  const p = (await r.json()) as { id: number };

  await page.goto(`/projects/${p.id}`);
  await expect(page.locator(".step.current")).toHaveText(/Specify/);
  await expect(page.getByText("Describe what you want to build")).toBeVisible();

  // Turn 1: the planner drafts the spec and asks a question.
  await page.getByPlaceholder(/Message the planner/).fill("A tiny CLI that greets people by name.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".msg-assistant").last()).toContainText("lowercase", { timeout: 30_000 });
  await expect(page.locator(".msg-files").last()).toContainText("specs/001-initial/spec.md");
  await expect(page.locator("pre.doc")).toContainText("FR-1.1");

  // Turn 2: plan, validation, acceptance test and command.
  await page.getByPlaceholder(/Message the planner/).fill("Lowercase, please.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".msg-assistant").last()).toContainText("approve the spec", { timeout: 30_000 });
  await expect(page.locator(".approve input")).toHaveValue("sh tests/acceptance/run.sh");
  await page.getByRole("button", { name: "Plan", exact: true }).click();
  await expect(page.locator("pre.doc")).toContainText("greeter/core.py");
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/spec-workspace.png` });

  // Approve -> decomposition -> plan review.
  await page.getByRole("button", { name: /Approve spec/ }).click();
  await expect(page.getByRole("heading", { name: "Review the plan" })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".proposed")).toHaveCount(2);
  await expect(page.locator(".proposed").first()).toContainText("greet() function");
  await expect(page.locator(".proposed").nth(1)).toContainText("After: T1");
  if (process.env.SHOTS_DIR) await page.screenshot({ path: `${process.env.SHOTS_DIR}/plan-review.png` });

  // Approve the plan -> tasks run.
  await page.getByRole("button", { name: /Approve plan/ }).click();
  await expect(page.locator(".step.current")).toHaveText(/Execute/);
  await expect(page.locator(".column").nth(3)).toContainText("Command-line entry point", { timeout: 60_000 });
  await expect(page.locator(".column").nth(3).locator(".task-card")).toHaveCount(2);
});
