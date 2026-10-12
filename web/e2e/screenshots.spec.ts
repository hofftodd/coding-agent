import { execFileSync } from "node:child_process";
import { dirname } from "node:path";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// Stages a realistic scene and captures the README screenshots.
// Skipped unless CAPTURE_SCREENSHOTS=1 (see scripts/screenshots.sh).
test.skip(!process.env.CAPTURE_SCREENSHOTS, "screenshot capture only");
test.use({ viewport: { width: 1280, height: 800 } });

const ORIGIN = process.env.ORIGIN_REPO!;
const OUT = process.env.SHOTS_DIR ?? "../docs/screenshots";

async function project(request: APIRequestContext, name: string) {
  // One bare repo per project, copied from the seeded origin, so the URLs read naturally.
  const repo = `${dirname(ORIGIN)}/${name}.git`;
  if (repo !== ORIGIN) execFileSync("git", ["clone", "-q", "--bare", ORIGIN, repo]);
  const r = await request.post("/api/projects", {
    data: { name, repo_url: repo, executor_kind: "shell", test_cmd: "test -f README.md", skip_spec: true },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return ((await r.json()) as { id: number }).id;
}

async function task(request: APIRequestContext, pid: number, data: Record<string, unknown>) {
  const r = await request.post(`/api/projects/${pid}/tasks`, { data });
  expect(r.ok(), await r.text()).toBeTruthy();
  return ((await r.json()) as { id: number }).id;
}

async function state(request: APIRequestContext, id: number) {
  const t = (await (await request.get(`/api/tasks/${id}`)).json()) as { state: string; reason: string };
  return t.state === "failed" ? `failed: ${t.reason}` : t.state;
}

const shot = (page: Page, name: string) => page.screenshot({ path: `${OUT}/${name}.png` });

// A shell script that looks like an agent working through a task.
const agent = (steps: string[], tail = "") =>
  steps.map((s) => `echo '${s}'; sleep 0.4`).join("\n") + (tail ? `\n${tail}` : "");

test("capture README screenshots", async ({ page, request }) => {
  test.setTimeout(240_000);

  // A second, finished project for the overview page.
  const docs = await project(request, "docs-site");
  const d1 = await task(request, docs, {
    title: "Add getting-started page",
    executor_script: "mkdir -p docs && echo '# Getting started' > docs/start.md",
    validation_cmd: "test -f docs/start.md",
  });
  await expect.poll(() => state(request, d1), { timeout: 60_000 }).toBe("done");

  const pid = await project(request, "todo-api");
  const config = await task(request, pid, {
    title: "Add config loader",
    description: "Load settings from environment variables with sane defaults.",
    todos: ["Create src/config.py", "Read PORT and DATABASE_URL", "Default PORT to 8080"],
    allowed_globs: ["src/config.py"],
    executor_script: agent(
      ["> reading src/app.py", "> creating src/config.py", "> running validation"],
      "cat > src/config.py <<'EOF'\nimport os\n\nPORT = int(os.environ.get(\"PORT\", \"8080\"))\nDATABASE_URL = os.environ.get(\"DATABASE_URL\", \"sqlite:///todo.db\")\nEOF",
    ),
    validation_cmd: "grep -q PORT src/config.py",
  });
  const tests = await task(request, pid, {
    title: "Write integration tests",
    allowed_globs: ["tests/*"],
    executor_script: agent(["> creating tests/test_api.py"], "mkdir -p tests && echo 'def test_list(): ...' > tests/test_api.py"),
    validation_cmd: "python3 -m pytest -q tests/",
  });
  const cli = await task(request, pid, {
    title: "Parse CLI arguments",
    description: "Add an argparse entry point that starts the server.",
    todos: ["Create src/cli.py", "Support --port and --reload", "Wire into pyproject scripts"],
    allowed_globs: ["src/cli.py"],
    deps: [config],
    executor_script: agent(
      [
        "> reading src/config.py",
        "> plan: argparse parser with --port/--reload, call app.run()",
        "> creating src/cli.py",
      ],
      "cat > src/cli.py <<'EOF'\nimport argparse\n\nfrom src import config\n\n\ndef main() -> None:\n    p = argparse.ArgumentParser(prog=\"todo-api\")\n    p.add_argument(\"--port\", type=int, default=config.PORT)\nEOF\n" +
        "i=0; while true; do i=$((i+1)); echo \"> running tests (attempt $i): 3 passed, 1 failed - test_reload_flag\"; sleep 1.5; done",
    ),
    validation_cmd: "python3 -m pytest -q tests/test_cli.py",
  });
  await task(request, pid, {
    title: "Add --verbose flag",
    allowed_globs: ["src/cli.py"],
    deps: [cli],
    executor_script: "true",
    validation_cmd: "grep -q verbose src/cli.py",
  });

  await expect.poll(() => state(request, config), { timeout: 60_000 }).toBe("done");
  await expect.poll(() => state(request, tests), { timeout: 60_000 }).toMatch(/^failed/);
  await expect.poll(() => state(request, cli), { timeout: 60_000 }).toBe("running");

  // 1. Projects overview
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "todo-api" })).toBeVisible();
  await shot(page, "projects");

  // 2. Project board with a running task
  await page.goto(`/projects/${pid}`);
  await expect(page.locator(".column").nth(1)).toContainText("Parse CLI arguments");
  await page.waitForTimeout(3000);
  await shot(page, "project-board");

  // 3. Live log of the running task
  await page.goto(`/tasks/${cli}`);
  await expect(page.locator(".event-log")).toContainText("attempt 3", { timeout: 30_000 });
  await shot(page, "task-live-log");

  // 4. Diff of a finished task
  await page.goto(`/tasks/${config}`);
  await page.getByRole("button", { name: "Diff" }).click();
  await expect(page.locator(".diff")).toContainText("DATABASE_URL");
  await shot(page, "task-diff");

  // 5. The big red button: stop the looping task
  await page.goto(`/tasks/${cli}`);
  await page.getByRole("button", { name: "STOP", exact: true }).click();
  await expect(page.locator("h1 .badge")).toHaveText(/stopped by user/i, { timeout: 10_000 });
  await expect(page.locator(".event-log")).toContainText("run killed");
  await shot(page, "task-stopped");

  // 6. E-stop
  const long = await task(request, docs, {
    title: "Generate API reference",
    executor_script: "while true; do echo '> generating...'; sleep 1; done",
    validation_cmd: "true",
  });
  await expect.poll(() => state(request, long), { timeout: 60_000 }).toBe("running");
  await page.goto(`/projects/${docs}`);
  await page.getByRole("button", { name: "E-STOP ALL" }).click();
  await expect(page.locator(".estop-banner")).toBeVisible();
  await expect(page.locator(".column").nth(2)).toContainText("Generate API reference", { timeout: 10_000 });
  await shot(page, "estop");
  await page.getByRole("button", { name: "Clear E-stop" }).click();
});
